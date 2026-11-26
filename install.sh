#!/usr/bin/env bash
set -euo pipefail
umask 077

package=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
account=x-ui-zh
app=/usr/local/x-ui
db=/etc/x-ui
log=/var/log/x-ui
unit=/etc/systemd/system/x-ui.service
command_path=/usr/local/bin/x-ui
mode=install
port=2053

die() { printf '安装错误：%s\n' "$*" >&2; exit 1; }

case ${1:-} in
    -h|--help|帮助)
        printf '全新安装：sudo bash install.sh [--port 2053]\n更新本精简版：sudo bash update.sh\n'
        exit 0 ;;
    --update) mode=update; shift ;;
    --port)
        [[ $# == 2 && $2 =~ ^[1-9][0-9]{0,4}$ ]] || die '端口须为 1 至 65535 的整数。'
        port=$2; shift 2 ;;
esac
[[ $# == 0 ]] || die '无法识别安装参数。运行 bash install.sh --help 查看用法。'
(( port <= 65535 )) || die '端口须为 1 至 65535 的整数。'
(( EUID == 0 )) || die '请使用 sudo 运行安装脚本。'
[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || die '此包仅支持 Linux x86_64。'
for program in systemctl runuser install flock sha256sum openssl ss awk curl; do
    command -v "$program" >/dev/null || die "缺少系统命令 $program。请按中文说明安装依赖。"
done
[[ -d /run/systemd/system ]] || die '目标需要正在运行的 systemd。'
[[ -s $package/x-ui/x-ui && -s $package/x-ui/bin/xray-linux-amd64 ]] || die '缺少 Linux 执行文件。请完整解压安装包。'
[[ -f $package/版本 && -f $package/SHA256SUMS ]] || die '缺少版本文件或校验清单。'
( cd "$package"; sha256sum --check --strict --quiet SHA256SUMS ) || die '安装包文件校验失败。'
version=$(<"$package/版本")
[[ $version =~ ^v3\.11\.0-zh\.[1-9][0-9]*$ ]] || die '安装包版本无效。'

exec 9>/run/lock/x-ui-zh-lite.lock
flock -n 9 || die '已有安装、更新或运维操作在执行。'

if [[ $mode == install ]]; then
    for path in "$app" "$db" "$command_path" /usr/bin/x-ui "$unit" /etc/default/x-ui /etc/systemd/system/x-ui.service.d; do
        [[ ! -e $path && ! -L $path ]] || die "发现已有面板或保留的数据：$path。请使用空白主机。"
    done
    if systemctl cat x-ui.service >/dev/null 2>&1; then die '发现已有 x-ui 服务，已停止安装。'; fi
    listening=$(ss -H -ltn "sport = :$port") || die '无法检查面板端口。'
    [[ -z $listening ]] || die "端口 $port 已被占用，请通过 --port 指定其他端口。"
    if ! id "$account" >/dev/null 2>&1; then
        useradd --system --user-group --no-create-home --home-dir "$db" --shell /usr/sbin/nologin "$account"
    fi
else
    [[ -f $app/.zh-lite-version && -x $command_path && -f $unit && -d $db ]] || die '未找到同版系安装。'
    previous=$(<"$app/.zh-lite-version")
    [[ $previous =~ ^v3\.11\.0-zh\.[1-9][0-9]*$ ]] || die '现有版本不受此更新脚本支持。'
    [[ $(printf '%s\n' "$previous" "$version" | sort -V | tail -n 1) == "$version" ]] || die '更新脚本拒绝降级。'
fi

stage=$(mktemp -d /usr/local/.x-ui-install.XXXXXXXX)
chown root:root "$stage"
changed=0
committed=0
was_active=0
cleanup() {
    local code=$?
    trap - EXIT
    if (( ! committed && changed )); then
        printf '配置或启动失败，正在恢复安装前的服务文件。\n' >&2
        set +e
        systemctl stop x-ui.service
        if [[ $mode == update ]]; then
            if [[ -d $stage/原程序 ]]; then
                if [[ -d $app ]]; then mv -T -- "$app" "$stage/失败程序"; fi
                mv -T -- "$stage/原程序" "$app"
            fi
            install -m 0644 "$stage/原服务" "$unit"
            install -m 0755 "$stage/原命令" "$command_path"
        else
            systemctl disable --quiet x-ui.service
            rm -f -- "$unit" "$command_path"
            if [[ -d $app ]]; then mv -T -- "$app" "$stage/失败程序"; fi
            printf '初始化数据保留在 %s。\n' "$db" >&2
        fi
        systemctl daemon-reload
        if (( was_active )); then systemctl start x-ui.service; fi
        printf '请检查 journalctl -u x-ui.service。\n' >&2
    fi
    if [[ ! -d $stage/原程序 ]]; then rm -rf -- "$stage";
    else printf '原程序仍在 %s，请检查恢复结果。\n' "$stage/原程序" >&2; fi
    exit "$code"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

cp -a -- "$package/x-ui" "$stage/program"
chown -R root:root "$stage/program"
chmod 0755 "$stage/program" "$stage/program/x-ui" "$stage/program/bin"
chmod 0755 "$stage/program/bin/"*-linux-amd64
chown "$account:$account" "$stage/program/bin"
printf '%s\n' "$version" > "$stage/program/.zh-lite-version"

if [[ $mode == install ]]; then
    install -d -m 0700 -o "$account" -g "$account" "$db" "$log"
    username="admin-$(openssl rand -hex 4)"
    password=$(openssl rand -hex 18)
    web_path="/$(openssl rand -hex 12)/"
else
    install -m 0644 "$unit" "$stage/原服务"
    install -m 0755 "$command_path" "$stage/原命令"
    if systemctl is-active --quiet x-ui.service; then was_active=1; fi
    changed=1
    systemctl stop x-ui.service
    install -d -m 0700 /var/backups/x-ui-zh-lite
    backup=$(mktemp /var/backups/x-ui-zh-lite/数据库.XXXXXXXX.tar.gz)
    tar -C /etc -czf "$backup" x-ui
    printf '已保存更新前数据库：%s\n' "$backup"
fi

changed=1
if [[ $mode == update ]]; then
    mv -T -- "$app" "$stage/原程序"
    # 节点配置和规则文件可能由面板写入；同核心版本更新时保留这些文件。
    for path in "$stage/原程序/bin/"*.dat; do
        if [[ -f $path && ! -L $path ]]; then install -m 0644 -- "$path" "$stage/program/bin/$(basename -- "$path")"; fi
    done
fi
mv -T -- "$stage/program" "$app"

if [[ $mode == install ]]; then
    ( cd "$app"; runuser -u "$account" -- env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME="$db" \
        XUI_DB_FOLDER="$db" XUI_LOG_FOLDER="$log" "$app/x-ui" setting \
        -username "$username" -password "$password" -webBasePath "$web_path" -listenIP 127.0.0.1 -port "$port" ) \
        >/dev/null || die '初始化 SQLite 和面板设置失败。'
fi

install -m 0644 "$package/x-ui.service" "$unit"
install -m 0755 "$package/x-ui.sh" "$command_path"
systemctl daemon-reload
if [[ $mode == install ]]; then systemctl enable --quiet x-ui.service; fi
if [[ $mode == install || $was_active == 1 ]]; then
    systemctl start x-ui.service
    if [[ $mode == install ]]; then
        ready=0
        for (( attempt=0; attempt<30; attempt++ )); do
            if curl --fail --silent --max-time 1 --output /dev/null "http://127.0.0.1:$port$web_path"; then ready=1; break; fi
            systemctl is-active --quiet "$unit" || break
            sleep 1
        done
        (( ready )) || die '面板本地地址未就绪。'
    else
        systemctl is-active --quiet "$unit" || die '更新之后服务未运行。'
    fi
fi

committed=1
if [[ $mode == install ]]; then
    printf '已安装并启动 3X-UI %s。\n管理地址：http://127.0.0.1:%s%s\n账号：%s\n密码：%s\n' \
        "$version" "$port" "$web_path" "$username" "$password"
    printf '在本机通过 SSH 转发该端口，再打开上面的地址。请妥善保管账号和密码。\n'
else
    mv -T -- "$stage/原程序" "$stage/更新前程序"
    printf '已更新到 %s。\n' "$version"
fi
printf '日常管理：sudo x-ui\n'
