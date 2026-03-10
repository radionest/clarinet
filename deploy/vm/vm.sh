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
    for cmd in virsh virt-install cloud-localds qemu-img; do
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

    if virsh domstate "$VM_NAME" &>/dev/null; then
        warn "VM '$VM_NAME' already exists. Use 'reimage' to recreate."
        exit 1
    fi

    download_image

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

    # Launch VM
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
        --channel unix,target.type=virtio,target.name=org.qemu.guest_agent.0

    log "VM '$VM_NAME' created. Waiting for cloud-init..."
    _wait_for_ssh
    log "VM is ready! Connect with: $0 ssh"
}

cmd_destroy() {
    if ! virsh domstate "$VM_NAME" &>/dev/null; then
        warn "VM '$VM_NAME' does not exist."
        return
    fi

    log "Destroying VM '$VM_NAME'..."
    virsh destroy "$VM_NAME" 2>/dev/null || true
    virsh undefine "$VM_NAME" --remove-all-storage 2>/dev/null || true

    # Clean up seed ISO and rendered cloud-init
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

cmd_deploy() {
    local ip
    ip="$(_get_ip)"
    if [[ -z "$ip" ]]; then
        err "Could not determine VM IP. Is the VM running?"
        exit 1
    fi

    local scp_opts="-o StrictHostKeyChecking=no -i $SSH_KEY_PATH"
    local ssh_target="${VM_USER}@${ip}"

    # Build wheel
    log "Building Clarinet wheel..."
    (cd "$PROJECT_DIR" && uv build --wheel --quiet)

    local wheel
    wheel=$(ls -t "$PROJECT_DIR"/dist/*.whl 2>/dev/null | head -1)
    if [[ -z "$wheel" ]]; then
        err "No wheel found in dist/. Build failed?"
        exit 1
    fi
    log "Built: $(basename "$wheel")"

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

# --- Main ---
case "${1:-help}" in
    create)  cmd_create ;;
    destroy) cmd_destroy ;;
    ssh)     shift; cmd_ssh "$@" ;;
    ip)      cmd_ip ;;
    status)  cmd_status ;;
    deploy)  cmd_deploy ;;
    reimage) cmd_reimage ;;
    help|*)
        echo "Usage: $(basename "$0") <command>"
        echo ""
        echo "Commands:"
        echo "  create   Create and boot VM from cloud image"
        echo "  destroy  Stop and remove VM with all storage"
        echo "  ssh      SSH into the VM (extra args passed to ssh)"
        echo "  ip       Print VM IP address"
        echo "  status   Show VM status (running/shut off/not found)"
        echo "  deploy   Build wheel and deploy to VM"
        echo "  reimage  Destroy + recreate VM (clean slate)"
        ;;
esac
