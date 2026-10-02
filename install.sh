#!/usr/bin/env bash
# Trusted local kit: sudo bash install.sh --bundle ... --sha256 ...
# Published assets: sudo bash install.sh --version v0.3.0 [installer arguments]
set -euo pipefail
umask 077

find_python() {
  local candidate
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys;raise SystemExit(0 if sys.version_info >= (3,9) else 1)' 2>/dev/null; then
      command -v "$candidate"; return 0
    fi
  done
  return 1
}
bootstrap_tools() {
  local pm=''
  for pm in apt-get dnf yum zypper pacman apk xbps-install; do
    command -v "$pm" >/dev/null 2>&1 && break
    pm=''
  done
  [[ -n "$pm" ]] || { echo 'Python 3.9+ is required to start the installer and no known package manager was found.' >&2; exit 1; }
  echo "No Python 3.9+ found. Detected package manager: $pm"
  case "$pm" in
    apt-get) apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y python3 ca-certificates curl ;;
    dnf) dnf install -y python3 ca-certificates curl ;;
    yum) yum install -y python3 ca-certificates curl ;;
    zypper) zypper --non-interactive install python3 ca-certificates curl ;;
    pacman) pacman -Sy --noconfirm --needed python ca-certificates curl ;;
    apk) apk add --no-cache python3 ca-certificates curl ;;
    xbps-install) xbps-install -Sy python3 ca-certificates curl ;;
  esac
}
PYTHON="$(find_python || true)"
if [[ -z "$PYTHON" ]]; then
  [[ "${EUID:-$(id -u)}" -eq 0 ]] || { echo 'Run with sudo so prerequisites can be installed.' >&2; exit 1; }
  bootstrap_tools
  PYTHON="$(find_python)" || { echo 'Package manager did not provide Python 3.9+.' >&2; exit 1; }
fi
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${1:-}" != '--version' ]]; then
  if [[ -f "$SCRIPT_DIR/scripts/install_system.py" ]]; then
    exec "$PYTHON" "$SCRIPT_DIR/scripts/install_system.py" "$@"
  elif [[ -f "$SCRIPT_DIR/install_system.py" ]]; then
    exec "$PYTHON" "$SCRIPT_DIR/install_system.py" "$@"
  fi
  printf '%s\n' 'Local installer is missing. Use the complete verified kit or --version vX.Y.Z.' >&2
  exit 1
fi
VERSION="${2:-}"
[[ "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'An explicit stable vX.Y.Z version is required' >&2; exit 1; }
shift 2
command -v curl >/dev/null || { echo 'Install curl first' >&2; exit 1; }
WORK="$(mktemp -d)"
trap 'rm -rf -- "$WORK"' EXIT
BASE="https://github.com/ForceMind/V-UI/releases/download/$VERSION"
for FILE in SHA256SUMS install_system.py platform_support.py firewall_support.py service_support.py vui-linux.zip; do
  curl --proto '=https' --proto-redir '=https' --tlsv1.2 --fail --location --retry 2 --connect-timeout 15 --max-time 300 \
    "$BASE/$FILE" -o "$WORK/$FILE"
done
# Only fixed names are accepted. Never execute paths named by a downloaded list.
"$PYTHON" - "$WORK" <<'PY'
import hashlib,pathlib,re,sys
root=pathlib.Path(sys.argv[1]);entries={}
for line in (root/'SHA256SUMS').read_text().splitlines():
    match=re.fullmatch(r'([a-f0-9]{64})  ([A-Za-z0-9._-]+)',line)
    if not match or match[2] in entries:raise SystemExit('Invalid checksum list')
    entries[match[2]]=match[1]
for name in ('install_system.py','platform_support.py','firewall_support.py','service_support.py','vui-linux.zip'):
    if hashlib.sha256((root/name).read_bytes()).hexdigest()!=entries.get(name):raise SystemExit('Release asset checksum mismatch')
(root/'bundle.sha').write_text(entries['vui-linux.zip'])
PY
# This checksum is from the explicitly selected official release, not a signature.
# Audit install.sh itself and the repository/release source before executing as root.
PYTHONPATH="$WORK" "$PYTHON" "$WORK/install_system.py" --bundle "$WORK/vui-linux.zip" --sha256 "$(cat "$WORK/bundle.sha")" "$@"
