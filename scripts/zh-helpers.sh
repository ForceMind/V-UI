#!/usr/bin/env bash

xui_migrated() {
    [[ -e /usr/local/x-ui/.zh-lite-migration || -L /usr/local/x-ui/.zh-lite-migration ]]
}

xui_runtime() (
    local app=/usr/local/x-ui mode owner path
    for path in /usr /usr/local "$app" "$app/zh-migrate.sh" "$app/.zh-lite-migration"; do
        [[ ! -L $path && -e $path ]] || { printf '接管文件路径不可信：%s\n' "$path" >&2; return 1; }
        owner=$(stat -c '%u' -- "$path") || return
        mode=$(stat -c '%a' -- "$path") || return
        [[ $owner == 0 ]] && (( (8#$mode & 8#022) == 0 )) || { printf '接管文件必须由 root 独占管理：%s\n' "$path" >&2; return 1; }
    done
    [[ -f $app/zh-migrate.sh && -f $app/.zh-lite-migration ]] || return 1
    source "$app/zh-migrate.sh" || return
    migration_inspect runtime
)

xui_load_paths() {
    xui_db_folder=/etc/x-ui
    xui_log_folder=/var/log/x-ui
    xui_db_kind=sqlite
    xui_unit_fragment=/etc/systemd/system/x-ui.service
    xui_service_account=x-ui-zh
    xui_bin_folder=/usr/local/x-ui/bin
    if xui_migrated; then
        local state parsed
        local -a paths
        state=$(xui_runtime) || return
        parsed=$(python3 -I -c 'import json,os,sys; s=json.load(sys.stdin); log=s["environment"].get("XUI_LOG_FOLDER") or "/var/log/x-ui"; bin=s["environment"].get("XUI_BIN_FOLDER") or "bin"; print(s["database"]); print(os.path.normpath(os.path.join("/usr/local/x-ui", log))); print(s["database_kind"]); print(s["unit"]); print(os.path.normpath(os.path.join("/usr/local/x-ui", bin)))' <<< "$state") || return
        mapfile -t paths <<< "$parsed"
        [[ ${#paths[@]} == 5 ]] || return 1
        xui_db_folder=${paths[0]}
        xui_log_folder=${paths[1]}
        xui_db_kind=${paths[2]}
        xui_unit_fragment=${paths[3]}
        xui_bin_folder=${paths[4]}
        xui_service_account=root
    fi
}

xui_service_exec() (
    cd /usr/local/x-ui || return
    if xui_migrated; then
        local state
        state=$(xui_runtime) || return
        # Re-read current service configuration for every command. Snapshots are backups,
        # never a credentials source. Do not export these values to the caller shell.
        python3 -I -c 'import json,os,sys; s=json.load(sys.stdin); e={k:v for k,v in os.environ.items() if not k.startswith("XUI_") and k!="NODE_TOKEN_ENCRYPTION"}; e.update(s.get("service_environment", s["environment"])); os.execve("/usr/local/x-ui/x-ui", ["x-ui", *sys.argv[1:]], e)' "$@" <<< "$state"
    else
        runuser -u x-ui-zh -- ./x-ui "$@"
    fi
)

xui_cli() (
    if ! xui_migrated; then
        export XUI_DB_FOLDER=/etc/x-ui XUI_LOG_FOLDER=/var/log/x-ui
    fi
    if ! xui_migrated && [[ -r /etc/default/x-ui ]]; then
        local name value
        while IFS='=' read -r name value; do
            case $name in
                XUI_DB_TYPE|XUI_DB_DSN|XUI_DB_MAX_OPEN_CONNS|XUI_DB_MAX_IDLE_CONNS)
                    if [[ $value == \'*\' || $value == \"*\" ]]; then value=${value:1:${#value}-2}; fi
                    export "$name=$value" ;;
            esac
        done < /etc/default/x-ui
    fi
    if [[ ${1:-} == cert && ${2:-} == -webCert ]]; then
        cert_permissions || return
        if xui_migrated; then
            xui_runtime >/dev/null || return
            [[ -r $3 && -r $5 ]] || { printf '原 root 服务无法读取证书或私钥。\n' >&2; return 1; }
        elif ! runuser -u x-ui-zh -- test -r "$3" || ! runuser -u x-ui-zh -- test -r "$5"; then
            printf '证书或私钥无法由 x-ui-zh 服务账号读取，请核对文件及父目录权限。\n' >&2
            return 1
        fi
        local cert_key private_key
        cert_key=$(openssl x509 -in "$3" -pubkey -noout) || return
        private_key=$(openssl pkey -in "$5" -passin pass: -pubout) || return
        if [[ -z $cert_key || $cert_key != "$private_key" ]]; then
            printf '证书与私钥不匹配。\n' >&2
            return 1
        fi
    fi
    cd /usr/local/x-ui || return
    xui_service_exec "$@" || return
    if [[ ${1:-} == cert && ( ${2:-} == -webCert || ${2:-} == -reset ) ]]; then
        local settings cert= key=
        if [[ $2 == -webCert ]]; then cert=$3; key=$5; fi
        settings=$(xui_service_exec setting -getCert true) || return
        if ! grep -Fxq -- "cert: $cert" <<< "$settings" || ! grep -Fxq -- "key: $key" <<< "$settings"; then
            printf '无法确认面板已保存所需的证书和私钥路径。\n' >&2
            return 1
        fi
    fi
)

xui_certificate_root() {
    if xui_migrated; then printf '/root/cert\n'; else printf '/etc/x-ui-managed-certs\n'; fi
}

cert_permissions() {
    local certificates
    certificates=$(xui_certificate_root) || return
    if xui_migrated; then
        xui_runtime >/dev/null || return
        # The original root service can read its existing certificates. Keep their
        # ownership and renewal hooks; there may be no x-ui-zh account at all.
        if [[ ! -e $certificates && ! -L $certificates ]]; then install -d -m 0700 -o root -g root "$certificates"; fi
        [[ -d $certificates && ! -L $certificates ]] || return 1
        return 0
    fi
    install -d -m 0750 -o root -g x-ui-zh "$certificates" || return
    find -P "$certificates" -mindepth 1 -type d -exec chgrp x-ui-zh {} + -exec chmod 2750 {} + || return
    find -P "$certificates" -type f \( -name privkey.pem -o -name fullchain.pem \) \
        -exec chgrp x-ui-zh {} + -exec chmod 0640 {} +
}

cert_dependencies() {
    if command -v socat >/dev/null && command -v crontab >/dev/null; then return 0; fi
    printf '正在安装证书签发和自动续期依赖。\n'
    if command -v apt-get >/dev/null; then
        apt-get update && apt-get install -y socat cron
    elif command -v dnf >/dev/null; then
        dnf install -y socat cronie && systemctl enable --now crond.service
    elif command -v pacman >/dev/null; then
        pacman -Sy --needed --noconfirm socat cronie && systemctl enable --now cronie.service
    else
        printf '请先安装 socat 和 cron 服务。\n' >&2
        return 1
    fi
}

is_port_in_use() {
    local port="$1" listeners
    if command -v ss >/dev/null && listeners=$(ss -H -ltn "sport = :$port" 2>/dev/null); then
        [[ -n $listeners ]]
    elif command -v netstat >/dev/null; then
        netstat -lnt 2>/dev/null | awk -v pattern=":${port}$" '$4 ~ pattern {found=1} END {exit !found}'
    else
        command -v lsof >/dev/null && lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
    fi
}

wait_for_panel() {
    local url="$1" attempt
    shift
    for ((attempt=0; attempt<30; attempt++)); do
        if curl --noproxy '*' --fail --silent --max-time 1 --output /dev/null "$@" -- "$url"; then return 0; fi
        systemctl is-active --quiet x-ui.service || return 1
        sleep 1
    done
    return 1
}
