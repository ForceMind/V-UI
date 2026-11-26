#!/usr/bin/env bash
set -euo pipefail

app=/usr/local/x-ui
db=/etc/x-ui
unit=x-ui.service

die() { printf '错误：%s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
3X-UI 中文精简版
用法：sudo x-ui [命令]
  状态              查看服务状态
  启动、停止、重启  管理面板和核心进程
  开机启动          启用开机启动
  关闭开机启动      关闭开机启动
  日志              查看最近 80 行服务日志
  设置              查看管理地址和端口
  重置账号          生成新账号和密码，重置两步验证
  证书              申请、续期、查看或绑定域名/IP 证书
  Cloudflare证书    申请 DNS 验证证书
  测速              Ookla 网络测速
  BBR               开启或关闭 BBR
  防火墙            管理防火墙端口
  Fail2ban          安装和管理 Fail2ban 与 IP 限制
  PostgreSQL        自动安装、迁移和管理 PostgreSQL
  备份 <文件>       保存 SQLite 数据，备份期间短暂停机
  更新 [安装包目录] 在线更新或使用已核对校验值的安装包
  卸载              移除程序和服务，保留数据库及日志
EOF
}

require_root() {
    (( EUID == 0 )) || die '请在命令前加 sudo。'
}

lock_operations() {
    exec 9>/run/x-ui-zh-lite.lock
    flock -n 9 || die '已有安装、更新或运维操作在执行。'
}

panel() {
    source "$app/zh-helpers.sh"
    xui_cli "$@"
}

ops() { require_root; exec bash "$app/zh-ops.sh" "$@"; }

show_settings() {
    local settings port path
    settings=$(panel setting -show true) || die '读取面板设置失败。'
    port=$(awk -F ': ' '$1 == "port" {print $2}' <<< "$settings")
    path=$(awk -F ': ' '$1 == "webBasePath" {print $2}' <<< "$settings")
    [[ -n $port && -n $path ]] || die '无法从面板读取端口或访问路径。'
    printf '管理端口：%s\n访问路径：%s\n' "$port" "$path"
}

