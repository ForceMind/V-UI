#!/usr/bin/env bash
# Mutable convenience entry point. This script pins the published installer and
# complete checksum list; the raw setup.sh URL itself remains a trust boundary.
set -euo pipefail
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

setup_main() {
  set -euo pipefail
  umask 077
  local version=v0.4.9 version_seen=0 argument
  local installer_sha=5aecc3bc2c3ade67946c334091c20ade1e577c7b00c8cadcbe18edf05827312f
  local kit_sha=bae439c5db3586a04406ac5f0455938ed88c4019602c162af89535f1c6c7bde9
  local -a installer_args=()
  while [[ "$#" -gt 0 ]]; do
    argument="$1"
    case "$argument" in
      --help|-h)
        cat <<'HELP'
V-UI v0.4.9 interactive installation and global v-ui command (Linux only).
Usage: bash setup.sh [--version v0.4.9] [installer options]

Uses root or the existing sudo policy; does not install a sudoers exemption.
Supported installer options: --domain, --email, --admin, --port, --bind,
--node-port, --node-udp-port, --open-firewall, --archive-temp-dir, --cert,
--key, --health-ca, --assume-external-ports-open, --accept-terms, --upgrade.
Values are forwarded unchanged. Administrator passwords are prompted securely.
The version, interactive mode, global command and trusted kit cannot be overridden.
Only v0.4.9 has trusted hashes in this entry point. The mutable raw setup.sh URL
must itself be trusted; downloaded release assets are checked against fixed hashes.
HELP
        return 0 ;;
      --version)
        [[ "$version_seen" -eq 0 && "$#" -ge 2 ]] || { printf '%s\n' '--version requires one trusted version and cannot be repeated.' >&2; return 2; }
        version="$2"; version_seen=1; shift 2 ;;
      --version=*)
        [[ "$version_seen" -eq 0 ]] || { printf '%s\n' '--version cannot be repeated.' >&2; return 2; }
        version="${argument#*=}"; version_seen=1; shift ;;
      --domain|--email|--admin|--port|--bind|--node-port|--node-udp-port|--open-firewall|--archive-temp-dir|--cert|--key|--health-ca)
        [[ "$#" -ge 2 && "$2" != --* ]] || { printf 'Missing value for %s\n' "$argument" >&2; return 2; }
        installer_args+=("$argument" "$2"); shift 2 ;;
      --domain=*|--email=*|--admin=*|--port=*|--bind=*|--node-port=*|--node-udp-port=*|--open-firewall=*|--archive-temp-dir=*|--cert=*|--key=*|--health-ca=*)
        installer_args+=("$argument"); shift ;;
      --assume-external-ports-open|--accept-terms|--upgrade)
        installer_args+=("$argument"); shift ;;
      *)
        printf 'Unsupported or managed setup option: %s\n' "$argument" >&2
        return 2 ;;
    esac
  done
  [[ "$version" == v0.4.9 ]] || { printf 'No trusted release hashes for version: %s\n' "$version" >&2; return 2; }
  [[ "$(uname -s)" == Linux ]] || { printf '%s\n' 'V-UI setup supports Linux only.' >&2; return 1; }
  # Re-enter the already loaded function as a command string, never through the
  # original file descriptor: sudo commonly closes process-substitution /dev/fd.
  if [[ "$EUID" -ne 0 ]]; then
    command -v sudo >/dev/null 2>&1 || { printf '%s\n' 'Run as root or install sudo and rerun setup.' >&2; return 1; }
    exec sudo -- /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME=/root LANG=C.UTF-8 \
      PYTHONDONTWRITEBYTECODE=1 /bin/bash -c "$(declare -f setup_main); setup_main \"\$@\"" v-ui-setup --version "$version" ${installer_args[@]+"${installer_args[@]}"}
  fi

  # EXIT traps run after a function's local variables have left scope.
  setup_dir=''
  local allocating=0 pending_signal=0 allocation_status=0
  setup_cleanup() {
    local status="$1"
    trap - EXIT
    trap '' INT TERM
    if [[ -n "$setup_dir" ]]; then
      rm -rf -- "$setup_dir" || printf 'Warning: could not remove setup directory: %s\n' "$setup_dir" >&2 || :
    fi
    exit "$status"
  }
  setup_signal() {
    if [[ "$allocating" -eq 1 ]]; then
      if [[ "$pending_signal" -eq 0 ]]; then pending_signal="$1"; fi
    else
      exit "$1"
    fi
  }
  trap 'setup_cleanup "$?"' EXIT
  trap 'setup_signal 130' INT
  trap 'setup_signal 143' TERM

  if ! command -v curl >/dev/null 2>&1 || ! command -v sha256sum >/dev/null 2>&1; then
    if command -v apt-get >/dev/null 2>&1; then apt-get update; DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl bash coreutils
    elif command -v apk >/dev/null 2>&1; then apk add --no-cache ca-certificates curl bash coreutils
    elif command -v dnf >/dev/null 2>&1; then dnf install -y ca-certificates curl bash coreutils
    elif command -v yum >/dev/null 2>&1; then yum install -y ca-certificates curl bash coreutils
    elif command -v zypper >/dev/null 2>&1; then zypper --non-interactive install ca-certificates curl bash coreutils
    elif command -v pacman >/dev/null 2>&1; then pacman -Sy --noconfirm --needed ca-certificates curl bash coreutils
    elif command -v xbps-install >/dev/null 2>&1; then xbps-install -Sy ca-certificates curl bash coreutils
    else printf '%s\n' 'Install curl and sha256sum, then rerun setup.' >&2; exit 1
    fi
  fi
  # Own even a path printed by a failing/interrupted mktemp before handling signals.
  allocating=1
  if setup_dir="$(mktemp -d /root/vui-bootstrap.XXXXXXXX)"; then :; else allocation_status=$?; fi
  allocating=0
  if [[ "$pending_signal" -ne 0 ]]; then exit "$pending_signal"; fi
  [[ "$allocation_status" -eq 0 ]] || exit "$allocation_status"
  [[ -n "$setup_dir" ]] || { printf '%s\n' 'Could not allocate private setup directory.' >&2; exit 1; }
  curl --proto '=https' --proto-redir '=https' --tlsv1.2 --fail --location --retry 2 --connect-timeout 15 --max-time 300 \
    "https://github.com/ForceMind/V-UI/releases/download/$version/install.sh" -o "$setup_dir/install.sh"
  printf '%s  %s\n' "$installer_sha" "$setup_dir/install.sh" | sha256sum -c -
  local status=0
  if /bin/bash "$setup_dir/install.sh" --version "$version" --with-global-cli --kit-sha256 "$kit_sha" --interactive ${installer_args[@]+"${installer_args[@]}"}; then
    :
  else
    status=$?
  fi
  exit "$status"
}

# Sanitize root execution too. No caller-controlled shell startup, proxy, Python,
# temporary-directory or loader settings are carried into the managed installer.
exec /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME=/root LANG=C.UTF-8 \
  PYTHONDONTWRITEBYTECODE=1 /bin/bash -c "$(declare -f setup_main); setup_main \"\$@\"" v-ui-setup "$@"
