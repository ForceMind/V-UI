#!/usr/bin/env bash

# 复用 3X-UI v3.11.0 的数据库与证书安装操作。
red='\033[0;31m'
green='\033[0;32m'
blue='\033[0;34m'
yellow='\033[0;33m'
plain='\033[0m'
PG_TARGET_MAJOR=18
NONINTERACTIVE=1
source /etc/os-release
release=$ID

gen_random_string() {
    local length="$1"
    openssl rand -base64 $((length * 2)) \
        | tr -dc 'a-zA-Z0-9' \
        | head -c "$length"
}

pg_ensure_hba_password_auth() {
    local pg_db="$1"
    local hba_file
    hba_file=$(runuser -u postgres -- psql -tAc 'SHOW hba_file' 2> /dev/null | tr -d '[:space:]')
    [[ -n "${hba_file}" && -f "${hba_file}" ]] || return 0
    grep -Eq "^host[[:space:]]+${pg_db}[[:space:]]" "${hba_file}" && return 0
    local tmp
    tmp=$(mktemp) || return 1
    {
        echo "# 由 3x-ui 添加：允许面板数据库使用密码登录。"
        echo "host    ${pg_db}    all    127.0.0.1/32    md5"
        echo "host    ${pg_db}    all    ::1/128         md5"
        cat "${hba_file}"
    } > "${tmp}" || {
        rm -f "${tmp}"
        return 1
    }
    cat "${tmp}" > "${hba_file}" || {
        rm -f "${tmp}"
        return 1
    }
    rm -f "${tmp}"
    runuser -u postgres -- psql -tAc 'SELECT pg_reload_conf()' > /dev/null 2>&1 || true
}

pg_has_debian_cluster() {
    command -v pg_lsclusters > /dev/null 2>&1 && [[ -n "$(pg_lsclusters -h 2> /dev/null)" ]]
}

pg_apt_install_pgdg_server() {
    local list added_lists=()
    for list in /etc/apt/sources.list.d/pgdg.list /etc/apt/sources.list.d/pgdg.sources; do
        [[ -e "${list}" ]] || added_lists+=("${list}")
    done
    if apt-get install -y -q postgresql-common ca-certificates gnupg >&2 \
        && /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y >&2 \
        && apt-get install -y -q "postgresql-${PG_TARGET_MAJOR}" >&2; then
        return 0
    fi
    for list in "${added_lists[@]}"; do
        rm -f "${list}"
    done
    apt-get update >&2 || true
    echo -e "${yellow}当前系统无法安装 PostgreSQL ${PG_TARGET_MAJOR}，将改用发行版提供的 PostgreSQL。${plain}" >&2
    return 1
}

pg_el_install_pgdg_server() {
    local elver
    elver=$(rpm -E %rhel 2> /dev/null)
    [[ "${elver}" =~ ^[0-9]+$ && "${elver}" -ge 8 ]] && command -v dnf > /dev/null 2>&1 || return 1
    [[ -f /var/lib/pgsql/data/PG_VERSION ]] && return 1
    local added_repo=false
    if ! rpm -q pgdg-redhat-repo > /dev/null 2>&1; then
        if dnf install -y -q "https://download.postgresql.org/pub/repos/yum/reporpms/EL-${elver}-$(uname -m)/pgdg-redhat-repo-latest.noarch.rpm" >&2; then
            added_repo=true
            dnf -qy module disable postgresql > /dev/null 2>&1 || true
        fi
    fi
    if rpm -q pgdg-redhat-repo > /dev/null 2>&1 \
        && dnf install -y -q "postgresql${PG_TARGET_MAJOR}-server" "postgresql${PG_TARGET_MAJOR}-contrib" >&2; then
        return 0
    fi
    if [[ "${added_repo}" == "true" ]]; then
        dnf -qy module reset postgresql > /dev/null 2>&1 || true
        dnf remove -y -q pgdg-redhat-repo >&2 || true
    fi
    echo -e "${yellow}当前系统无法安装 PostgreSQL ${PG_TARGET_MAJOR}，将改用发行版提供的 PostgreSQL。${plain}" >&2
    return 1
}

