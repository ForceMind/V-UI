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
mode_selected=
port=2053
port_set=0
postgres=0
fail2ban=0
ssl_host=
update_units=(x-ui-zh-update.service x-ui-zh-update.path)

die() { printf '安装错误：%s\n' "$*" >&2; exit 1; }
select_mode() {
    [[ -z $mode_selected || $mode_selected == "$1" ]] || die '更新和接管模式不能同时使用。'
    mode=$1; mode_selected=$1
}

while (( $# )); do case $1 in
    -h|--help|帮助)
        printf '全新安装：sudo bash install.sh [--port 2053] [--postgres] [--fail2ban] [--ssl 域名或IPv4]\n更新：sudo bash update.sh\n接管原版 v2.9.2 / v3.11.0（保留配置）：sudo bash install.sh --migrate\n'
        exit 0 ;;
    --update) select_mode update; shift ;;
    --migrate) select_mode migrate; shift ;;
    --port)
        [[ $# -ge 2 && $2 =~ ^[1-9][0-9]{0,4}$ ]] || die '端口须为 1 至 65535 的整数。'
        port=$2; port_set=1; shift 2 ;;
    --postgres) postgres=1; shift ;;
    --fail2ban) fail2ban=1; shift ;;
    --ssl) [[ $# -ge 2 && -n $2 ]] || die '请提供要申请证书的域名或 IPv4。'; ssl_host=$2; shift 2 ;;
    *) die '无法识别安装参数。运行 bash install.sh --help 查看用法。' ;;
esac; done
if [[ $mode == update && ( -e $app/.zh-lite-migration || -L $app/.zh-lite-migration ) ]]; then mode=migrate; fi
if [[ $mode == migrate && ( $port_set == 1 || $postgres == 1 || $fail2ban == 1 || -n $ssl_host ) ]]; then die '接管保留原配置，不能同时指定端口、数据库、Fail2ban 或证书参数。'; fi
if [[ $mode == update && ( $postgres == 1 || $fail2ban == 1 || -n $ssl_host ) ]]; then die '更新保留当前证书及数据库。需修改配置时请使用 x-ui 菜单。'; fi
(( port <= 65535 )) || die '端口须为 1 至 65535 的整数。'
(( EUID == 0 )) || die '请使用 sudo 运行安装脚本。'
[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || die '此包仅支持 Linux x86_64。'
for program in systemctl runuser install flock sha256sum awk curl; do
    command -v "$program" >/dev/null || die "缺少系统命令 $program。请按中文说明安装依赖。"
done
[[ -d /run/systemd/system ]] || die '目标需要正在运行的 systemd。'
[[ -s $package/x-ui/x-ui && -s $package/x-ui/bin/xray-linux-amd64 ]] || die '缺少 Linux 执行文件。请完整解压安装包。'
[[ -f $package/VERSION && -f $package/SHA256SUMS ]] || die '缺少版本文件或校验清单。'
( cd "$package"; sha256sum --check --strict --quiet SHA256SUMS ) || die '安装包文件校验失败。'
version=$(<"$package/VERSION")
[[ $version =~ ^v3\.11\.0-zh\.[1-9][0-9]*$ ]] || die '安装包版本无效。'

exec 9>/run/x-ui-zh-lite.lock
flock -n 9 || die '已有安装、更新或运维操作在执行。'

if [[ $mode == migrate ]]; then
    source "$package/scripts/zh-migrate.sh"
    migrate_existing
    exit 0
fi

source "$package/scripts/zh-helpers.sh"
source "$package/scripts/zh-provision.sh"
if [[ -n $ssl_host ]] && ! is_domain "$ssl_host" && ! is_ipv4 "$ssl_host"; then die '证书申请目标必须是有效域名或 IPv4。'; fi

panel_listens_on() {
    local settings
    settings=$(xui_cli setting -getListen true) || return
    grep -Fxq -- "listenIP: $1" <<< "$settings"
}

if [[ $mode == install ]]; then
    for path in "$app" "$db" "$command_path" /usr/bin/x-ui "$unit" /etc/default/x-ui /etc/systemd/system/x-ui.service.d; do
        [[ ! -e $path && ! -L $path ]] || die "发现已有面板或保留的数据：$path。请使用空白主机。"
    done
    if systemctl cat x-ui.service >/dev/null 2>&1; then die '发现已有 x-ui 服务，已停止安装。'; fi
    if ! command -v openssl >/dev/null || ! command -v ss >/dev/null; then
        printf '正在补齐安装依赖。\n'
        if command -v apt-get >/dev/null; then apt-get update && apt-get install -y openssl iproute2 ca-certificates || die '安装系统依赖失败。'
        elif command -v dnf >/dev/null; then dnf install -y openssl iproute ca-certificates || die '安装系统依赖失败。'
        elif command -v pacman >/dev/null; then pacman -S --needed --noconfirm openssl iproute2 ca-certificates || die '安装系统依赖失败。'
        else die '请安装 openssl 和提供 ss 的 iproute2，再重试。'; fi
    fi
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
        if [[ ! -e $stage/原更新监听 ]]; then
            systemctl disable --now --quiet x-ui-zh-update.path
            rm -f -- /etc/systemd/system/x-ui-zh-update.{service,path}
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
install -m 0755 "$package/install-online.sh" "$stage/program/install-online.sh"
install -m 0755 "$package/scripts/zh-ops.sh" "$stage/program/zh-ops.sh"
install -m 0644 "$package/scripts/zh-helpers.sh" "$stage/program/zh-helpers.sh"
install -m 0755 "$package/scripts/zh-update-request.sh" "$stage/program/zh-update-request.sh"

if [[ $mode == install ]]; then
    install -d -m 0700 -o "$account" -g "$account" "$db" "$log"
    username="admin-$(openssl rand -hex 4)"
    password=$(openssl rand -hex 18)
    web_path="/$(openssl rand -hex 12)/"
else
    if [[ -f /etc/systemd/system/x-ui-zh-update.path ]]; then touch "$stage/原更新监听"; fi
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
    listen_ip=0.0.0.0
    if [[ -n $ssl_host ]]; then listen_ip=127.0.0.1; fi
    if [[ $postgres == 1 ]]; then
        dsn=$(install_postgres_local) || die 'PostgreSQL 自动安装失败。'
        [[ $dsn =~ ^postgres://[A-Za-z0-9:@./?=_%-]+$ ]] || die 'PostgreSQL 连接配置无效。'
        printf "XUI_DB_TYPE=postgres\nXUI_DB_DSN='%s'\n" "$dsn" > /etc/default/x-ui
        chmod 0600 /etc/default/x-ui
        ensure_pg_client || printf 'PostgreSQL 备份工具未安装，可稍后在运维菜单补装。\n' >&2
    fi
    xui_cli setting -username "$username" -password "$password" -webBasePath "$web_path" -listenIP "$listen_ip" -port "$port" \
        >/dev/null || die '初始化数据库和面板设置失败。'
    panel_listens_on "$listen_ip" || die '未能保存面板监听地址。'
fi

install -m 0644 "$package/x-ui.service" "$unit"
install -m 0755 "$package/x-ui.sh" "$command_path"
install -d -m 0750 -o root -g "$account" /var/lib/x-ui-zh-updates
for update_unit in "${update_units[@]}"; do
    install -m 0644 "$package/scripts/$update_unit" "/etc/systemd/system/$update_unit"
done
systemctl daemon-reload
systemctl enable --now --quiet x-ui-zh-update.path
if [[ $mode == install ]]; then systemctl enable --quiet x-ui.service; fi
if [[ $mode == install || $was_active == 1 ]]; then
    systemctl start x-ui.service
    if [[ $mode == install ]]; then
        panel_url="http://127.0.0.1:$port$web_path"
        wait_for_panel "$panel_url" || die '面板本地地址未就绪。'
    else
        systemctl is-active --quiet x-ui.service || die '更新之后服务未运行。'
    fi
fi

committed=1
if [[ $mode == install ]]; then
    printf '已安装并启动 3X-UI %s。\n账号：%s\n密码：%s\n' "$version" "$username" "$password"
    if [[ $fail2ban == 1 ]]; then
        "$command_path" setup-fail2ban || printf 'Fail2ban 配置未完成，可在 x-ui 菜单重试。\n' >&2
    fi
    if [[ -n $ssl_host ]]; then
        tls_url="https://$ssl_host:$port$web_path"
        if (cert_permissions && if is_ip "$ssl_host"; then setup_ip_certificate "$ssl_host" ''; else setup_ssl_certificate "$ssl_host"; fi) \
            && systemctl restart x-ui.service \
            && wait_for_panel "$tls_url" --connect-to "$ssl_host:$port:127.0.0.1:$port" \
            && xui_cli setting -listenIP 0.0.0.0 >/dev/null \
            && panel_listens_on 0.0.0.0 \
            && systemctl restart x-ui.service \
            && wait_for_panel "$tls_url" --connect-to "$ssl_host:$port:127.0.0.1:$port"; then
            panel_url=$tls_url
            printf '%s 的 HTTPS 已通过本地验证。\n' "$ssl_host"
        else
            printf '证书申请或 HTTPS 验证未完成，正在恢复本机 HTTP。证书文件保留，可从 sudo x-ui 证书 重试。\n' >&2
            xui_cli setting -listenIP 127.0.0.1 >/dev/null && panel_listens_on 127.0.0.1 && xui_cli cert -reset \
                && systemctl restart x-ui.service && wait_for_panel "$panel_url" \
                || die '未能恢复本机 HTTP，请运行 sudo x-ui 日志 查看详情。'
            printf '面板仅在本机监听。可通过 SSH 转发访问。\n'
        fi
        printf '管理地址：%s\n' "$panel_url"
    else
        if server_ipv4=$(detect_server_ipv4); then
            printf '管理地址：http://%s:%s%s\n' "$server_ipv4" "$port" "$web_path"
        else
            printf '未能自动识别公网 IPv4。请使用服务器的公网地址。\n管理地址：http://<服务器公网 IP>:%s%s\n' "$port" "$web_path"
        fi
        printf '面板监听所有 IPv4 网卡。HTTP 登录为明文，可从 sudo x-ui 证书 配置 HTTPS。\n'
    fi
else
    mv -T -- "$stage/原程序" "$stage/更新前程序"
    printf '已更新到 %s。\n' "$version"
fi
printf '日常管理：sudo x-ui\n'