backup() (
    [[ $# == 1 ]] || die '用法：sudo x-ui 备份 <文件>'
    [[ ! -e $1 && ! -L $1 ]] || die '备份文件已存在，请另选文件名。'
    source "$app/zh-helpers.sh"
    xui_load_paths || die '无法确认原服务的数据位置，已取消备份。'
    db=$xui_db_folder
    [[ $xui_db_kind == sqlite ]] || die '当前使用 PostgreSQL，请从面板数据库备份或 PostgreSQL 工具保存备份。'
    [[ $(realpath -m -- "$1") != "$db/"* ]] || die '请将备份保存到数据库目录之外。'
    umask 077
    local active=0 target temporary snapshot
    target=$(realpath -m -- "$1")
    temporary=$(mktemp -- "$(dirname -- "$target")/.x-ui-backup.XXXXXXXX")
    snapshot=$(mktemp -d -- "$(dirname -- "$target")/.x-ui-snapshot.XXXXXXXX")
    trap 'if (( active )); then systemctl start "$unit" || printf "备份后启动失败，请检查服务日志。\n" >&2; fi; rm -f -- "$temporary"; rm -rf -- "$snapshot"' EXIT
    if systemctl is-active --quiet "$unit"; then
        active=1
        systemctl stop "$unit"
    fi
    [[ $(systemctl show "$unit" --property=MainPID --value) == 0 ]] || die '停服后仍有进程，未保存不一致的备份。'
    cp -a -- "$db" "$snapshot/$(basename -- "$db")"
    if (( active )); then
        systemctl start "$unit" || die '复制数据后服务启动失败，备份尚未保存。请查看日志。'
        active=0
    fi
    # Compression runs after restoring service, so downtime covers only the copy.
    tar -C "$snapshot" -czf "$temporary" "$(basename -- "$db")"
    ln -T -- "$temporary" "$target" || die '保存备份失败。'
    printf '备份已保存：%s\n' "$target"
)

uninstall() {
    local answer migrated=0 log_path unit_path
    source "$app/zh-helpers.sh"
    xui_load_paths || die '无法确认原服务和数据位置，已取消卸载。'
    db=$xui_db_folder
    log_path=$xui_log_folder
    unit_path=$xui_unit_fragment
    if xui_migrated; then migrated=1; fi
    printf '输入“卸载”确认。本操作会停止面板和节点服务，保留数据库和日志。\n'
    read -r answer || return 0
    [[ $answer == 卸载 ]] || return 0
    systemctl disable --now "$unit"
    if systemctl cat x-ui-zh-update.path >/dev/null 2>&1; then systemctl disable --now x-ui-zh-update.path; fi
    if (( migrated )) && [[ -f /usr/bin/x-ui ]] && cmp -s -- /usr/bin/x-ui /usr/local/bin/x-ui; then rm -f -- /usr/bin/x-ui; fi
    rm -f -- "$unit_path" /usr/local/bin/x-ui /etc/systemd/system/x-ui-zh-update.{path,service}
    local retired
    retired=$(mktemp -d /usr/local/.x-ui-uninstalled.XXXXXXXX)
    mv -T -- "$app" "$retired/program"
    if (( migrated )); then
        # Original log/certificate paths may be inside app; keep the retired tree.
        if [[ $log_path == "$app" || $log_path == "$app/"* ]]; then log_path=$retired/program${log_path#"$app"}; fi
        printf '原程序及自定义资源保留在 %s。\n' "$retired/program"
    else
        rm -rf -- "$retired"
    fi
    systemctl daemon-reload
    printf '已卸载。数据库位于 %s，日志位于 %s。原环境文件与自定义服务附加配置保留。\n' "$db" "$log_path"
}

command=${1:-}
if (( $# )); then shift; fi
case $command in
    '') if [[ -t 0 ]]; then ops; else usage; fi ;;
    -h|--help|帮助) usage ;;
    状态|status)
        if systemctl is-active --quiet "$unit"; then printf '面板正在运行。\n';
        else printf '面板当前未运行。可运行 sudo x-ui 日志 查看原因。\n'; exit 1; fi ;;
    启动|start) require_root; systemctl start "$unit"; printf '已启动。\n' ;;
    停止|stop) require_root; systemctl stop "$unit"; printf '已停止。\n' ;;
    重启|restart) require_root; systemctl restart "$unit"; printf '已重启。\n' ;;
    开机启动|enable) require_root; systemctl enable --quiet "$unit"; printf '已启用开机启动。\n' ;;
    关闭开机启动|disable) require_root; systemctl disable --quiet "$unit"; printf '已关闭开机启动。\n' ;;
    日志|log) require_root; journalctl --quiet --no-pager -u "$unit" -n 80 ;;
    设置|settings) require_root; show_settings ;;
    重置账号|reset-user)
        require_root
        lock_operations
        username="admin-$(openssl rand -hex 4)"
        password=$(openssl rand -hex 18)
        panel setting -username "$username" -password "$password" -resetTwoFactor=true >/dev/null || die '重置账号失败。'
        printf '新账号：%s\n新密码：%s\n' "$username" "$password"
        systemctl restart "$unit" ;;
    备份|backup) require_root; lock_operations; backup "$@" ;;
    证书|cert) ops cert ;;
    Cloudflare证书|cloudflare-cert) ops cloudflare-cert ;;
    测速|speedtest) ops speedtest ;;
    BBR|bbr) ops bbr ;;
    防火墙|firewall) ops firewall ;;
    Fail2ban|ip-limit) ops ip-limit ;;
    PostgreSQL|postgres) ops postgres ;;
    setup-fail2ban|banlog|restart-xray|update-all-geofiles|geo|migrateDB|pgclient) ops "$command" "$@" ;;
    cert-permissions) require_root; source "$app/zh-helpers.sh"; cert_permissions ;;
    更新|update)
        require_root
        if (( $# == 0 )); then exec bash "$app/install-online.sh" --update; fi
        [[ $# == 1 && -f $1/update.sh ]] || die '请传入已解压且校验过的同版系安装包目录。'
        source "$app/zh-helpers.sh"
        if xui_migrated; then exec bash "$1/install.sh" --migrate; fi
        exec bash "$1/update.sh" ;;
    update-dev) require_root; exec bash "$app/install-online.sh" --update --dev ;;
    卸载|uninstall) require_root; lock_operations; uninstall ;;
    *) usage >&2; die '无法识别此命令。' ;;
esac
