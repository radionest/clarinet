#!/usr/bin/env bash
# Clarinet VM lifecycle manager
# Usage: vm.sh <create|destroy|ssh|ip|status|deploy|reimage>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_DIR="$(cd "$DEPLOY_DIR/.." && pwd)"

# Load configuration
source "$SCRIPT_DIR/vm.conf"

# Derived paths
DISK_PATH="${DISKS_DIR}/${VM_NAME}.qcow2"
SEED_ISO="${DISKS_DIR}/${VM_NAME}-seed.iso"
RENDERED_USER_DATA="${SCRIPT_DIR}/cloud-init/user-data-rendered.yaml"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log()  { echo -e "${GREEN}[vm]${NC} $*"; }
warn() { echo -e "${YELLOW}[vm]${NC} $*"; }
err()  { echo -e "${RED}[vm]${NC} $*" >&2; }

check_deps() {
    local missing=()
    for cmd in virsh virt-install cloud-localds qemu-img jq; do
        if ! command -v "$cmd" &>/dev/null; then
            missing+=("$cmd")
        fi
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        err "Missing dependencies: ${missing[*]}"
        echo ""
        echo "Install with:"
        echo "  sudo apt install libvirt-daemon-system virtinst cloud-image-utils qemu-utils"
        exit 1
    fi
}