install_postgres_local() {
    local pg_user pg_pass
    pg_pass=$(gen_random_string 24)
    local pg_db="xui_zh"
    local pg_host="127.0.0.1"
    local pg_port="5432"

    local pg_service="postgresql"
    case "${release}" in
        ubuntu | debian | armbian)
            apt-get update >&2 || return 1
            if pg_has_debian_cluster || ! pg_apt_install_pgdg_server; then
                apt-get install -y -q postgresql >&2 || return 1
            fi
            ;;
        fedora | amzn | virtuozzo | rhel | almalinux | rocky | ol | centos)
            if [[ "${release}" != "fedora" && "${release}" != "amzn" ]] && pg_el_install_pgdg_server; then
                pg_service="postgresql-${PG_TARGET_MAJOR}"
                [[ -f "/var/lib/pgsql/${PG_TARGET_MAJOR}/data/PG_VERSION" ]] \
                    || "/usr/pgsql-${PG_TARGET_MAJOR}/bin/postgresql-${PG_TARGET_MAJOR}-setup" initdb >&2 || return 1
            else
                if [[ "${release}" == "centos" && "${VERSION_ID}" =~ ^7 ]]; then
                    yum install -y postgresql-server postgresql-contrib >&2 || return 1
                else
                    dnf install -y -q postgresql-server postgresql-contrib >&2 || return 1
                fi
                [[ -d /var/lib/pgsql/data && -f /var/lib/pgsql/data/PG_VERSION ]] || postgresql-setup --initdb >&2 || return 1
            fi
            ;;
        arch | manjaro | parch)
            pacman -Sy --noconfirm postgresql >&2 || return 1
            if [[ ! -f /var/lib/postgres/data/PG_VERSION ]]; then
                runuser -u postgres -- initdb -D /var/lib/postgres/data >&2 || return 1
            fi
            ;;
        opensuse-tumbleweed | opensuse-leap)
            zypper -q install -y postgresql-server postgresql-contrib >&2 || return 1
            if [[ ! -f /var/lib/pgsql/data/PG_VERSION ]]; then
                install -d -o postgres -g postgres -m 700 /var/lib/pgsql/data >&2 || return 1
                su - postgres -c "initdb -D /var/lib/pgsql/data" >&2 || return 1
            fi
            ;;
        alpine)
            apk add --no-cache postgresql postgresql-contrib >&2 || return 1
            if [[ ! -f /var/lib/postgresql/data/PG_VERSION ]]; then
                /etc/init.d/postgresql setup >&2 || return 1
            fi
            rc-update add postgresql default >&2 2> /dev/null || true
            rc-service postgresql start >&2 || return 1
            ;;
        *)
            echo -e "${red}当前发行版 ${release} 不支持自动安装 PostgreSQL${plain}" >&2
            return 1
            ;;
    esac

    if [[ "${release}" != "alpine" ]]; then
        systemctl enable --now "${pg_service}" >&2 || return 1
    fi

    # Wait briefly for the server to accept connections.
    local i
    for i in 1 2 3 4 5; do
        runuser -u postgres -- psql -tAc 'SELECT 1' > /dev/null 2>&1 && break
        sleep 1
    done

    local existing_owner=""
    existing_owner=$(runuser -u postgres -- psql -tAc \
        "SELECT pg_catalog.pg_get_userbyid(datdba) FROM pg_database WHERE datname='${pg_db}'" 2> /dev/null \
        | tr -d '[:space:]')
    if [[ -n "$existing_owner" ]]; then
        printf '数据库 %s 已存在，保留现有数据库与账号。可在运维菜单迁移或接入。\n' "$pg_db" >&2
        return 1
    fi
    pg_user="xui_$(gen_random_string 8)"

    # Idempotent role/db creation. Identifiers are double-quoted because a
    # random username may start with a digit, which Postgres rejects unquoted.
    runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='${pg_user}'" 2> /dev/null \
        | grep -q 1 \
        || runuser -u postgres -- psql -c "CREATE USER \"${pg_user}\" WITH PASSWORD '${pg_pass}';" >&2 || return 1

    runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_database WHERE datname='${pg_db}'" 2> /dev/null \
        | grep -q 1 \
        || runuser -u postgres -- psql -c "CREATE DATABASE \"${pg_db}\" OWNER \"${pg_user}\";" >&2 || return 1

    runuser -u postgres -- psql -c "ALTER USER \"${pg_user}\" WITH PASSWORD '${pg_pass}';" >&2 || return 1

    pg_ensure_hba_password_auth "${pg_db}" \
        || echo -e "${yellow}警告：无法更新 pg_hba.conf，PostgreSQL 可能因 ident 认证拒绝面板的 TCP 登录。${plain}" >&2

    local pg_pass_enc
    pg_pass_enc=$(printf '%s' "${pg_pass}" | sed -e 's/%/%25/g' -e 's/:/%3A/g' -e 's/@/%40/g' -e 's|/|%2F|g' -e 's/?/%3F/g' -e 's/#/%23/g')

    if [[ -n "${PG_CRED_FILE:-}" ]]; then
        local prev_umask
        prev_umask=$(umask)
        umask 077
        if ! cat > "${PG_CRED_FILE}" << EOF; then
