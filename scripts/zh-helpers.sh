#!/usr/bin/env bash

xui_cli() (
    export XUI_DB_FOLDER=/etc/x-ui XUI_LOG_FOLDER=/var/log/x-ui
    if [[ -r /etc/default/x-ui ]]; then
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
        if ! runuser -u x-ui-zh -- test -r "$3" || ! runuser -u x-ui-zh -- test -r "$5"; then
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
    runuser -u x-ui-zh -- ./x-ui "$@"
)

cert_permissions() {
    local certificates=/etc/x-ui-managed-certs
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