ensure_storage_access() {
    # libvirt-qemu needs +x (traverse) on every directory in the path to disk files.
    # On Ubuntu, ~/.local and ~/.local/share default to 0700, blocking the hypervisor.
    local home_dirs=("$HOME" "$HOME/.local" "$HOME/.local/share")
    local storage_dirs=("$DATA_DIR" "$DISKS_DIR" "$IMAGES_DIR")

    # For home directories: prefer ACL (only grants access to libvirt-qemu),
    # fall back to chmod o+x with a warning about privacy implications.
    for dir in "${home_dirs[@]}"; do
        [[ -d "$dir" ]] || continue
        local perms
        perms=$(stat -c %a "$dir")
        (( 8#$perms & 8#001 )) && continue  # o+x already set — skip

        if command -v setfacl &>/dev/null && setfacl -m u:libvirt-qemu:--x "$dir" 2>/dev/null; then
            log "Granted libvirt-qemu traverse on $dir via ACL"
        else
            warn "Adding o+x to $dir (exposes directory listing to local users)"
            chmod o+x "$dir"
        fi
    done

    # For storage directories we manage: chmod o+x is fine.
    for dir in "${storage_dirs[@]}"; do
        [[ -d "$dir" ]] || continue
        local perms
        perms=$(stat -c %a "$dir")
        if (( (8#$perms & 8#001) == 0 )); then
            log "Fixing permissions on $dir ($perms -> adding o+x)..."
            chmod o+x "$dir"
        fi
    done
}

get_ssh_pubkey() {
    local pub_key="${SSH_KEY_PATH}.pub"
    if [[ ! -f "$pub_key" ]]; then
        err "SSH public key not found: $pub_key"
        err "Generate one with: ssh-keygen -t ed25519"
        exit 1
    fi
    cat "$pub_key"
}

download_image() {
    mkdir -p "$IMAGES_DIR"
    local image_path="${IMAGES_DIR}/${IMAGE_NAME}"
    if [[ -f "$image_path" ]]; then
        log "Cloud image already cached: $image_path"
        return
    fi
    log "Downloading cloud image..."
    curl -L --progress-bar -o "$image_path" "$IMAGE_URL"
    log "Image saved to $image_path"
}

cmd_create() {
    check_deps
    ensure_storage_access

    if virsh domstate "$VM_NAME" &>/dev/null; then
        warn "VM '$VM_NAME' already exists. Use 'reimage' to recreate."
        exit 1
    fi

    download_image

    # Clean up stale files from a previous failed create (libvirt's
    # dynamic_ownership may have chowned them to libvirt-qemu:kvm)
    rm -f "$DISK_PATH" "$SEED_ISO"

    # Create disk overlay
    mkdir -p "$DISKS_DIR"
    local base_image="${IMAGES_DIR}/${IMAGE_NAME}"
    log "Creating ${VM_DISK_SIZE}G disk overlay..."
    qemu-img create -f qcow2 -b "$base_image" -F qcow2 "$DISK_PATH" "${VM_DISK_SIZE}G"

    # Render cloud-init with SSH key
    local ssh_key
    ssh_key="$(get_ssh_pubkey)"
    sed "s|__SSH_PUBLIC_KEY__|${ssh_key}|g" \
        "$SCRIPT_DIR/cloud-init/user-data.yaml" > "$RENDERED_USER_DATA"

    # Create cloud-init ISO
    log "Creating cloud-init seed ISO..."
    cloud-localds "$SEED_ISO" \
        "$RENDERED_USER_DATA" \
        "$SCRIPT_DIR/cloud-init/meta-data.yaml"

    # Grant libvirt-qemu access to disk files (backing image needs read,
    # overlay needs read-write). Prefer ACL; fall back to chmod.
    if command -v setfacl &>/dev/null; then
        setfacl -m u:libvirt-qemu:rw "$DISK_PATH" 2>/dev/null || true
        setfacl -m u:libvirt-qemu:r "${IMAGES_DIR}/${IMAGE_NAME}" 2>/dev/null || true
        setfacl -m u:libvirt-qemu:r "$SEED_ISO" 2>/dev/null || true
    else
        chmod o+rw "$DISK_PATH"
        chmod o+r "${IMAGES_DIR}/${IMAGE_NAME}" "$SEED_ISO"
    fi

    # Launch VM (seclabel=none disables AppArmor confinement — its profile
    # generator doesn't include qcow2 backing files in non-standard paths)
    log "Creating VM: $VM_NAME (${VM_RAM}MB RAM, ${VM_VCPUS} vCPUs)..."
    virt-install \
        --name "$VM_NAME" \
        --ram "$VM_RAM" \
        --vcpus "$VM_VCPUS" \
        --disk "path=$DISK_PATH,format=qcow2" \
        --disk "path=$SEED_ISO,device=cdrom" \
        --os-variant ubuntu24.04 \
        --network default \
        --graphics none \
        --console pty,target_type=serial \
        --import \
        --noautoconsole \
        --seclabel type=none \
        --channel unix,target.type=virtio,target.name=org.qemu.guest_agent.0

    log "VM '$VM_NAME' created. Waiting for cloud-init..."
    _wait_for_ssh
    log "VM is ready! Connect with: $0 ssh"
}

cmd_destroy() {
    if virsh domstate "$VM_NAME" &>/dev/null; then
        log "Destroying VM '$VM_NAME'..."
        virsh destroy "$VM_NAME" 2>/dev/null || true
        virsh undefine "$VM_NAME" --remove-all-storage 2>/dev/null || true
    else
        warn "VM '$VM_NAME' does not exist."
    fi

    # Always clean up files, even if VM was already gone
    rm -f "$SEED_ISO" "$RENDERED_USER_DATA"
    log "VM destroyed."
}

cmd_ip() {
    local ip
    ip="$(_get_ip)"
    if [[ -z "$ip" ]]; then
        err "Could not determine VM IP. Is the VM running?"
        exit 1
    fi
    echo "$ip"
}

_get_ip() {
    # Try virsh domifaddr first (works with qemu-guest-agent)
    local ip
    ip=$(virsh domifaddr "$VM_NAME" --source agent 2>/dev/null \
        | grep -oP '\d+\.\d+\.\d+\.\d+' | head -1)
    if [[ -n "$ip" && "$ip" != "127.0.0.1" ]]; then
        echo "$ip"
        return
    fi

    # Fallback to lease-based
    ip=$(virsh domifaddr "$VM_NAME" 2>/dev/null \
        | grep -oP '\d+\.\d+\.\d+\.\d+' | head -1)
    echo "$ip"
}

_wait_for_ssh() {
    local max_attempts=60
    local attempt=0

    while [[ $attempt -lt $max_attempts ]]; do
        local ip
        ip="$(_get_ip)"
        if [[ -n "$ip" ]]; then
            if ssh -o StrictHostKeyChecking=no -o ConnectTimeout=3 \
                   -o BatchMode=yes -i "$SSH_KEY_PATH" \
                   "${VM_USER}@${ip}" "cloud-init status --wait" &>/dev/null; then
                log "SSH is ready at $ip"
                return 0
            fi
        fi
        attempt=$((attempt + 1))
        sleep 5
    done

    err "Timed out waiting for SSH (${max_attempts} attempts)"
    exit 1
}

_ssh_cmd() {
    local ip
    ip="$(_get_ip)"
    if [[ -z "$ip" ]]; then
        err "Could not determine VM IP."
        exit 1
    fi
    ssh -o StrictHostKeyChecking=no -i "$SSH_KEY_PATH" "${VM_USER}@${ip}" "$@"
}

cmd_ssh() {
    _ssh_cmd "${@}"
}

cmd_status() {
    if ! virsh domstate "$VM_NAME" &>/dev/null; then
        echo "not found"
        return
    fi
    virsh domstate "$VM_NAME"
}

_download_latest_wheel() {
    local repo="${CLARINET_RELEASE_REPO:-radionest/clarinet}"
    local download_dir="$PROJECT_DIR/dist"
    mkdir -p "$download_dir"

    log "Fetching latest release from github.com/${repo}..." >&2

    local auth_header=""
    if [[ -n "${GITHUB_TOKEN:-}" ]]; then
        auth_header="Authorization: Bearer ${GITHUB_TOKEN}"
    fi

    local wheel_urls
    wheel_urls=$(curl -fsSL ${auth_header:+-H "$auth_header"} \
        "https://api.github.com/repos/${repo}/releases/latest" \
        | jq -r '.assets[]?.browser_download_url // empty | select(endswith(".whl"))')

    if [[ -z "$wheel_urls" ]]; then
        err "No .whl asset found in latest GitHub release for ${repo}"
        err "Make sure a release exists with a wheel attached"
        exit 1
    fi

    local wheel_url
    wheel_url=$(head -1 <<< "$wheel_urls")

    local count
    count=$(wc -l <<< "$wheel_urls")
    if [[ "$count" -gt 1 ]]; then
        warn "Multiple .whl assets found ($count), using: $(basename "$wheel_url")" >&2
    fi

    local wheel_name wheel_path
    wheel_name="$(basename "$wheel_url")"
    wheel_path="${download_dir}/${wheel_name}"

    if [[ -f "$wheel_path" ]]; then
        log "Wheel already cached: $wheel_name" >&2
    else
        log "Downloading $wheel_name..." >&2
        curl -fSL --progress-bar ${auth_header:+-H "$auth_header"} \
            -o "$wheel_path" "$wheel_url"
    fi

    echo "$wheel_path"
}

cmd_deploy() {
    local wheel="${1:-}"

    local ip
    ip="$(_get_ip)"
    if [[ -z "$ip" ]]; then
        err "Could not determine VM IP. Is the VM running?"
        exit 1
    fi

    local scp_opts="-o StrictHostKeyChecking=no -i $SSH_KEY_PATH"
    local ssh_target="${VM_USER}@${ip}"

    if [[ -n "$wheel" ]]; then
        # Use provided local wheel
        if [[ ! -f "$wheel" ]]; then
            err "Wheel not found: $wheel"
            exit 1
        fi
        wheel="$(realpath "$wheel")"
        log "Using local wheel: $(basename "$wheel")"
    else
        # Download latest wheel from GitHub releases
        wheel="$(_download_latest_wheel)"
        log "Using wheel: $(basename "$wheel")"
    fi

    # Create remote staging directory
    _ssh_cmd "mkdir -p /tmp/clarinet-deploy"

    # Copy wheel + deploy scripts
    log "Uploading deployment files..."
    scp $scp_opts "$wheel" "${ssh_target}:/tmp/clarinet-deploy/"
    scp $scp_opts -r "$DEPLOY_DIR/install" "${ssh_target}:/tmp/clarinet-deploy/"
    scp $scp_opts -r "$DEPLOY_DIR/systemd" "${ssh_target}:/tmp/clarinet-deploy/"
    scp $scp_opts -r "$DEPLOY_DIR/nginx"   "${ssh_target}:/tmp/clarinet-deploy/"

    # Run install script
    local wheel_name
    wheel_name="$(basename "$wheel")"
    log "Running installer on VM..."
    _ssh_cmd "sudo CLARINET_PATH_PREFIX='${PATH_PREFIX}' \
        bash /tmp/clarinet-deploy/install/install-clarinet.sh \
        /tmp/clarinet-deploy/${wheel_name} \
        /tmp/clarinet-deploy"

    log "Deployment complete!"
    log "Access at: https://${ip}${PATH_PREFIX}"
}

cmd_reimage() {
    cmd_destroy
    cmd_create
}

cmd_setup() {
    check_deps
    mkdir -p "$IMAGES_DIR" "$DISKS_DIR"
    ensure_storage_access

    if virsh nodeinfo &>/dev/null; then
        log "libvirt connection: OK"
    else
        warn "Cannot connect to libvirt. Check that you are in the 'libvirt' group:"
        echo "  groups"
        echo "  sudo usermod -aG libvirt \$USER  # then re-login"
        exit 1
    fi

    log "Setup complete. Run 'make vm-create' to create a VM."
}

# --- Main ---
case "${1:-help}" in
    create)  cmd_create ;;
    destroy) cmd_destroy ;;
    setup)   cmd_setup ;;
    ssh)     shift; cmd_ssh "$@" ;;
    ip)      cmd_ip ;;
    status)  cmd_status ;;
    deploy)  shift; cmd_deploy "$@" ;;
    reimage) cmd_reimage ;;
    help|*)
        echo "Usage: $(basename "$0") <command>"
        echo ""
        echo "Commands:"
        echo "  create   Create and boot VM from cloud image"
        echo "  destroy  Stop and remove VM with all storage"
        echo "  setup    One-time host setup (permissions + libvirt check)"
        echo "  ssh      SSH into the VM (extra args passed to ssh)"
        echo "  ip       Print VM IP address"
        echo "  status   Show VM status (running/shut off/not found)"
        echo "  deploy   Deploy to VM (optional: path to .whl, else downloads latest release)"
        echo "  reimage  Destroy + recreate VM (clean slate)"
        ;;
esac