PG_USER=${pg_user}
PG_PASS=${pg_pass}
PG_HOST=${pg_host}
PG_PORT=${pg_port}
PG_DB=${pg_db}
EOF
            umask "${prev_umask}"
            echo -e "${red}无法将 PostgreSQL 凭据写入 ${PG_CRED_FILE}${plain}" >&2
            return 1
        fi
        umask "${prev_umask}"
    fi

    echo "postgres://${pg_user}:${pg_pass_enc}@${pg_host}:${pg_port}/${pg_db}?sslmode=disable"
    return 0
}

ensure_pg_client() {
    if command -v pg_dump > /dev/null 2>&1 && command -v pg_restore > /dev/null 2>&1; then
        return 0
    fi
    echo -e "${yellow}正在安装 PostgreSQL 客户端工具（pg_dump/pg_restore），以支持面板内备份……${plain}" >&2
    case "${release}" in
        ubuntu | debian | armbian)
            apt-get update >&2 && apt-get install -y -q postgresql-client >&2 || return 1
            ;;
        fedora | amzn | virtuozzo | rhel | almalinux | rocky | ol)
            dnf install -y -q postgresql >&2 || return 1
            ;;
        centos)
            if [[ "${VERSION_ID}" =~ ^7 ]]; then
                yum install -y postgresql >&2 || return 1
            else
                dnf install -y -q postgresql >&2 || return 1
            fi
            ;;
        arch | manjaro | parch)
            pacman -Sy --noconfirm postgresql >&2 || return 1
            ;;
        opensuse-tumbleweed | opensuse-leap)
            zypper -q install -y postgresql >&2 || return 1
            ;;
        alpine)
            apk add --no-cache postgresql-client >&2 || return 1
            ;;
        *)
            return 1
            ;;
    esac
    command -v pg_dump > /dev/null 2>&1 && command -v pg_restore > /dev/null 2>&1
}

