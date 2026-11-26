#!/usr/bin/env bash
# Filesystem transaction regression tests. Uses isolated fixtures, never host services.
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
test_root=$(mktemp -d "$repo/.migration-test.XXXXXXXX")
trap 'rm -rf -- "$test_root"' EXIT
original_path=$PATH
mkdir "$test_root/mocks"
cat > "$test_root/mocks/systemctl" <<'SH'
#!/usr/bin/env bash
case $1 in
    stop) printf 'stop\n' >> "$fixture/stops"; printf 0 > "$fixture/active" ;;
    start)
        if [[ -f $fixture/fail-start && ! -f $fixture/start-failed ]]; then
            touch "$fixture/start-failed"
            printf mutated > "$fixture/db/x-ui.db"
            exit 1
        fi
        printf 1 > "$fixture/active" ;;
    show)
        case ${*: -1} in
            --property=MainPID|--value) # Match the requested property without depending on option order.
                if [[ $* == *MainPID* ]]; then printf 0
                elif [[ $* == *SubState* ]]; then printf running; fi ;;
        esac ;;
    is-active) [[ $(<"$fixture/active") == 1 ]] ;;
    cat) printf 'original systemd unit\n' ;;
    daemon-reload) : ;;
    *) exit 1 ;;
esac
SH
cat > "$test_root/mocks/python3" <<'SH'
#!/usr/bin/env bash
[[ ${1:-} != -I ]] || shift
if [[ $1 == -c ]]; then
    case $2 in
        *database*) printf '%s\n' "$fixture/db" ;;
        *was_active*) cat "$fixture/initial-active" ;;
        *cli_existed*) printf 1 ;;
        *config_files*) : ;;
        *source_version*) if [[ $fixture == *legacy* ]]; then printf 2.9.2; else printf 3.11.0; fi ;;
        *legacy_upgrade*) if [[ $fixture == *legacy* ]]; then printf 1; else printf 0; fi ;;
        *needs_workdir*) if [[ $fixture == *no-workdir* ]]; then printf 1; else printf 0; fi ;;
        *) exit 1 ;;
    esac
else
    cat >/dev/null
    [[ ! -f $fixture/fail-integrity ]]
fi
SH
cat > "$test_root/mocks/install" <<'SH'
#!/usr/bin/env bash
while [[ $1 == -* ]]; do shift 2; done
cp -- "$1" "$2"
SH
cat > "$test_root/mocks/chown" <<'SH'
#!/usr/bin/env bash
exit 0
SH
cat > "$test_root/mocks/sleep" <<'SH'
#!/usr/bin/env bash
exit 0
SH
chmod +x "$test_root/mocks/"*
cp "$test_root/mocks/sleep" "$test_root/mocks/setpriv"

