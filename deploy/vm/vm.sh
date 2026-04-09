#!/usr/bin/env bash
# Clarinet VM lifecycle manager
# Usage: vm.sh <create|destroy|ssh|ip|status|deploy|reimage|bake>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_DIR="$(cd "$DEPLOY_DIR/.." && pwd)"

# Load configuration
source "$SCRIPT_DIR/vm.conf"
source "$DEPLOY_DIR/lib/common.sh"
init_logging "vm"

# Per-worktree VM name: explicit env > auto-detect from worktree path > vm.conf default
if [[ -n "${CLARINET_VM_NAME:-}" ]]; then
    VM_NAME="$CLARINET_VM_NAME"
elif [[ "$PROJECT_DIR" == */.claude/worktrees/* ]]; then
    _wt_suffix="${PROJECT_DIR##*/.claude/worktrees/}"
    _wt_suffix="${_wt_suffix%%/*}"
    VM_NAME="clarinet-test-${_wt_suffix}"
    log "Worktree detected — using VM name: $VM_NAME"
fi

# Derived paths
DISK_PATH="${DISKS_DIR}/${VM_NAME}.qcow2"
SEED_ISO="${DISKS_DIR}/${VM_NAME}-seed.iso"
RENDERED_USER_DATA="${SCRIPT_DIR}/cloud-init/user-data-rendered.yaml"
GOLDEN_IMAGE="${IMAGES_DIR}/clarinet-golden.qcow2"

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
    require_commands virsh virt-install cloud-localds qemu-img jq
    ensure_storage_access

    if virsh domstate "$VM_NAME" &>/dev/null; then
        warn "VM '$VM_NAME' already exists. Use 'reimage' to recreate."
        exit 1
    fi

    # Clean up stale files from a previous failed create (libvirt's
    # dynamic_ownership may have chowned them to libvirt-qemu:kvm)
    rm -f "$DISK_PATH" "$SEED_ISO"

    # Select backing image: golden (pre-baked) or plain cloud image
    local base_image user_data_template
    if [[ -f "$GOLDEN_IMAGE" ]]; then
        base_image="$GOLDEN_IMAGE"
        user_data_template="$SCRIPT_DIR/cloud-init/user-data-golden.yaml"
        log "Using golden image (services pre-installed)"
    else
        download_image
        base_image="${IMAGES_DIR}/${IMAGE_NAME}"
        user_data_template="$SCRIPT_DIR/cloud-init/user-data.yaml"
        log "Golden image not found — using plain cloud image"
    fi

    # Create disk overlay
    mkdir -p "$DISKS_DIR"
    log "Creating ${VM_DISK_SIZE}G disk overlay..."
    qemu-img create -f qcow2 -b "$base_image" -F qcow2 "$DISK_PATH" "${VM_DISK_SIZE}G"

    # Render cloud-init with SSH key
    local ssh_key
    ssh_key="$(get_ssh_pubkey)"
    sed "s|__SSH_PUBLIC_KEY__|${ssh_key}|g" \
        "$user_data_template" > "$RENDERED_USER_DATA"

    # Create cloud-init ISO
    log "Creating cloud-init seed ISO..."
    cloud-localds "$SEED_ISO" \
        "$RENDERED_USER_DATA" \
        "$SCRIPT_DIR/cloud-init/meta-data.yaml"

    # Grant libvirt-qemu access to disk files (backing image needs read,
    # overlay needs read-write). Prefer ACL; fall back to chmod.
    if command -v setfacl &>/dev/null; then
        setfacl -m u:libvirt-qemu:rw "$DISK_PATH" 2>/dev/null || true
        setfacl -m u:libvirt-qemu:r "$base_image" 2>/dev/null || true
        setfacl -m u:libvirt-qemu:r "$SEED_ISO" 2>/dev/null || true
    else
        chmod o+rw "$DISK_PATH"
        chmod o+r "$base_image" "$SEED_ISO"
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
    local cleaned=0

    mkdir -p "$(dirname "$KNOWN_HOSTS_FILE")"
    touch "$KNOWN_HOSTS_FILE"

    while [[ $attempt -lt $max_attempts ]]; do
        local ip
        ip="$(_get_ip)"
        if [[ -n "$ip" ]]; then
            # Remove any stale host key for this IP from the dedicated
            # known_hosts file. libvirt often re-assigns the same address to
            # a freshly reimaged VM, which produces a new host key. OpenSSH's
            # StrictHostKeyChecking=no still refuses port forwarding when the
            # known_hosts entry conflicts, which silently breaks Stage 6 SSH
            # tunnels in `make test-all-stages`. The dedicated file keeps the
            # user's ~/.ssh/known_hosts untouched.
            if [[ $cleaned -eq 0 ]]; then
                ssh-keygen -f "$KNOWN_HOSTS_FILE" -R "$ip" &>/dev/null || true
                cleaned=1
            fi
            if ssh -o StrictHostKeyChecking=no \
                   -o "UserKnownHostsFile=${KNOWN_HOSTS_FILE}" \
                   -o ConnectTimeout=3 \
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

cmd_ssh() {
    ssh_vm "$@"
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

    local scp_opts=(-o StrictHostKeyChecking=no -o "UserKnownHostsFile=${KNOWN_HOSTS_FILE}" -i "$SSH_KEY_PATH")
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

    # Download dependency wheels on host (fast internet) for offline install on VM
    local deps_dir="$PROJECT_DIR/dist/deps"
    if [[ ! -d "$deps_dir" ]] || [[ -z "$(ls -A "$deps_dir" 2>/dev/null)" ]]; then
        log "Downloading dependency wheels..."
        mkdir -p "$deps_dir"
        uv tool run --python 3.12 pip download \
            -d "$deps_dir" \
            "${wheel}[performance]"
    else
        log "Using cached dependency wheels from $deps_dir"
    fi

    # Download OHIF tarball on host (VM has no internet)
    local ohif_version="3.12.0"
    local ohif_tarball="$PROJECT_DIR/dist/ohif-app-${ohif_version}.tgz"
    if [[ ! -f "$ohif_tarball" ]]; then
        log "Downloading OHIF Viewer v${ohif_version}..."
        mkdir -p "$PROJECT_DIR/dist"
        curl -fsSL "https://registry.npmjs.org/@ohif/app/-/app-${ohif_version}.tgz" \
            -o "$ohif_tarball"
    else
        log "Using cached OHIF tarball: $ohif_tarball"
    fi

    # Create remote staging directory
    ssh_vm "mkdir -p /tmp/clarinet-deploy"

    # Copy wheel + deps + OHIF + deploy scripts
    log "Uploading deployment files..."
    scp "${scp_opts[@]}" "$wheel" "${ssh_target}:/tmp/clarinet-deploy/"
    scp "${scp_opts[@]}" -r "$deps_dir" "${ssh_target}:/tmp/clarinet-deploy/"
    scp "${scp_opts[@]}" "$ohif_tarball" "${ssh_target}:/tmp/clarinet-deploy/"
    scp "${scp_opts[@]}" -r "$DEPLOY_DIR/lib"     "${ssh_target}:/tmp/clarinet-deploy/"
    scp "${scp_opts[@]}" -r "$DEPLOY_DIR/install" "${ssh_target}:/tmp/clarinet-deploy/"
    scp "${scp_opts[@]}" -r "$DEPLOY_DIR/systemd" "${ssh_target}:/tmp/clarinet-deploy/"
    scp "${scp_opts[@]}" -r "$DEPLOY_DIR/nginx"   "${ssh_target}:/tmp/clarinet-deploy/"

    # Run install script
    local wheel_name
    wheel_name="$(basename "$wheel")"
    log "Running installer on VM..."
    ssh_vm "sudo CLARINET_PATH_PREFIX='${PATH_PREFIX}' \
        CLARINET_PACS_HOST='${PACS_HOST:-localhost}' \
        bash /tmp/clarinet-deploy/install/install-clarinet.sh \
        /tmp/clarinet-deploy/${wheel_name} \
        /tmp/clarinet-deploy"

    log "Deployment complete!"
    log "Access at: https://${ip}${PATH_PREFIX}"
}

cmd_bake() {
    local dicom_dir="${1:-}"
    local auto_fetched_dicom=""

    if [[ -n "$dicom_dir" ]]; then
        dicom_dir="$(realpath "$dicom_dir")"
        if [[ ! -d "$dicom_dir" ]]; then
            err "DICOM directory not found: $dicom_dir"
            exit 1
        fi
        log "DICOM test images: $dicom_dir"
    elif [[ -n "${DICOM_SOURCE_URL:-}" ]]; then
        # Auto-fetch from source Orthanc
        log "No DICOM dir specified — fetching from ${DICOM_SOURCE_URL}..."
        auto_fetched_dicom="$(mktemp -d -t bake-dicom-XXXXXX)"
        if ! dicom_dir=$("$SCRIPT_DIR/fetch-test-dicom.sh" --output "$auto_fetched_dicom" \
            | tail -1) || [[ ! -d "$dicom_dir" ]]; then
            err "Failed to fetch DICOM test data"
            exit 1
        fi
    fi

    require_commands virsh virt-install cloud-localds qemu-img
    ensure_storage_access

    local bake_name="clarinet-golden-bake-$$"
    local bake_disk="${DISKS_DIR}/${bake_name}.qcow2"
    local bake_seed="${DISKS_DIR}/${bake_name}-seed.iso"

    _cleanup_bake() {
        warn "Cleaning up failed bake..."
        virsh destroy "$bake_name" 2>/dev/null || true
        virsh undefine "$bake_name" --remove-all-storage 2>/dev/null || true
        rm -f "$bake_disk" "$bake_seed"
        [[ -n "$auto_fetched_dicom" ]] && rm -rf "$auto_fetched_dicom"
    }
    trap _cleanup_bake ERR

    download_image

    log "Baking golden image..."

    # Create disk overlay for baking VM
    mkdir -p "$DISKS_DIR"
    local base_image="${IMAGES_DIR}/${IMAGE_NAME}"
    qemu-img create -f qcow2 -b "$base_image" -F qcow2 "$bake_disk" "${VM_DISK_SIZE}G"

    # Minimal cloud-init: just enough for SSH access
    local bake_user_data
    bake_user_data=$(mktemp)
    local ssh_key
    ssh_key="$(get_ssh_pubkey)"
    sed "s|__SSH_PUBLIC_KEY__|${ssh_key}|g" \
        "$SCRIPT_DIR/cloud-init/user-data.yaml" > "$bake_user_data"

    cloud-localds "$bake_seed" "$bake_user_data" "$SCRIPT_DIR/cloud-init/meta-data.yaml"
    rm -f "$bake_user_data"

    # Grant libvirt access
    if command -v setfacl &>/dev/null; then
        setfacl -m u:libvirt-qemu:rw "$bake_disk" 2>/dev/null || true
        setfacl -m u:libvirt-qemu:r "$base_image" 2>/dev/null || true
        setfacl -m u:libvirt-qemu:r "$bake_seed" 2>/dev/null || true
    else
        chmod o+rw "$bake_disk"
        chmod o+r "$base_image" "$bake_seed"
    fi

    # Launch temporary baking VM
    log "Launching baking VM: $bake_name"
    virt-install \
        --name "$bake_name" \
        --ram "$VM_RAM" \
        --vcpus "$VM_VCPUS" \
        --disk "path=$bake_disk,format=qcow2" \
        --disk "path=$bake_seed,device=cdrom" \
        --os-variant ubuntu24.04 \
        --network default \
        --graphics none \
        --console pty,target_type=serial \
        --import \
        --noautoconsole \
        --seclabel type=none \
        --channel unix,target.type=virtio,target.name=org.qemu.guest_agent.0

    # Wait for SSH (reuse existing logic with overridden VM_NAME)
    local orig_vm_name="$VM_NAME"
    VM_NAME="$bake_name"
    _wait_for_ssh
    VM_NAME="$orig_vm_name"

    # Copy and run bake script (reuse _get_ip with overridden VM_NAME for fallback)
    VM_NAME="$bake_name"
    local bake_ip
    bake_ip="$(_get_ip)"
    VM_NAME="$orig_vm_name"
    local scp_opts=(-o StrictHostKeyChecking=no -o "UserKnownHostsFile=${KNOWN_HOSTS_FILE}" -i "$SSH_KEY_PATH")
    local ssh_target="${VM_USER}@${bake_ip}"

    log "Uploading bake script..."
    ssh "${scp_opts[@]}" "$ssh_target" "mkdir -p /tmp/clarinet-deploy"
    scp "${scp_opts[@]}" -r "$DEPLOY_DIR/lib" "${ssh_target}:/tmp/clarinet-deploy/"
    scp "${scp_opts[@]}" "$SCRIPT_DIR/bake-image.sh" "${ssh_target}:/tmp/clarinet-deploy/"

    # Upload DICOM test images if provided
    local bake_dicom_args=""
    if [[ -n "$dicom_dir" ]]; then
        log "Uploading DICOM test images..."
        scp "${scp_opts[@]}" -r "$dicom_dir" "${ssh_target}:/tmp/clarinet-deploy/dicom"
        bake_dicom_args="--dicom-dir /tmp/clarinet-deploy/dicom"
    fi

    log "Running bake script (this takes several minutes)..."
    # shellcheck disable=SC2029
    ssh "${scp_opts[@]}" "$ssh_target" \
        "sudo bash /tmp/clarinet-deploy/bake-image.sh ${bake_dicom_args}"

    # Shut down baking VM
    log "Shutting down baking VM..."
    virsh shutdown "$bake_name"
    local wait=0
    while virsh domstate "$bake_name" 2>/dev/null | grep -q "running"; do
        sleep 2
        wait=$((wait + 2))
        if [[ $wait -ge 60 ]]; then
            warn "Graceful shutdown timed out, forcing..."
            virsh destroy "$bake_name" 2>/dev/null || true
            break
        fi
    done

    # Export: flatten overlay into standalone golden qcow2
    log "Exporting golden image (this may take a minute)..."
    mkdir -p "$IMAGES_DIR"
    # Remove old golden image (may be owned by libvirt-qemu from a previous bake)
    rm -f "$GOLDEN_IMAGE" 2>/dev/null || sudo rm -f "$GOLDEN_IMAGE"
    qemu-img convert -c -O qcow2 "$bake_disk" "$GOLDEN_IMAGE"

    # Grant libvirt read access to golden image
    if command -v setfacl &>/dev/null; then
        setfacl -m u:libvirt-qemu:r "$GOLDEN_IMAGE" 2>/dev/null || true
    else
        chmod o+r "$GOLDEN_IMAGE"
    fi

    # Cleanup baking VM
    virsh undefine "$bake_name" --remove-all-storage 2>/dev/null || true
    rm -f "$bake_disk" "$bake_seed"

    # Clean up auto-fetched DICOM temp dir
    if [[ -n "$auto_fetched_dicom" ]]; then
        rm -rf "$auto_fetched_dicom"
    fi

    trap - ERR

    local size
    size=$(du -h "$GOLDEN_IMAGE" | cut -f1)
    log "Golden image created: $GOLDEN_IMAGE ($size)"
    log "Future 'vm create' will use this image automatically."
}

cmd_reimage() {
    cmd_destroy
    cmd_create
}

cmd_setup() {
    require_commands virsh virt-install cloud-localds qemu-img jq
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
    name)    echo "$VM_NAME" ;;
    status)  cmd_status ;;
    deploy)  shift; cmd_deploy "$@" ;;
    reimage) cmd_reimage ;;
    bake)    shift; cmd_bake "$@" ;;
    help|*)
        echo "Usage: $(basename "$0") <command>"
        echo ""
        echo "Commands:"
        echo "  create   Create and boot VM (uses golden image if available)"
        echo "  destroy  Stop and remove VM with all storage"
        echo "  setup    One-time host setup (permissions + libvirt check)"
        echo "  ssh      SSH into the VM (extra args passed to ssh)"
        echo "  ip       Print VM IP address"
        echo "  name     Print resolved VM name"
        echo "  status   Show VM status (running/shut off/not found)"
        echo "  deploy   Deploy to VM (optional: path to .whl, else downloads latest release)"
        echo "  reimage  Destroy + recreate VM (clean slate)"
        echo "  bake     Create golden image with pre-installed packages and services"
        echo "           Optional: bake /path/to/dicoms — pre-load test DICOM images into Orthanc"
        ;;
esac