is_ipv4() {
    [[ "$1" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] && return 0 || return 1
}

is_ipv6() {
    [[ "$1" =~ : ]] && return 0 || return 1
}

is_ip() {
    is_ipv4 "$1" || is_ipv6 "$1"
}

is_domain() {
    [[ "$1" =~ ^([A-Za-z0-9](-*[A-Za-z0-9])*\.)+(xn--[a-z0-9]{2,}|[A-Za-z]{2,})$ ]] && return 0 || return 1
}

acme_listen_flag() {
    if ip -4 addr show scope global 2> /dev/null | grep -q "inet "; then
        echo ""
    else
        echo "--listen-v6"
    fi
}

install_acme() {
    echo -e "${green}正在安装 acme.sh 证书工具。${plain}"
    cd ~ || return 1
    if ! (set -o pipefail; curl -fsSL https://get.acme.sh | sh) || [[ ! -x $HOME/.acme.sh/acme.sh ]]; then
        echo -e "${red}acme.sh 安装失败${plain}"
        return 1
    else
        echo -e "${green}acme.sh 安装完成${plain}"
    fi
    return 0
}

setup_ssl_certificate() {
    local domain="$1"
    if is_port_in_use 80; then printf '端口 80 已被占用，未启动独立证书验证。可在运维菜单使用 DNS 验证。\n' >&2; return 1; fi
    cert_dependencies || return 1

    echo -e "${green}正在配置证书。${plain}"

    # Check if acme.sh is installed
    if ! command -v ~/.acme.sh/acme.sh &> /dev/null; then
        install_acme
        if [ $? -ne 0 ]; then
            echo -e "${yellow}acme.sh 安装失败，已跳过 SSL 配置${plain}"
            return 1
        fi
    fi

    # Create certificate directory
    local certPath="/etc/x-ui-managed-certs/${domain}"
    mkdir -p "$certPath"

    # Issue certificate
    echo -e "${green}正在签发证书，目标为 ${domain}...${plain}"
    echo -e "${yellow}HTTP 验证需要公网能访问 80 端口。${plain}"

    ~/.acme.sh/acme.sh --set-default-ca --server letsencrypt --force > /dev/null 2>&1
    ~/.acme.sh/acme.sh --issue -d ${domain} $(acme_listen_flag) --standalone --httpport 80 --force

    if [ $? -ne 0 ]; then
        echo -e "${yellow}证书签发失败，目标为 ${domain}${plain}"
        echo -e "${yellow}请确认外网能访问 80 端口，再运行 x-ui 重试。${plain}"
        return 1
    fi

    # Install certificate
    ~/.acme.sh/acme.sh --installcert --force -d ${domain} \
        --key-file /etc/x-ui-managed-certs/${domain}/privkey.pem \
        --fullchain-file /etc/x-ui-managed-certs/${domain}/fullchain.pem \
        --reloadcmd "/usr/local/bin/x-ui cert-permissions && systemctl try-restart x-ui" > /dev/null 2>&1

    if [ $? -ne 0 ]; then
        echo -e "${yellow}证书安装失败${plain}"
        return 1
    fi

    # Set certificate for panel
    local webCertFile="/etc/x-ui-managed-certs/${domain}/fullchain.pem"
    local webKeyFile="/etc/x-ui-managed-certs/${domain}/privkey.pem"

    if [[ -f "$webCertFile" && -f "$webKeyFile" ]]; then
        xui_cli cert -webCert "$webCertFile" -webCertKey "$webKeyFile" || return 1
        echo -e "${green}证书已安装并绑定面板。${plain}"
        return 0
    else
        echo -e "${yellow}找不到证书材料${plain}"
        return 1
    fi
}

setup_ip_certificate() {
    cert_dependencies || return 1
    local ipv4="$1"
    local ipv6="$2" # optional

    echo -e "${green}正在签发 Let's Encrypt 短期 IP 证书。${plain}"
    echo -e "${yellow}提示：IP 证书有效期约 6 天，将自动续期。${plain}"
    echo -e "${yellow}默认监听端口为 80；若选择其他端口，请将公网 80 端口转发到该端口。${plain}"

    # Check for acme.sh
    if ! command -v ~/.acme.sh/acme.sh &> /dev/null; then
        install_acme
        if [ $? -ne 0 ]; then
            echo -e "${red}acme.sh 安装失败${plain}"
            return 1
        fi
    fi

    # Validate IP address
    if [[ -z "$ipv4" ]]; then
        echo -e "${red}需要 IPv4 地址${plain}"
        return 1
    fi

    if ! is_ipv4 "$ipv4"; then
        echo -e "${red}IPv4 地址无效： $ipv4${plain}"
        return 1
    fi

    # Create certificate directory
    local certDir="/etc/x-ui-managed-certs/ip"
    mkdir -p "$certDir"

    # Build domain arguments
    local domain_args="-d ${ipv4}"
    if [[ -n "$ipv6" ]] && is_ipv6 "$ipv6"; then
        domain_args="${domain_args} -d ${ipv6}"
        echo -e "${green}包含 IPv6 地址： ${ipv6}${plain}"
    fi

    # Set reload command for auto-renewal (add || true so it doesn't fail during first install)
    local reloadCmd="systemctl restart x-ui 2>/dev/null || rc-service x-ui restart 2>/dev/null || true"

    # Choose port for HTTP-01 listener (default 80, prompt override)
    local WebPort=""
    WebPort=${XUI_ACME_HTTP_PORT:-80}
    WebPort="${WebPort:-80}"
    if ! [[ "${WebPort}" =~ ^[0-9]+$ ]] || ((WebPort < 1 || WebPort > 65535)); then
        echo -e "${red}端口无效，使用 80 端口。${plain}"
        WebPort=80
    fi
    echo -e "${green}使用端口 ${WebPort} 进行独立验证。${plain}"
    if [[ "${WebPort}" -ne 80 ]]; then
        echo -e "${yellow}提醒：Let's Encrypt 仍会连接公网 80 端口，请将其转发到 ${WebPort}。${plain}"
    fi

    # Ensure chosen port is available
    while true; do
        if is_port_in_use "${WebPort}"; then
            echo -e "${yellow}端口 ${WebPort} 已被占用。${plain}"

            local alt_port=""
            if [[ "$NONINTERACTIVE" == "1" ]]; then
                echo -e "${red}端口 ${WebPort} 已被占用，非交互模式下无法继续。${plain}"
                return 1
            fi
            read -rp "请输入 acme.sh 独立验证服务的其他监听端口（留空取消）： " alt_port
            alt_port="${alt_port// /}"
            if [[ -z "${alt_port}" ]]; then
                echo -e "${red}端口 ${WebPort} 已被占用，无法继续。${plain}"
                return 1
            fi
            if ! [[ "${alt_port}" =~ ^[0-9]+$ ]] || ((alt_port < 1 || alt_port > 65535)); then
                echo -e "${red}输入的端口无效。${plain}"
                return 1
            fi
            WebPort="${alt_port}"
            continue
        else
            echo -e "${green}端口 ${WebPort} 可用，可以进行独立验证。${plain}"
            break
        fi
    done

    # Issue certificate with shortlived profile
    echo -e "${green}正在为 ${ipv4} 签发 IP 证书……${plain}"
    ~/.acme.sh/acme.sh --set-default-ca --server letsencrypt --force > /dev/null 2>&1 || { printf '设置证书签发机构失败。\n' >&2; return 1; }
    if [[ -n "${XUI_ACME_EMAIL:-}" ]]; then
        ~/.acme.sh/acme.sh --register-account -m "$XUI_ACME_EMAIL" > /dev/null 2>&1 || { printf 'ACME 账号注册失败，未继续签发。\n' >&2; return 1; }
    fi

    ~/.acme.sh/acme.sh --issue \
        ${domain_args} \
        --standalone \
        --server letsencrypt \
        --certificate-profile shortlived \
        --days 6 \
        --httpport ${WebPort} \
        --force

    if [ $? -ne 0 ]; then
        echo -e "${red}IP 证书签发失败${plain}"
        echo -e "${yellow}请确保端口 ${WebPort} 可从公网访问（或已从公网 80 端口转发）${plain}"
        return 1
    fi

    echo -e "${green}证书签发成功，正在安装……${plain}"

    if ! ~/.acme.sh/acme.sh --installcert --force -d "$ipv4" \
        --key-file "${certDir}/privkey.pem" \
        --fullchain-file "${certDir}/fullchain.pem" \
        --reloadcmd "/usr/local/bin/x-ui cert-permissions && ${reloadCmd}"; then
        printf '证书材料部署或续期重载配置失败。\n' >&2
        return 1
    fi

    if [[ ! -f "${certDir}/fullchain.pem" || ! -f "${certDir}/privkey.pem" ]]; then
        echo -e "${red}安装后找不到证书材料。${plain}"
        return 1
    fi

    echo -e "${green}证书材料已安装${plain}"

    # Configure panel to use the certificate
    echo -e "${green}正在绑定面板证书。${plain}"
    xui_cli cert -webCert "${certDir}/fullchain.pem" -webCertKey "${certDir}/privkey.pem"

    if [ $? -ne 0 ]; then
        echo -e "${yellow}警告：无法自动设置证书路径${plain}"
        echo -e "${yellow}证书文件位于：${plain}"
        echo -e "  证书： ${certDir}/fullchain.pem"
        echo -e "  私钥：${certDir}/privkey.pem"
        return 1
    else
        echo -e "${green}面板证书绑定成功${plain}"
    fi

    echo -e "${green}IP 证书已安装并绑定面板。${plain}"
    echo -e "${green}证书有效期约 6 天，由 acme.sh 定时任务自动续期。${plain}"
    echo -e "${yellow}acme.sh 将在证书到期前自动续期并重载 x-ui。${plain}"
    return 0
}
