#!/usr/bin/env bash
set -euo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
cd "$root"
[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || { printf '请在 Linux x86_64 构建。\n' >&2; exit 1; }
for cmd in node npm go gcc git curl tar sha256sum; do
    command -v "$cmd" >/dev/null || { printf '缺少构建命令：%s\n' "$cmd" >&2; exit 1; }
done
git diff --quiet HEAD -- || { printf '请先提交源代码修改，再制作带提交号的安装包。\n' >&2; exit 1; }
version="v$(<internal/config/version)"
upstream_digest=0ddfa4702f595442e2962acd81addc768fdacaa4ff2672c971a0463dae744916
out=${1:-"$root/dist"}
mkdir -p -- "$out"
out=$(cd -- "$out" && pwd -P)
filename="3x-ui-${version}-linux-amd64.tar.gz"
[[ ! -e $out/$filename && ! -e $out/$filename.sha256 ]] || { printf '目标安装包已存在，请另选输出目录。\n' >&2; exit 1; }
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
upstream="$work/upstream.tar.gz"
if [[ -n ${XUI_UPSTREAM_ARCHIVE:-} ]]; then
    cp -- "$XUI_UPSTREAM_ARCHIVE" "$upstream"
else
    curl --fail --location --silent --show-error --retry 3 --output "$upstream" \
        https://github.com/MHSanaei/3x-ui/releases/download/v3.11.0/x-ui-linux-amd64.tar.gz
fi
printf '%s  %s\n' "$upstream_digest" "$upstream" | sha256sum --check --strict --quiet

printf '正在构建中文面板。\n'
( cd frontend; npm ci --ignore-scripts --no-fund --no-audit; npm run build )
CGO_ENABLED=1 go build -trimpath -tags 'netgo osusergo' -ldflags '-s -w -linkmode external -extldflags -static' -o "$work/x-ui" .

bundle="$work/3x-ui-zh-lite"
mkdir -p -- "$bundle"
tar -xzf "$upstream" -C "$bundle" x-ui/bin
install -m 0755 "$work/x-ui" "$bundle/x-ui/x-ui"
install -m 0755 install.sh install-online.sh update.sh x-ui.sh "$bundle"
install -m 0644 x-ui.service LICENSE README.md "$bundle"
cp -a -- licenses "$bundle/licenses"
mkdir -p "$bundle/scripts"
install -m 0644 scripts/{zh-helpers.sh,zh-ops.sh,zh-provision.sh,zh-update-request.sh,x-ui-zh-update.service,x-ui-zh-update.path} "$bundle/scripts"
printf '%s\n' "$version" > "$bundle/VERSION"
git rev-parse HEAD > "$bundle/SOURCE_COMMIT"
( cd "$bundle"; find . -type f -print0 | sort -z | xargs -0 sha256sum > "$work/SHA256SUMS" )
mv -- "$work/SHA256SUMS" "$bundle/SHA256SUMS"
tar -czf "$work/$filename" -C "$work" 3x-ui-zh-lite
install -m 0644 "$work/$filename" "$out/$filename"
( cd "$out"; sha256sum "$filename" > "$filename.sha256" )
printf '安装包和 SHA-256 已保存至 %s。\n' "$out"
