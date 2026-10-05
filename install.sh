#!/usr/bin/env bash
# Trusted local kit: sudo bash install.sh --bundle ... --sha256 ...
# After the candidate is published: sudo bash install.sh --version v0.4.3 [installer arguments]
set -euo pipefail
umask 077

detect_target() {
  local arch libc
  arch="$(uname -m)"
  case "$arch" in
    x86_64|amd64) arch=x86_64 ;;
    aarch64|arm64) arch=aarch64 ;;
    *) echo "Unsupported CPU architecture: $arch" >&2; return 1 ;;
  esac
  if ls /lib/ld-musl-*.so.1 /usr/lib/ld-musl-*.so.1 >/dev/null 2>&1 || (command -v ldd >/dev/null 2>&1 && ldd --version 2>&1 | grep -qi musl); then
    libc=musl
  else
    libc=gnu
  fi
  printf '%s-%s\n' "$arch" "$libc"
}

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
  for pm in apt-get dnf yum zypper pacman apk xbps-install emerge; do
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
    xbps-install) xbps-install -Sy python3 ca-certificates curl tar ;;
    emerge) emerge --noconfmem --oneshot dev-lang/python net-misc/curl app-misc/ca-certificates app-arch/tar ;;
  esac
}
bootstrap_portable_python() {
  local key url sha root archive
  key="$(detect_target)" || return 1
  case "$key" in
    x86_64-gnu)
      url='https://github.com/astral-sh/python-build-standalone/releases/download/20260901/cpython-3.12.14%2B20260901-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz'
      sha='72748da13197c1fb161e3afeef20a6a385ff24f2165e6e2758e47008e7faba4c' ;;
    x86_64-musl)
      url='https://github.com/astral-sh/python-build-standalone/releases/download/20260901/cpython-3.12.14%2B20260901-x86_64-unknown-linux-musl-install_only_stripped.tar.gz'
      sha='1f37044c8cdbd74d5ee112a753c65ef209fedd169c98f3e4e748a93e27eb27a4' ;;
    aarch64-gnu)
      url='https://github.com/astral-sh/python-build-standalone/releases/download/20260901/cpython-3.12.14%2B20260901-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz'
      sha='577b4bec0793ad1ff0cbff9adbd0df078eddde38a4c41bf5d83ad381a85ee39d' ;;
    aarch64-musl)
      url='https://github.com/astral-sh/python-build-standalone/releases/download/20260901/cpython-3.12.14%2B20260901-aarch64-unknown-linux-musl-install_only_stripped.tar.gz'
      sha='a0ad6f01b9204eba573a08927097b78143c564f98bea68a41ea1e172f041da3a' ;;
    *) return 1 ;;
  esac
  command -v curl >/dev/null 2>&1 || return 1
  command -v sha256sum >/dev/null 2>&1 || return 1
  command -v tar >/dev/null 2>&1 || return 1
  root="$(mktemp -d)"
  BOOTSTRAP_RUNTIME_DIR="$root"
  archive="$root/python.tar.gz"
  curl --proto '=https' --proto-redir '=https' --tlsv1.2 --fail --location --retry 2 --connect-timeout 15 --max-time 300 "$url" -o "$archive"
  printf '%s  %s\n' "$sha" "$archive" | sha256sum -c - >/dev/null
  tar -xzf "$archive" -C "$root"
  [[ -x "$root/python/bin/python3" ]] || return 1
  printf '%s\n' "$root/python/bin/python3"
}
BOOTSTRAP_RUNTIME_DIR=''
PYTHON="$(find_python || true)"
if [[ -z "$PYTHON" ]]; then
  [[ "${EUID:-$(id -u)}" -eq 0 ]] || { echo 'Run with sudo so prerequisites can be installed.' >&2; exit 1; }
  bootstrap_tools || true
  PYTHON="$(find_python || true)"
fi
if [[ -z "$PYTHON" ]]; then
  echo 'System Python is still too old; using the fixed portable bootstrap runtime.'
  PYTHON="$(bootstrap_portable_python)" || { echo 'Could not obtain the verified portable installer runtime.' >&2; exit 1; }
fi
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cleanup_bootstrap() { [[ -n "$BOOTSTRAP_RUNTIME_DIR" ]] && rm -rf -- "$BOOTSTRAP_RUNTIME_DIR"; }
trap cleanup_bootstrap EXIT
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
cleanup_all() { rm -rf -- "$WORK"; cleanup_bootstrap; }
trap cleanup_all EXIT
BASE="https://github.com/ForceMind/V-UI/releases/download/$VERSION"
TARGET="$(detect_target)"
BUNDLE="vui-linux-$TARGET.zip"
echo "Detected release target: $TARGET"
for FILE in SHA256SUMS install_system.py platform_support.py firewall_support.py service_support.py "$BUNDLE"; do
  curl --proto '=https' --proto-redir '=https' --tlsv1.2 --fail --location --retry 2 --connect-timeout 15 --max-time 300 \
    "$BASE/$FILE" -o "$WORK/$FILE"
done
# Only fixed names are accepted. Never execute paths named by a downloaded list.
"$PYTHON" - "$WORK" "$BUNDLE" <<'PY'
import hashlib,pathlib,re,sys
root=pathlib.Path(sys.argv[1]);bundle=sys.argv[2];entries={}
if not re.fullmatch(r'vui-linux-(?:x86_64|aarch64)-(?:gnu|musl)\.zip',bundle):
    raise SystemExit('Invalid target bundle name')
for line in (root/'SHA256SUMS').read_text().splitlines():
    match=re.fullmatch(r'([a-f0-9]{64})  ([A-Za-z0-9._-]+)',line)
    if not match or match[2] in entries:raise SystemExit('Invalid checksum list')
    entries[match[2]]=match[1]
for name in ('install_system.py','platform_support.py','firewall_support.py','service_support.py',bundle):
    if hashlib.sha256((root/name).read_bytes()).hexdigest()!=entries.get(name):
        raise SystemExit('Release asset checksum mismatch: '+name)
(root/'bundle.sha').write_text(entries[bundle])
PY
# This checksum is from the explicitly selected official release, not a signature.
# Audit install.sh itself and the repository/release source before executing as root.
PYTHONPATH="$WORK" "$PYTHON" "$WORK/install_system.py" --bundle "$WORK/$BUNDLE" --sha256 "$(cat "$WORK/bundle.sha")" "$@"