run_case() (
    export fixture=$test_root/$1
    mkdir -p "$fixture/local/x-ui/bin/nested" "$fixture/db" "$fixture/package/x-ui/bin" "$fixture/package/scripts" "$fixture/local/bin" "$fixture/systemd/system/x-ui.service.d"
    app=$fixture/local/x-ui
    command_path=$fixture/local/bin/x-ui
    package=$fixture/package
    version=v3.11.0-zh.3
    printf old-binary > "$app/x-ui"
    printf custom-geodata > "$app/bin/geoip.dat"
    printf old-core > "$app/bin/xray-linux-amd64"
    printf custom-helper > "$app/bin/mtg-linux-amd64"
    printf custom-config > "$app/bin/nested/custom.json"
    printf original-data > "$fixture/db/x-ui.db"
    printf original-wal > "$fixture/db/x-ui.db-wal"
    printf original-shm > "$fixture/db/x-ui.db-shm"
    printf original-cli > "$command_path"
    printf upstream-original-cli > "$fixture/local/upstream-cli"
    printf new-binary > "$package/x-ui/x-ui"
    printf new-core > "$package/x-ui/bin/xray-linux-amd64"
    printf bundled-geodata > "$package/x-ui/bin/geoip.dat"
    printf bundled-helper > "$package/x-ui/bin/mtg-linux-amd64"
    for path in install-online.sh x-ui.sh scripts/zh-ops.sh scripts/zh-helpers.sh scripts/zh-update-request.sh scripts/zh-migrate.sh; do
        printf 'new-tool\n' > "$package/$path"
    done
    printf '%s' "$2" > "$fixture/active"
    printf '%s' "$2" > "$fixture/initial-active"
    [[ $1 != *rollback ]] || touch "$fixture/fail-start"
    [[ $1 != integrity ]] || touch "$fixture/fail-integrity"
    # Rewrite the staging prefix in an isolated copy. No production test mode or root override exists.
    sed -e "s|/usr/local/|$fixture/local/|g" -e "s|/usr/bin/x-ui|$fixture/local/upstream-cli|g" -e "s|/etc/systemd/|$fixture/systemd/|g" "$repo/scripts/zh-migrate.sh" > "$fixture/migrate.sh"
    source "$fixture/migrate.sh"
    migration_inspect() { printf '{"fixture":true}\n'; }
    die() { printf '%s\n' "$*" >&2; exit 1; }
    export PATH=$test_root/mocks:$original_path
    export app command_path package version
    if bash -euo pipefail -c 'source "$fixture/migrate.sh"; migration_inspect() { printf "{}\n"; }; die() { printf "%s\n" "$*" >&2; exit 1; }; migrate_existing' > "$fixture/output" 2>&1; then result=0; else result=$?; fi
    if [[ $result != 0 && $1 != *rollback && $1 != integrity ]]; then cat "$fixture/output" >&2; fi
    if [[ $1 == *rollback || $1 == integrity ]]; then
        [[ $result != 0 ]]
        [[ $(<"$app/x-ui") == old-binary ]]
        [[ $(<"$fixture/db/x-ui.db") == original-data ]]
        [[ $(<"$fixture/db/x-ui.db-wal") == original-wal ]]
        [[ $(<"$fixture/db/x-ui.db-shm") == original-shm ]]
        [[ $(<"$command_path") == original-cli ]]
        [[ $(<"$fixture/local/upstream-cli") == upstream-original-cli ]]
        [[ $(<"$app/bin/xray-linux-amd64") == old-core ]]
    else
        [[ $result == 0 ]]
        [[ $(<"$app/x-ui") == new-binary ]]
        [[ $(<"$fixture/db/x-ui.db") == original-data ]]
        [[ $(<"$app/.zh-lite-migration") == sqlite-preserved-v1 ]]
        [[ $(<"$fixture/local/upstream-cli") == new-tool ]]
        if [[ $1 == legacy* ]]; then [[ $(<"$app/bin/xray-linux-amd64") == new-core ]]; else [[ $(<"$app/bin/xray-linux-amd64") == old-core ]]; fi
        # Same release can be re-run without losing user data or custom resources.
        stops_before=$(wc -l < "$fixture/stops")
        bash -euo pipefail -c 'source "$fixture/migrate.sh"; migration_inspect() { printf "{}\n"; }; die() { printf "%s\n" "$*" >&2; exit 1; }; migrate_existing' >> "$fixture/output" 2>&1
        [[ $(wc -l < "$fixture/stops") == "$stops_before" ]]
        if [[ $1 == legacy* ]]; then [[ ! -e $fixture/db/x-ui.db-wal ]]; else [[ $(<"$fixture/db/x-ui.db-wal") == original-wal ]]; fi
    fi
    [[ $(<"$app/bin/geoip.dat") == custom-geodata ]]
    [[ $(<"$app/bin/mtg-linux-amd64") == custom-helper ]]
    [[ $(<"$app/bin/nested/custom.json") == custom-config ]]
    [[ $(<"$fixture/active") == "$2" ]]
    if [[ $1 == *no-workdir* ]]; then
        if [[ $1 == *rollback ]]; then [[ ! -e $fixture/systemd/system/x-ui.service.d/90-zh-working-directory.conf ]];
        else [[ -f $fixture/systemd/system/x-ui.service.d/90-zh-working-directory.conf ]]; fi
    fi
    printf 'PASS %s\n' "$1"
)

run_case active 1
run_case inactive 0
run_case rollback 1
run_case integrity 1
run_case legacy-active 1
run_case legacy-no-workdir 1
run_case legacy-no-workdir-rollback 1

# Check argument parsing before platform/root preflight. A valid --port followed by
# another flag must reach platform checks, whereas incompatible modes fail in parsing.
for args in '--port 2053 --fail2ban' '--fail2ban --port 2053'; do
    output=$(bash "$repo/install.sh" $args 2>&1 || true)
    [[ $output != *'端口须为'* ]]
done
for args in '--migrate --update' '--update --migrate'; do
    output=$(bash "$repo/install.sh" $args 2>&1 || true)
    [[ $output == *'不能同时使用'* ]]
done
printf 'PASS argument order and mode conflict\n'

