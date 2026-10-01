#!/usr/bin/env bash
# Trusted local kit: sudo bash install.sh --bundle ... --sha256 ...
# Published assets: sudo bash install.sh --version v0.3.0 [installer arguments]
set -euo pipefail
umask 077
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${1:-}" != '--version' ]]; then
  if [[ -f "$SCRIPT_DIR/scripts/install_system.py" ]]; then
    exec python3 "$SCRIPT_DIR/scripts/install_system.py" "$@"
  elif [[ -f "$SCRIPT_DIR/install_system.py" ]]; then
    exec python3 "$SCRIPT_DIR/install_system.py" "$@"
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
for FILE in SHA256SUMS install_system.py vui-linux-amd64.zip; do
  curl --proto '=https' --proto-redir '=https' --tlsv1.2 --fail --location --retry 2 --connect-timeout 15 --max-time 300 \
    "$BASE/$FILE" -o "$WORK/$FILE"
done
# Only fixed names are accepted. Never execute paths named by a downloaded list.
python3 - "$WORK" <<'PY'
import hashlib,pathlib,re,sys
root=pathlib.Path(sys.argv[1]);entries={}
for line in (root/'SHA256SUMS').read_text().splitlines():
    match=re.fullmatch(r'([a-f0-9]{64})  ([A-Za-z0-9._-]+)',line)
    if not match or match[2] in entries:raise SystemExit('Invalid checksum list')
    entries[match[2]]=match[1]
for name in ('install_system.py','vui-linux-amd64.zip'):
    if hashlib.sha256((root/name).read_bytes()).hexdigest()!=entries.get(name):raise SystemExit('Release asset checksum mismatch')
(root/'bundle.sha').write_text(entries['vui-linux-amd64.zip'])
PY
# This checksum is from the explicitly selected official release, not a signature.
# Audit install.sh itself and the repository/release source before executing as root.
python3 "$WORK/install_system.py" --bundle "$WORK/vui-linux-amd64.zip" --sha256 "$(cat "$WORK/bundle.sha")" "$@"
