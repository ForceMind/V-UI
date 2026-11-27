#!/usr/bin/env bash
set -euo pipefail
umask 077

die() { printf '预览安装错误：%s\n' "$*" >&2; exit 1; }

version=v3.11.0-zh.7
digest=d7665da30b50f338057e49c1c2488d185ac2beeb024e0bcd18182ee12c2b22b6
revision=d04d2352b43cf78e4d7571a3c2abb7d3e125a4c3
repo=https://raw.githubusercontent.com/ForceMind/V-UI
file="3x-ui-${version}-linux-amd64.tar.gz"

[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || die '此安装包适用于 Linux x86_64。'
for program in curl sha256sum tar; do command -v "$program" >/dev/null || die "系统缺少 $program。"; done

work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
printf '正在下载 3X-UI 中文预览版 %s。\n' "$version"
curl --proto '=https' --proto-redir '=https' --fail --location --silent --show-error \
    --retry 3 --connect-timeout 15 --max-time 600 --max-filesize 104857600 \
    --output "$work/$file" "$repo/$revision/packages/$file" || die '安装包下载失败。'
printf '%s  %s\n' "$digest" "$work/$file" | sha256sum --check --strict --quiet || die '安装包 SHA-256 校验失败。'
tar -xzf "$work/$file" -C "$work" || die '解包失败。'

package="$work/3x-ui-zh-lite"
[[ -s $package/install.sh && -f $package/VERSION && $(<"$package/VERSION") == "$version" ]] || die '安装包内容或版本不正确。'
if [[ ${1:-} == --verify-only ]]; then
    (cd "$package"; sha256sum --check --strict --quiet SHA256SUMS) || die '解包后的文件校验失败。'
    printf '预览安装包 %s 下载及校验通过。\n' "$version"
    exit 0
fi

exec </dev/null
if (( EUID == 0 )); then bash "$package/install.sh" "$@"; else sudo bash "$package/install.sh" "$@"; fi
