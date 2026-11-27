#!/usr/bin/env bash
# Verify the installer dispatch with isolated paths and no host service operations.
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
fixture=$(mktemp -d "$repo/.install-mode-test.XXXXXXXX")
trap 'rm -rf -- "$fixture"' EXIT
mkdir -p "$fixture"/{bin,systemd,scripts,x-ui/bin}
printf 'v3.11.0-zh.7\n' >"$fixture/VERSION"
printf 'panel\n' >"$fixture/x-ui/x-ui"
printf 'core\n' >"$fixture/x-ui/bin/xray-linux-amd64"
(cd "$fixture"; sha256sum x-ui/x-ui x-ui/bin/xray-linux-amd64 >SHA256SUMS)
touch "$fixture/scripts/zh-helpers.sh" "$fixture/scripts/zh-provision.sh"
cat >"$fixture/scripts/zh-migrate.sh" <<'SH'
migrate_existing() {
    printf '迁移入口\n'
    return "${MIGRATION_STATUS:-0}"
}
SH
cat >"$fixture/bin/systemctl" <<'SH'
#!/usr/bin/env bash
case "$*" in
    'show x-ui.service --property=LoadState --value')
        printf '%s\n' "${LOAD_STATE:-}"
        exit "${SHOW_STATUS:-0}" ;;
    'cat x-ui.service') exit "${CAT_STATUS:-1}" ;;
    *) printf '意外的 systemctl 调用：%s\n' "$*" >&2; exit 91 ;;
esac
SH
printf '#!/usr/bin/env bash\ncase $1 in -s) echo Linux;; -m) echo x86_64;; esac\n' >"$fixture/bin/uname"
for tool in flock runuser; do printf '#!/usr/bin/env bash\nexit 0\n' >"$fixture/bin/$tool"; done
chmod +x "$fixture/bin/"*

# Exercise the packaged dispatch and fresh-install guards up to the first action.
awk '/^    if ! command -v openssl / {exit} {print}' "$repo/install.sh" |
    sed -e '/^(( EUID == 0 ))/d' -e "s|/usr/local/x-ui|$fixture/old-app|g" \
        -e "s|/usr/local/bin/x-ui|$fixture/local-cli|g" -e "s|/usr/bin/x-ui|$fixture/upstream-cli|g" \
        -e "s|/etc/x-ui|$fixture/old-db|g" -e "s|/etc/default/x-ui|$fixture/env|g" \
        -e "s|/etc/systemd/system|$fixture/systemd|g" -e "s|/run/systemd/system|$fixture/systemd|g" \
        -e "s|/run/x-ui-zh-lite.lock|$fixture/lock|g" >"$fixture/install.sh"
cat >>"$fixture/install.sh" <<'SH'
    printf '新装入口\n'
else
    printf '更新入口\n'
fi
SH
export PATH="$fixture/bin:$PATH"

check() {
    local expected="$1" text="$2" output status=0
    shift 2
    output=$("$@" 2>&1) || status=$?
    if [[ $status != "$expected" || $output != *"$text"* ]]; then
        printf '安装分流验证失败：%s（退出 %s）\n%s\n' "$text" "$status" "$output" >&2
        return 1
    fi
    [[ $status == 0 || $output != *'新装入口'* ]]
}

check 0 新装入口 env LOAD_STATE=not-found SHOW_STATUS=1 bash "$fixture/install.sh"
check 0 新装入口 env LOAD_STATE=not-found bash "$fixture/install.sh" --port 4567
check 0 迁移入口 env LOAD_STATE=loaded bash "$fixture/install.sh"
check 1 '不能同时指定' env LOAD_STATE=loaded bash "$fixture/install.sh" --postgres
check 17 迁移入口 env LOAD_STATE=loaded MIGRATION_STATUS=17 bash "$fixture/install.sh"
check 1 '服务状态无法安全处理' env LOAD_STATE=masked bash "$fixture/install.sh"
check 1 '无法确认现有 x-ui 服务状态' env SHOW_STATUS=1 bash "$fixture/install.sh"
check 1 '发现已有 x-ui 服务' env LOAD_STATE=not-found CAT_STATUS=0 bash "$fixture/install.sh"
mkdir "$fixture/old-db" "$fixture/old-app"
check 1 '发现已有面板或保留的数据' env LOAD_STATE=not-found bash "$fixture/install.sh"
touch "$fixture/old-app/.zh-lite-version"
check 1 '发现已有中文版' env LOAD_STATE=loaded bash "$fixture/install.sh"
mv "$fixture/old-app/.zh-lite-version" "$fixture/old-app/.zh-lite-migration"
check 1 '发现已有中文版' env LOAD_STATE=loaded bash "$fixture/install.sh"
check 0 迁移入口 env SHOW_STATUS=1 bash "$fixture/install.sh" --migrate
check 0 迁移入口 env SHOW_STATUS=1 bash "$fixture/install.sh" --update
mv "$fixture/old-app/.zh-lite-migration" "$fixture/old-app/.zh-lite-version"
check 0 更新入口 env SHOW_STATUS=1 bash "$fixture/install.sh" --update
if ln -s "$fixture/missing" "$fixture/old-app/.zh-lite-migration" 2>/dev/null &&
   [[ -L $fixture/old-app/.zh-lite-migration ]]; then
    mv "$fixture/old-app/.zh-lite-version" "$fixture/previous-version"
    check 1 '发现已有中文版' env LOAD_STATE=loaded bash "$fixture/install.sh"
fi
printf '通过安装自动分流、恰当拒绝与显式更新入口验证。\n'