(
    fixture=$test_root/update-routing
    mkdir -p "$fixture/package" "$fixture/app"
    sed "s|/usr/local/x-ui/|$fixture/app/|g" "$repo/update.sh" > "$fixture/package/update.sh"
    cat > "$fixture/package/install.sh" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$@"
SH
    [[ $(bash "$fixture/package/update.sh") == --update ]]
    touch "$fixture/app/.zh-lite-migration"
    [[ $(bash "$fixture/package/update.sh") == --migrate ]]
    sed -e "s|app=/usr/local/x-ui|app=$fixture/app|" -e '/^if \[\[ $mode == migrate &&/,$d' "$repo/install.sh" > "$fixture/parse.sh"
    printf '\nprintf "%%s\\n" "$mode"\n' >> "$fixture/parse.sh"
    [[ $(bash "$fixture/parse.sh" --update) == migrate ]]
    printf 'PASS direct offline and installed update routing\n'
)

# Management backup must honor a relocated database, and restart before gzip.
(
    export fixture=$test_root/management-backup
    mkdir -p "$fixture/app" "$fixture/custom-data" "$fixture/mocks"
    printf original-db > "$fixture/custom-data/x-ui.db"
    printf original-wal > "$fixture/custom-data/x-ui.db-wal"
    printf 1 > "$fixture/active"
    cat > "$fixture/app/zh-helpers.sh" <<'SH'
xui_load_paths() { xui_db_folder=$fixture/custom-data; xui_db_kind=sqlite; }
SH
    sed '/^command=${1:-}/,$d' "$repo/x-ui.sh" > "$fixture/commands.sh"
    source "$fixture/commands.sh"
    app=$fixture/app
    command -v cp > "$fixture/cp-path"
    command -v tar > "$fixture/tar-path"
    cat > "$fixture/mocks/systemctl" <<'SH'
#!/usr/bin/env bash
case $1 in
    is-active) [[ $(<"$fixture/active") == 1 ]] ;;
    stop) printf 'stop\n' >> "$fixture/order"; printf 0 > "$fixture/active" ;;
    start) printf 'start\n' >> "$fixture/order"; printf 1 > "$fixture/active" ;;
    show) printf 0 ;;
    *) exit 1 ;;
esac
SH
    cat > "$fixture/mocks/cp" <<'SH'
#!/usr/bin/env bash
printf 'copy\n' >> "$fixture/order"
exec "$(<"$fixture/cp-path")" "$@"
SH
    cat > "$fixture/mocks/tar" <<'SH'
#!/usr/bin/env bash
[[ $(<"$fixture/active") == 1 ]] || exit 1
printf 'compress\n' >> "$fixture/order"
exec "$(<"$fixture/tar-path")" "$@"
SH
    chmod +x "$fixture/mocks/"*
    export PATH=$fixture/mocks:$original_path
    backup "$fixture/preserved.tar.gz" > "$fixture/output"
    [[ $(<"$fixture/order") == $'stop\ncopy\nstart\ncompress' ]]
    mkdir "$fixture/restored"
    "$(<"$fixture/tar-path")" -xzf "$fixture/preserved.tar.gz" -C "$fixture/restored"
    [[ $(<"$fixture/restored/custom-data/x-ui.db") == original-db ]]
    [[ $(<"$fixture/restored/custom-data/x-ui.db-wal") == original-wal ]]
    printf 'PASS relocated management backup and short downtime\n'
)

(
    fixture=$test_root/root-certificates
    mkdir -p "$fixture"
    printf certificate > "$fixture/original-cert.pem"
    printf private-key > "$fixture/original-key.pem"
    source "$repo/scripts/zh-helpers.sh"
    xui_migrated() { return 0; }
    xui_runtime() { printf '{}\n'; }
    cert_permissions() { return 0; }
    openssl() { printf 'matching-public-key\n'; }
    runuser() { printf 'unexpected service account switch\n' >&2; return 1; }
    xui_service_exec() {
        case $1 in
            cert) printf bound > "$fixture/result" ;;
            setting) printf 'cert: %s\nkey: %s\n' "$fixture/original-cert.pem" "$fixture/original-key.pem" ;;
            *) return 1 ;;
        esac
    }
    # xui_cli normally runs in app; rewrite only that cd in an isolated function copy.
    definition=$(declare -f xui_cli)
    definition=${definition/'cd /usr/local/x-ui'/'cd "$fixture"'}
    eval "$definition"
    xui_cli cert -webCert "$fixture/original-cert.pem" -webCertKey "$fixture/original-key.pem"
    [[ $(<"$fixture/result") == bound ]]
    [[ $(<"$fixture/original-key.pem") == private-key ]]
    printf 'PASS original root certificate identity and paths\n'
)
