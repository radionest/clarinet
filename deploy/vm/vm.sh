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

# Derived paths (per-VM disk/seed are computed inside _boot_vm from $VM_NAME,
# so the topology commands can override VM_NAME per VM).
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

    _boot_vm
    log "Connect with: $0 ssh"
}

# Boot a single VM described by the CURRENT $VM_NAME / VM_RAM / VM_VCPUS /
# VM_DISK_SIZE. Disk + seed paths are recomputed locally from $VM_NAME (the
# top-level DISK_PATH/SEED_ISO globals were fixed at load time and don't track
# per-VM overrides — the topology commands rely on this re-derivation).
_boot_vm() {
    local disk="${DISKS_DIR}/${VM_NAME}.qcow2"
    local seed="${DISKS_DIR}/${VM_NAME}-seed.iso"

    # Clean up stale files from a previous failed create (libvirt's
    # dynamic_ownership may have chowned them to libvirt-qemu:kvm)
    rm -f "$disk" "$seed"

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
    qemu-img create -f qcow2 -b "$base_image" -F qcow2 "$disk" "${VM_DISK_SIZE}G"

    # Render cloud-init (SSH key into user-data, hostname into meta-data).
    # Hostnames can't contain '_' (RFC 1123), so sanitise for local-hostname /
    # instance-id while VM_NAME itself may carry the topology name verbatim.
    local rendered_user_data rendered_meta_data ssh_key
    rendered_user_data="$(mktemp)"
    rendered_meta_data="$(mktemp)"
    ssh_key="$(get_ssh_pubkey)"
    sed "s|__SSH_PUBLIC_KEY__|${ssh_key}|g" "$user_data_template" > "$rendered_user_data"
    sed "s|__HOSTNAME__|${VM_NAME//_/-}|g" "$SCRIPT_DIR/cloud-init/meta-data.yaml" > "$rendered_meta_data"

    # Create cloud-init ISO
    log "Creating cloud-init seed ISO..."
    cloud-localds "$seed" "$rendered_user_data" "$rendered_meta_data"
    rm -f "$rendered_user_data" "$rendered_meta_data"

    # Grant libvirt-qemu access to disk files (backing image needs read,
    # overlay needs read-write). Prefer ACL; fall back to chmod.
    if command -v setfacl &>/dev/null; then
        setfacl -m u:libvirt-qemu:rw "$disk" 2>/dev/null || true
        setfacl -m u:libvirt-qemu:r "$base_image" 2>/dev/null || true
        setfacl -m u:libvirt-qemu:r "$seed" 2>/dev/null || true
    else
        chmod o+rw "$disk"
        chmod o+r "$base_image" "$seed"
    fi

    # Launch VM (seclabel=none disables AppArmor confinement — its profile
    # generator doesn't include qcow2 backing files in non-standard paths)
    log "Creating VM: $VM_NAME (${VM_RAM}MB RAM, ${VM_VCPUS} vCPUs)..."
    virt-install \
        --name "$VM_NAME" \
        --ram "$VM_RAM" \
        --vcpus "$VM_VCPUS" \
        --disk "path=$disk,format=qcow2" \
        --disk "path=$seed,device=cdrom" \
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
    log "VM '$VM_NAME' is ready!"
}

