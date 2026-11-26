#!/usr/bin/env bash
set -euo pipefail
umask 077

die() { printf '在线安装错误：%s\n' "$*" >&2; exit 1; }

channel=stable
mode=install
verify_only=0
case ${1:-} in
    --help|-h) printf '3X-UI 中文版\n空白主机安装：可指定 --port 端口、--postgres、--fail2ban、--ssl 域名或 IPv4。\n保留已有配置迁移：--migrate\n仅下载并校验：--verify-only\n'; exit 0 ;;
    --update) mode=update; shift ;;
    --migrate) mode=migrate; shift ;;
    --verify-only) verify_only=1; shift ;;
esac
if [[ $mode == update && ${1:-} == --dev ]]; then channel=dev; shift; fi
[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || die '当前安装包适用于 Linux x86_64。'
for program in curl sha256sum tar; do command -v "$program" >/dev/null || die "系统缺少 $program。"; done

repo=https://raw.githubusercontent.com/ForceMind/V-UI
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
fetch=(curl --proto '=https' --proto-redir '=https' --fail --location --silent --show-error --retry 3 --connect-timeout 15)
"${fetch[@]}" --max-time 45 --max-filesize 256 --output "$work/release" "$repo/3x-ui-zh-lite/packages/$channel.txt" \
    || die "暂时无法读取$([[ $channel == dev ]] && printf '开发版' || printf '稳定版')安装包清单。"
IFS=' ' read -r version digest revision extra < "$work/release" || die '安装包清单格式错误。'
[[ -z ${extra:-} && $version =~ ^v3\.11\.0-zh\.[1-9][0-9]{0,8}$ && $digest =~ ^[0-9a-f]{64}$ && $revision =~ ^[0-9a-f]{40}$ ]] \
    || die '安装包清单中的版本、摘要或提交不合法。'

if [[ $mode == update && -r /usr/local/x-ui/.zh-lite-version ]] && [[ $(</usr/local/x-ui/.zh-lite-version) == "$version" ]]; then
    printf '已是当前渠道版本 %s。\n' "$version"
    exit 0
fi

file="3x-ui-${version}-linux-amd64.tar.gz"
printf '正在下载 3X-UI 中文版 %s。\n' "$version"
"${fetch[@]}" --max-time 600 --max-filesize 104857600 --output "$work/$file" "$repo/$revision/packages/$file" \
    || die '安装包下载失败。'
printf '%s  %s\n' "$digest" "$work/$file" | sha256sum --check --strict --quiet || die '安装包 SHA-256 校验失败。'
tar -xzf "$work/$file" -C "$work" || die '解包失败。'
path="$work/3x-ui-zh-lite/install.sh"
if (( verify_only )); then
    [[ -s $path ]] || die '安装脚本未打入安装包。'
    (cd "$work/3x-ui-zh-lite"; sha256sum --check --strict --quiet SHA256SUMS) || die '解包后的文件校验失败。'
    printf '安装包 %s 下载及校验通过。\n' "$version"
    exit 0
fi
if [[ $mode == update && -f /usr/local/x-ui/.zh-lite-migration ]]; then mode=migrate; fi
case $mode in
    update) set -- --update "$@" ;;
    migrate) set -- --migrate "$@" ;;
esac
if (( EUID == 0 )); then bash "$path" "$@"; else sudo bash "$path" "$@"; fi