cmd_destroy() {
    if virsh domstate "$VM_NAME" &>/dev/null; then
        log "Destroying VM '$VM_NAME'..."
        virsh destroy "$VM_NAME" 2>/dev/null || true
        virsh undefine "$VM_NAME" --remove-all-storage 2>/dev/null || true
    else
        warn "VM '$VM_NAME' does not exist."
    fi

    # Clean up the seed ISO (the disk overlay goes with --remove-all-storage),
    # even if the VM was already gone. Path is derived from the current VM_NAME.
    rm -f "${DISKS_DIR}/${VM_NAME}-seed.iso"
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

provision_quarto_fixtures() {
    # Test-VM only: demo report templates + a downstream-style .env.example.
    # Kept out of install-clarinet.sh so production bundles stay untouched.
    # The API/worker restart picks up the templates (registry is built at
    # startup) and the new venv extras.
    local ssh_target="$1"
    shift
    local scp_opts=("$@")
    local fixtures_dir="$DEPLOY_DIR/test/fixtures/quarto"

    log "Provisioning Quarto e2e fixtures..."
    ssh_vm "rm -rf /tmp/clarinet-deploy/quarto-fixtures"
    scp "${scp_opts[@]}" -r "$fixtures_dir" "${ssh_target}:/tmp/clarinet-deploy/quarto-fixtures"
    ssh_vm "sudo install -d -o clarinet -g clarinet /opt/clarinet/review && \
        sudo install -o clarinet -g clarinet -m 644 /tmp/clarinet-deploy/quarto-fixtures/review/* /opt/clarinet/review/ && \
        sudo install -o clarinet -g clarinet -m 644 /tmp/clarinet-deploy/quarto-fixtures/env.example /opt/clarinet/.env.example && \
        sudo systemctl restart clarinet-api clarinet-worker@default"
}

cmd_deploy() {
    local wheel="${1:-}"
    # Role gating for topology deploys (unset role = "all" = single-VM, unchanged).
    local role="${CLARINET_ROLE:-all}"
    local want_assets=0   # OHIF + Quarto are stand-only
    if [[ "$role" == all || "$role" == stand ]]; then
        want_assets=1
    fi

    local ip
    ip="$(_get_ip)"
    if [[ -z "$ip" ]]; then
        err "Could not determine VM IP. Is the VM running?"
        exit 1
    fi

    local scp_opts=(-o StrictHostKeyChecking=no -o "UserKnownHostsFile=${KNOWN_HOSTS_FILE}" -i "$SSH_KEY_PATH")
    local ssh_target="${VM_USER}@${ip}"

    # PACS role: no clarinet wheel/deps/OHIF/Quarto — ship only the install
    # scripts and run the Orthanc-only path. install-clarinet.sh still takes a
    # wheel argument positionally, but install_wheel is gated off for pacs so the
    # placeholder is never read.
    if [[ "$role" == pacs ]]; then
        ssh_vm "mkdir -p /tmp/clarinet-deploy"
        log "Uploading install scripts (role: pacs)..."
        scp "${scp_opts[@]}" -r "$DEPLOY_DIR/lib"     "${ssh_target}:/tmp/clarinet-deploy/"
        scp "${scp_opts[@]}" -r "$DEPLOY_DIR/install" "${ssh_target}:/tmp/clarinet-deploy/"
        log "Running installer on VM (role: pacs)..."
        ssh_vm "sudo CLARINET_ROLE='pacs' \
            bash /tmp/clarinet-deploy/install/install-clarinet.sh \
            pacs-noop /tmp/clarinet-deploy"
        log "Deployment complete (role: pacs)!"
        return
    fi

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

    # Download dependency wheels on host (fast internet) for offline install on VM.
    # The .extras marker invalidates caches built with a different extras set
    # (a stale cache without e.g. the quarto wheels breaks --no-index install).
    local deps_dir="$PROJECT_DIR/dist/deps"
    local deps_extras="performance,quarto"
    if [[ ! -f "$deps_dir/.extras" ]] || [[ "$(cat "$deps_dir/.extras")" != "$deps_extras" ]]; then
        log "Downloading dependency wheels (extras: ${deps_extras})..."
        rm -rf "$deps_dir"
        mkdir -p "$deps_dir"
        uv tool run --python 3.12 pip download \
            -d "$deps_dir" \
            "${wheel}[${deps_extras}]"
        echo "$deps_extras" > "$deps_dir/.extras"
    else
        log "Using cached dependency wheels from $deps_dir"
    fi

    # OHIF + Quarto are stand-only assets — a worker neither serves the viewer
    # nor renders reports, so skip these (large) downloads/uploads for it.
    local ohif_tarball="" quarto_tarball=""
    if [[ $want_assets -eq 1 ]]; then
        # Download OHIF tarball on host (VM has no internet)
        local ohif_version="3.12.0"
        ohif_tarball="$PROJECT_DIR/dist/ohif-app-${ohif_version}.tgz"
        if [[ ! -f "$ohif_tarball" ]]; then
            log "Downloading OHIF Viewer v${ohif_version}..."
            mkdir -p "$PROJECT_DIR/dist"
            curl -fsSL "https://registry.npmjs.org/@ohif/app/-/app-${ohif_version}.tgz" \
                -o "$ohif_tarball"
        else
            log "Using cached OHIF tarball: $ohif_tarball"
        fi

        # Download Quarto tarball on host (VM has no internet). Cached outside
        # dist/ — the test pipeline wipes dist/ on every run. Shipping the tarball
        # opts the deployment into Quarto (CLI + pip extra) — see install-clarinet.sh.
        # Single source of truth: settings.quarto_default_version.
        local quarto_version
        quarto_version=$(grep -oP 'quarto_default_version:\s*str\s*=\s*"\K[^"]+' \
            "$PROJECT_DIR/clarinet/settings.py" || true)
        if [[ -z "$quarto_version" ]]; then
            err "Cannot parse quarto_default_version from clarinet/settings.py"
            exit 1
        fi
        local quarto_cache_dir="${XDG_CACHE_HOME:-$HOME/.cache}/clarinet-deploy"
        quarto_tarball="${quarto_cache_dir}/quarto-${quarto_version}-linux-amd64.tar.gz"
        if [[ ! -f "$quarto_tarball" ]]; then
            log "Downloading Quarto v${quarto_version}..."
            mkdir -p "$quarto_cache_dir"
            curl -fSL --progress-bar \
                "https://github.com/quarto-dev/quarto-cli/releases/download/v${quarto_version}/quarto-${quarto_version}-linux-amd64.tar.gz" \
                -o "$quarto_tarball"
        else
            log "Using cached Quarto tarball: $quarto_tarball"
        fi
    fi

    # Create remote staging directory
    ssh_vm "mkdir -p /tmp/clarinet-deploy"

    # Copy wheel + deps + deploy scripts (+ OHIF/Quarto for stand)
    log "Uploading deployment files (role: ${role})..."
    scp "${scp_opts[@]}" "$wheel" "${ssh_target}:/tmp/clarinet-deploy/"
    scp "${scp_opts[@]}" -r "$deps_dir" "${ssh_target}:/tmp/clarinet-deploy/"
    if [[ $want_assets -eq 1 ]]; then
        scp "${scp_opts[@]}" "$ohif_tarball" "${ssh_target}:/tmp/clarinet-deploy/"
        scp "${scp_opts[@]}" "$quarto_tarball" "${ssh_target}:/tmp/clarinet-deploy/"
    fi
    scp "${scp_opts[@]}" -r "$DEPLOY_DIR/lib"     "${ssh_target}:/tmp/clarinet-deploy/"
    scp "${scp_opts[@]}" -r "$DEPLOY_DIR/install" "${ssh_target}:/tmp/clarinet-deploy/"
    scp "${scp_opts[@]}" -r "$DEPLOY_DIR/systemd" "${ssh_target}:/tmp/clarinet-deploy/"
    scp "${scp_opts[@]}" -r "$DEPLOY_DIR/nginx"   "${ssh_target}:/tmp/clarinet-deploy/"

    # Ship the downstream project (plan/ + settings.toml + review/) if configured
    local project_dir="${CLARINET_PROJECT_SOURCE_DIR:-${PROJECT_SOURCE_DIR:-}}"
    local project_bundle_env=""
    if [[ -n "$project_dir" ]]; then
        if [[ ! -d "$project_dir/plan" || ! -f "$project_dir/settings.toml" ]]; then
            err "PROJECT_SOURCE_DIR must contain plan/ and settings.toml: $project_dir"
            exit 1
        fi
        log "Uploading downstream project from $project_dir..."
        local project_items=(plan settings.toml)
        [[ -d "$project_dir/review" ]] && project_items+=(review)
        # tar instead of scp -r: keeps checkout noise (__pycache__, *.pyc)
        # out of the bundle that lands in /opt/clarinet.
        tar -C "$project_dir" --exclude='__pycache__' --exclude='*.pyc' \
            -czf - "${project_items[@]}" \
            | ssh "${scp_opts[@]}" "$ssh_target" \
                "rm -rf /tmp/clarinet-deploy/project && mkdir -p /tmp/clarinet-deploy/project && tar -xzf - -C /tmp/clarinet-deploy/project"
        project_bundle_env="CLARINET_PROJECT_BUNDLE='/tmp/clarinet-deploy/project'"
    fi

    # Run install script
    local wheel_name
    wheel_name="$(basename "$wheel")"
    log "Running installer on VM (role: ${role})..."
    ssh_vm "sudo CLARINET_ROLE='${role}' \
        CLARINET_PATH_PREFIX='${PATH_PREFIX}' \
        CLARINET_PACS_HOST='${PACS_HOST:-localhost}' \
        ${project_bundle_env} \
        bash /tmp/clarinet-deploy/install/install-clarinet.sh \
        /tmp/clarinet-deploy/${wheel_name} \
        /tmp/clarinet-deploy"

    # Quarto e2e fixtures are stand-only (they restart clarinet-api + worker@default).
    if [[ $want_assets -eq 1 ]]; then
        provision_quarto_fixtures "$ssh_target" "${scp_opts[@]}"
    fi

    log "Deployment complete (role: ${role})!"
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
    local bake_user_data bake_meta_data
    bake_user_data=$(mktemp)
    bake_meta_data=$(mktemp)
    local ssh_key
    ssh_key="$(get_ssh_pubkey)"
    sed "s|__SSH_PUBLIC_KEY__|${ssh_key}|g" \
        "$SCRIPT_DIR/cloud-init/user-data.yaml" > "$bake_user_data"
    sed "s|__HOSTNAME__|${bake_name//_/-}|g" \
        "$SCRIPT_DIR/cloud-init/meta-data.yaml" > "$bake_meta_data"

    cloud-localds "$bake_seed" "$bake_user_data" "$bake_meta_data"
    rm -f "$bake_user_data" "$bake_meta_data"

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

# --- Topology (multi-VM) ---
# A topology brings up several role-specialised VMs (stand + pacs + worker) on
# the libvirt default NAT network and wires service discovery by IP. The TOML
# schema lives in deploy/vm/topologies/<name>.toml; runtime IPs land in a lock
# file under $DATA_DIR. Selected with TOPOLOGY=<name> in the environment.

TOPO_PY="$DEPLOY_DIR/lib/topology.py"

# ssh_ip <ip> [args...] — like ssh_vm but to an explicit IP (wire/smoke read IPs
# from the lock file rather than resolving a single global VM_NAME).
ssh_ip() {
    local ip="$1"; shift
    ssh -o StrictHostKeyChecking=no \
        -o "UserKnownHostsFile=${KNOWN_HOSTS_FILE}" \
        -i "$SSH_KEY_PATH" \
        "${VM_USER}@${ip}" "$@"
}

# Resolve TOPOLOGY -> TOPO_FILE + LOCK_FILE and load the [project] block.
_topology_load() {
    require_commands python3
    : "${TOPOLOGY:?set TOPOLOGY=<name> (e.g. TOPOLOGY=nir_liver)}"
    TOPO_FILE="$(python3 "$TOPO_PY" file "$TOPOLOGY")"
    LOCK_FILE="${DATA_DIR}/topology-${TOPOLOGY}.lock.json"
    TOPO_PROJECT_NAME="$(python3 "$TOPO_PY" project "$TOPO_FILE" name "$TOPOLOGY")"
    TOPO_PATH_PREFIX="$(python3 "$TOPO_PY" project "$TOPO_FILE" path_prefix "/")"
    TOPO_SOURCE_DIR="$(python3 "$TOPO_PY" project "$TOPO_FILE" source_dir "")"
    # Env override wins over the topology file (matches cmd_deploy's precedence).
    TOPO_SOURCE_DIR="${CLARINET_PROJECT_SOURCE_DIR:-$TOPO_SOURCE_DIR}"
}

_topo_vm_name() { echo "${TOPOLOGY_VM_PREFIX}-${TOPOLOGY}-$1"; }
_topo_get() { python3 "$TOPO_PY" get "$TOPO_FILE" "$@"; }
_lock_get() { python3 "$TOPO_PY" lock-get "$LOCK_FILE" "$@"; }
# Map a role to its [vm.<key>] name so wire/smoke don't assume key == role.
_topo_key() { python3 "$TOPO_PY" key-of-role "$TOPO_FILE" "$1"; }

cmd_topology_create() {
    require_commands virsh virt-install cloud-localds qemu-img jq python3
    _topology_load
    ensure_storage_access

    local vms vm
    vms="$(python3 "$TOPO_PY" vms "$TOPO_FILE")"
    for vm in $vms; do
        VM_NAME="$(_topo_vm_name "$vm")"
        VM_RAM="$(_topo_get "$vm" ram)"
        VM_VCPUS="$(_topo_get "$vm" vcpus)"
        VM_DISK_SIZE="$(_topo_get "$vm" disk_size)"
        if virsh domstate "$VM_NAME" &>/dev/null; then
            log "VM '$VM_NAME' already exists — skipping create"
        else
            log "Creating topology VM '$VM_NAME' (role: $(_topo_get "$vm" role))..."
            _boot_vm
        fi
    done

    _topology_write_lock "$vms"
    log "Topology '$TOPOLOGY' created. Lock: $LOCK_FILE"
}

# Collect each VM's role + name + current IP into the lock JSON (validated and
# normalised by topology.py lock-write).
_topology_write_lock() {
    local vms="$1" vm role ip entries=""
    mkdir -p "$DATA_DIR"
    for vm in $vms; do
        VM_NAME="$(_topo_vm_name "$vm")"
        role="$(_topo_get "$vm" role)"
        ip="$(_get_ip)"
        # _get_ip masks a failed lookup (grep in a command substitution), so an
        # un-addressed VM yields "". Fail loudly here instead of persisting
        # "ip":"" and surfacing a misleading error later in wire.
        if [[ -z "$ip" ]]; then
            err "No IP for VM '$VM_NAME' (is it running?). Topology lock not written."
            exit 1
        fi
        [[ -n "$entries" ]] && entries+=","
        entries+="\"${vm}\":{\"role\":\"${role}\",\"vm_name\":\"${VM_NAME}\",\"ip\":\"${ip}\"}"
    done
    local doc
    doc="{\"topology\":\"${TOPOLOGY}\",\"project\":{\"name\":\"${TOPO_PROJECT_NAME}\",\"path_prefix\":\"${TOPO_PATH_PREFIX}\",\"source_dir\":\"${TOPO_SOURCE_DIR}\"},\"vms\":{${entries}}}"
    python3 "$TOPO_PY" lock-write "$LOCK_FILE" "$doc"
}

cmd_topology_deploy() {
    require_commands virsh python3 uv
    _topology_load

    # Point the stand/worker DICOM client at the topology's pacs VM (from the
    # lock), not vm.conf's external-Orthanc PACS_HOST. wire re-asserts this, but
    # this keeps topology-deploy correct when run on its own.
    local pacs_ip
    pacs_ip="$(_lock_get "$(_topo_key pacs)" ip)"
    [[ -n "$pacs_ip" ]] && PACS_HOST="$pacs_ip"

    local vms vm role
    vms="$(python3 "$TOPO_PY" vms "$TOPO_FILE")"
    for vm in $vms; do
        VM_NAME="$(_topo_vm_name "$vm")"
        role="$(_topo_get "$vm" role)"
        log "Deploying '$VM_NAME' (role: ${role})..."

        export CLARINET_ROLE="$role"
        # stand + worker share the project so their pipeline_task_namespace
        # (derived from project_name) matches; pacs gets no project bundle.
        # Queues are NOT plumbed here — topology-wire enables them from the TOML.
        if [[ "$role" == stand || "$role" == worker ]]; then
            export CLARINET_PROJECT_SOURCE_DIR="$TOPO_SOURCE_DIR"
        else
            export CLARINET_PROJECT_SOURCE_DIR=""
        fi
        PATH_PREFIX="$TOPO_PATH_PREFIX"

        cmd_deploy
    done
    unset CLARINET_ROLE CLARINET_PROJECT_SOURCE_DIR
    log "Topology '$TOPOLOGY' deployed."
}

cmd_topology_wire() {
    require_commands python3
    _topology_load

    # Resolve VMs by role (not by hardcoded table key) so a topology whose vm
    # key differs from its role still wires correctly.
    local stand_key pacs_key worker_key stand_ip pacs_ip worker_ip prefix
    stand_key="$(_topo_key stand)"
    pacs_key="$(_topo_key pacs)"
    worker_key="$(_topo_key worker)"
    stand_ip="$(_lock_get "$stand_key" ip)"
    pacs_ip="$(_lock_get "$pacs_key" ip)"
    worker_ip="$(_lock_get "$worker_key" ip)"
    prefix="$TOPO_PATH_PREFIX"
    if [[ -z "$stand_ip" || -z "$pacs_ip" || -z "$worker_ip" ]]; then
        err "Lock file missing IPs (run topology-create first): $LOCK_FILE"
        exit 1
    fi

    local vmset="$DEPLOY_DIR/lib/vm-setting.sh"
    local vmwrite="$DEPLOY_DIR/lib/vm-setting-write.sh"

    # stand: point its DICOM client at the PACS VM
    log "Wiring stand ($stand_ip) -> pacs ($pacs_ip)..."
    bash "$vmwrite" "$stand_ip" "pacs_host=$pacs_ip" "pacs_port=4242"

    # NFS: stand exports the storage root, worker mounts it. uid/gid align
    # because the clarinet user is baked into the golden image, and the worker
    # writes as clarinet (not root) — so the default root_squash is sufficient,
    # no need to weaken the export with no_root_squash.
    log "Configuring NFS export on stand..."
    ssh_ip "$stand_ip" "sudo apt-get install -y -qq nfs-kernel-server >/dev/null && \
        sudo mkdir -p /var/lib/clarinet/data && \
        ( grep -qs '^/var/lib/clarinet/data ' /etc/exports || \
          echo '/var/lib/clarinet/data ${TOPOLOGY_SUBNET}(rw,sync,no_subtree_check)' | sudo tee -a /etc/exports >/dev/null ) && \
        sudo exportfs -ra && sudo systemctl enable --now nfs-server"

    # worker: copy the shared secrets off the stand, then write the overlay.
    # secret_key is intentionally NOT copied — the worker authenticates to the
    # API via effective_service_token (derived from admin_password), and
    # secret_key only signs FastAPI-Users reset/verify tokens the worker never
    # issues. admin_password + anon_uid_salt are the shared secrets that matter.
    log "Wiring worker ($worker_ip)..."
    local rabbit_user rabbit_pass admin_pass anon_salt
    rabbit_user="$(bash "$vmset" "$stand_ip" rabbitmq_login)"
    rabbit_pass="$(bash "$vmset" "$stand_ip" rabbitmq_password)"
    admin_pass="$(bash "$vmset" "$stand_ip" admin_password)"
    # Shared so stand + worker derive identical per-study anonymised IDs/paths on
    # the NFS share (only diverges when anon_per_study_patient_id is enabled).
    anon_salt="$(bash "$vmset" "$stand_ip" anon_uid_salt)"
    bash "$vmwrite" "$worker_ip" \
        "api_base_url=https://${stand_ip}${prefix}api" \
        "api_verify_ssl=false" \
        "rabbitmq_host=$stand_ip" \
        "rabbitmq_login=$rabbit_user" \
        "rabbitmq_password=$rabbit_pass" \
        "pacs_host=$pacs_ip" \
        "pacs_port=4242" \
        "dicom_retrieve_mode=c-get" \
        "admin_password=$admin_pass" \
        "anon_uid_salt=$anon_salt"

    log "Mounting NFS share on worker..."
    ssh_ip "$worker_ip" "sudo apt-get install -y -qq nfs-common >/dev/null && \
        sudo mkdir -p /var/lib/clarinet/data && \
        ( grep -qs '^${stand_ip}:/var/lib/clarinet/data ' /etc/fstab || \
          echo '${stand_ip}:/var/lib/clarinet/data /var/lib/clarinet/data nfs defaults,_netdev 0 0' | sudo tee -a /etc/fstab >/dev/null ) && \
        sudo mount -a"

    # pacs: allow-list the worker AET as an Orthanc modality (c-get doesn't use
    # the reverse Host/Port, but Orthanc still needs the AET registered).
    log "Registering worker AET on pacs ($pacs_ip)..."
    local dicom_aet dicom_port
    dicom_aet="$(bash "$vmset" "$worker_ip" dicom_aet)"; dicom_aet="${dicom_aet:-CLARINET}"
    dicom_port="$(bash "$vmset" "$worker_ip" dicom_port)"; dicom_port="${dicom_port:-11112}"
    # orthanc:orthanc is the user Orthanc itself auto-registers once
    # setup-services.sh flips RemoteAccessAllowed (implicit auth-on, empty
    # RegisteredUsers) — a stock default on a disposable NAT VM, not a secret.
    ssh_ip "$pacs_ip" "curl -sf -u orthanc:orthanc -X PUT http://localhost:8042/modalities/clarinet \
        -H 'Content-Type: application/json' \
        -d '{\"AET\":\"${dicom_aet}\",\"Host\":\"${worker_ip}\",\"Port\":${dicom_port},\"AllowFind\":true,\"AllowGet\":true,\"AllowMove\":true,\"AllowStore\":true}'" \
        || warn "Orthanc modality registration returned non-zero (continuing)"

    # restart the stand API so it picks up the new pacs_host
    log "Restarting clarinet-api on stand..."
    ssh_ip "$stand_ip" "sudo systemctl restart clarinet-api"

    # enable + start the worker's queues (deferred from install for this reason)
    local q
    for q in $(python3 "$TOPO_PY" get "$TOPO_FILE" "$worker_key" queues); do
        log "Enabling clarinet-worker@${q} on worker..."
        ssh_ip "$worker_ip" "sudo systemctl enable --now clarinet-worker@${q} && \
            sudo systemctl restart clarinet-worker@${q}"
    done

    log "Topology '$TOPOLOGY' wired."
    log "Note: re-run topology-wire after any later topology-deploy — a bare re-deploy resets the worker's settings.custom.toml overlay."
}

cmd_topology_smoke() {
    require_commands python3
    _topology_load

    local stand_ip pacs_ip worker_ip worker_key worker_q prefix fails=0
    stand_ip="$(_lock_get "$(_topo_key stand)" ip)"
    pacs_ip="$(_lock_get "$(_topo_key pacs)" ip)"
    worker_key="$(_topo_key worker)"
    worker_ip="$(_lock_get "$worker_key" ip)"
    # Smoke the worker's first configured queue (proves broker connectivity for
    # any topology, not only one that defines a 'dicom' queue).
    worker_q="$(python3 "$TOPO_PY" get "$TOPO_FILE" "$worker_key" queues)"
    worker_q="${worker_q%% *}"
    [[ -n "$worker_q" ]] || worker_q="default"
    prefix="$TOPO_PATH_PREFIX"

    # 1. RabbitMQ listens on all interfaces (deterministic remote access)
    if ssh_ip "$stand_ip" "ss -ltn | grep -qE '(0\.0\.0\.0|\*):5672'"; then
        log "smoke OK: stand RabbitMQ listens on *:5672"
    else
        err "smoke FAIL: stand RabbitMQ not listening on all interfaces (:5672)"; fails=$((fails + 1))
    fi

    # 2. worker -> broker: the worker unit is active with no AMQP errors
    if ssh_ip "$worker_ip" "systemctl is-active --quiet clarinet-worker@${worker_q}" \
        && ssh_ip "$worker_ip" "! journalctl -u clarinet-worker@${worker_q} --since '-2 min' --no-pager | grep -qE 'Connection refused|AMQPConnectionError'"; then
        log "smoke OK: worker@${worker_q} active, no broker errors"
    else
        err "smoke FAIL: worker@${worker_q} not active or logged broker errors"; fails=$((fails + 1))
    fi

    # 3. worker -> API: the stand health endpoint is reachable from the worker
    if ssh_ip "$worker_ip" "curl -ksf https://${stand_ip}${prefix}api/health >/dev/null"; then
        log "smoke OK: worker reaches stand API health"
    else
        err "smoke FAIL: worker cannot reach https://${stand_ip}${prefix}api/health"; fails=$((fails + 1))
    fi

    # 4. stand API VM -> PACS: a pynetdicom C-ECHO association succeeds
    if ssh_ip "$stand_ip" "PACS_IP='$pacs_ip' /opt/clarinet/venv/bin/python - <<'PY'
import os, sys
from pynetdicom import AE
from pynetdicom.sop_class import Verification
ae = AE(ae_title='CLARINET')
ae.add_requested_context(Verification)
assoc = ae.associate(os.environ['PACS_IP'], 4242, ae_title='ORTHANC')
if not assoc.is_established:
    sys.exit(1)
st = assoc.send_c_echo()
assoc.release()
sys.exit(0 if st and st.Status == 0x0000 else 1)
PY
"; then
        log "smoke OK: stand C-ECHO to pacs association succeeds"
    else
        err "smoke FAIL: stand C-ECHO to pacs ($pacs_ip:4242) failed"; fails=$((fails + 1))
    fi

    # 5. NFS: a file written on the worker is visible on the stand
    if ssh_ip "$worker_ip" "touch /var/lib/clarinet/data/.nfs_smoke" \
        && ssh_ip "$stand_ip" "test -f /var/lib/clarinet/data/.nfs_smoke"; then
        log "smoke OK: NFS share visible worker -> stand"
        ssh_ip "$worker_ip" "rm -f /var/lib/clarinet/data/.nfs_smoke" || true
    else
        err "smoke FAIL: NFS share not visible across stand/worker"; fails=$((fails + 1))
    fi

    if [[ $fails -eq 0 ]]; then
        log "Topology '$TOPOLOGY' smoke: ALL CHECKS PASSED"
    else
        err "Topology '$TOPOLOGY' smoke: ${fails} check(s) FAILED"
        exit 1
    fi
}

cmd_topology_down() {
    require_commands virsh python3
    _topology_load

    local vms vm
    vms="$(python3 "$TOPO_PY" vms "$TOPO_FILE")"
    for vm in $vms; do
        VM_NAME="$(_topo_vm_name "$vm")"
        cmd_destroy
    done
    rm -f "$LOCK_FILE"
    log "Topology '$TOPOLOGY' destroyed."
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
    topology-create) cmd_topology_create ;;
    topology-deploy) cmd_topology_deploy ;;
    topology-wire)   cmd_topology_wire ;;
    topology-smoke)  cmd_topology_smoke ;;
    topology-down)   cmd_topology_down ;;
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
        echo ""
        echo "Topology (multi-VM, requires TOPOLOGY=<name>):"
        echo "  topology-create  Create all VMs of the topology + write the lock file"
        echo "  topology-deploy  Per-role deploy (stand/pacs/worker) to each VM"
        echo "  topology-wire    Write per-role settings + NFS + modality registration"
        echo "  topology-smoke   Connectivity smoke (broker, API, C-ECHO, NFS)"
        echo "  topology-down    Destroy all VMs of the topology"
        ;;
esac
