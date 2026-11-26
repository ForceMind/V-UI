#!/usr/bin/env bash
# Sourced only after install.sh has verified the complete release and acquired its lock.
# Existing units, service credentials and configuration remain authoritative.

migration_inspect() {
    python3 -I - "$app" "${1:-migrate}" <<'PY'
import json, os, pathlib, pwd, re, shlex, stat, subprocess, sys

app = pathlib.Path(sys.argv[1])
runtime = len(sys.argv) > 2 and sys.argv[2] == 'runtime'
def reject(message):
    sys.exit('迁移预检失败：' + message)
def show(name):
    return subprocess.check_output(['systemctl', 'show', 'x-ui.service', '--property='+name, '--value'], text=True).strip()
def trusted(path, directory=False):
    p = pathlib.Path(path)
    for item in [p, *p.parents]:
        st = item.lstat()
        if stat.S_ISLNK(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
            reject('路径必须由 root 独占管理且不能经过符号链接：' + str(item))
    if directory and not p.is_dir():
        reject('目录不存在：' + str(p))
    return p

try:
    if show('LoadState') != 'loaded' or show('User') not in ('', 'root') or show('Group') not in ('', 'root'):
        reject('仅接管已加载、使用 root 身份的原版 systemd 服务；不会自动更改现有身份。')
    for prop in ('RootDirectory', 'RootImage', 'BindPaths', 'BindReadOnlyPaths', 'TemporaryFileSystem', 'ExecStartPre', 'ExecStartPost', 'ExecStop', 'ExecStopPost', 'EnvironmentDirectories', 'PAMName'):
        if show(prop): reject('存在尚未支持的数据映射或服务钩子：' + prop)
    if show('DynamicUser') == 'yes' or show('RootDirectoryStartOnly') == 'yes':
        reject('不支持动态用户或隔离根目录。')
    start = show('ExecStart')
    if not re.fullmatch(r'\{ path=/usr/local/x-ui/x-ui ; argv\[\]=/usr/local/x-ui/x-ui(?: run)? ; ignore_errors=no ; .*\}', start):
        reject('ExecStart 不是支持的原版二进制直接启动布局。')
    trusted(app, True)
    executable = trusted(app/'x-ui')
    if not executable.is_file() or executable.stat().st_nlink != 1:
        reject('面板程序必须是独立普通文件。')
    marker = app/'.zh-lite-migration'
    observed = ''
    if not runtime:
        nobody = pwd.getpwnam('nobody')
        observed = subprocess.run(['setpriv', '--reuid='+str(nobody.pw_uid), '--regid='+str(nobody.pw_gid), '--clear-groups', '--no-new-privs', 'env', '-i', 'PATH=/usr/bin:/bin', str(app/'x-ui'), '-v'], cwd='/', text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10, check=True).stdout.strip()
        if observed.startswith('v'): observed = observed[1:]
    source_version = observed
    if marker.exists():
        origin = app/'.zh-lite-source-version'
        if origin.exists() or origin.is_symlink():
            trusted(origin)
            source_version = origin.read_text().strip()
            if source_version not in ('2.9.2', '3.11.0'): reject('接管来源版本标记无效。')
        else: source_version = '3.11.0'
    working_directory = show('WorkingDirectory').rstrip('/')
    legacy_no_workdir = not working_directory and source_version == '2.9.2'
    if working_directory != str(app) and not legacy_no_workdir:
        reject('仅支持标准程序目录；只有原版 2.9.2 允许缺少 WorkingDirectory。')
    effective_workdir = working_directory or '/'
    fragment = trusted(show('FragmentPath'))
    # Do not evaluate unit files, shell snippets, or EnvironmentFile as shell code.
    env = {}
    def assignments(values):
        for value in values:
            if '=' not in value: reject('环境变量格式无法安全解析。')
            key, value = value.split('=', 1)
            if not re.fullmatch('[A-Za-z_][A-Za-z0-9_]*', key) or any(c in value for c in '\\%\n\r'):
                reject('环境变量使用尚未支持的转义或展开。')
            env[key] = value
    assignments(shlex.split(show('Environment')))
    envfiles = show('EnvironmentFiles')
    # systemctl prints one structured record per file, including duplicates from
    # unit/drop-ins. Records may be newline- or whitespace-separated. Consume
    # every byte; never extract valid-looking substrings from malformed output.
    entry_pattern = re.compile(r'''\s*("/[-A-Za-z0-9_./ ]+"|'/[-A-Za-z0-9_./ ]+'|/[-A-Za-z0-9_./ ]+?)\s+\(\s*ignore_errors\s*=\s*(yes|no)\s*\)(?=\s|$)''')
    entries = []
    offset = 0
    while offset < len(envfiles):
        if not envfiles[offset:].strip(): break
        match = entry_pattern.match(envfiles, offset)
        if match is None: reject('EnvironmentFiles 布局无法安全解析。')
        token = match.group(1)
        values = shlex.split(token) if token.startswith(('"', "'")) else [token]
        if len(values) != 1: reject('EnvironmentFiles 路径无法安全解析。')
        filename = values[0]
        if '..' in pathlib.Path(filename).parts or str(pathlib.Path(filename)) != filename:
            reject('EnvironmentFiles 路径必须是规范绝对路径。')
        entries.append((filename, match.group(2)))
        offset = match.end()
    for filename, optional in entries:
        p = pathlib.Path(filename)
        if not p.exists() and optional == 'yes': continue
        trusted(p)
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith(('#', ';')): continue
            values = shlex.split(line)
            if len(values) != 1: reject('EnvironmentFile 包含多行、空格或复杂语法。')
            assignments(values)
    if show('PassEnvironment') or show('UnsetEnvironment'):
        reject('暂不支持继承或删除 systemd 管理器环境。')
    active = show('ActiveState')
    if active not in ('active', 'inactive', 'failed') or (active == 'active' and show('SubState') != 'running'):
        reject('服务正在切换状态；请等待稳定后重试。')
    if active == 'active':
        pid = int(show('MainPID'))
        if os.path.realpath(f'/proc/{pid}/exe') != str(app/'x-ui') or os.path.realpath(f'/proc/{pid}/cwd') != effective_workdir:
            reject('运行中的服务程序或工作目录与单元不一致。')
        actual = dict(item.decode().split('=', 1) for item in pathlib.Path(f'/proc/{pid}/environ').read_bytes().split(b'\0') if b'=' in item)
        for key in set(env) | set(actual):
            if (key.startswith('XUI_') or key == 'NODE_TOKEN_ENCRYPTION') and env.get(key, '') != actual.get(key, ''):
                reject('服务当前环境与单元配置不一致，请先核对：' + key)
    # main.loadServiceEnvFile runs before argument parsing, then runWebServer loads
    # WorkingDirectory/.env. godotenv fills only keys absent from the process env.
    # Accept a literal subset of dotenv; expansion/escape syntax fails closed.
    extra_configs = []
    def load_dotenv(p):
        trusted(p)
        extra_configs.append(str(p))
        additions = {}
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#'): continue
            if any(c in line for c in '$\\#%'):
                reject('dotenv 使用尚未支持的展开或转义：' + str(p))
            values = shlex.split(line)
            if len(values) != 1 or '=' not in values[0]: reject('dotenv 语法无法安全解析：' + str(p))
            key, value = values[0].split('=', 1)
            if not re.fullmatch('[A-Za-z_][A-Za-z0-9_]*', key): reject('dotenv 变量名不受支持。')
            additions[key] = value
        for key, value in additions.items(): env.setdefault(key, value)
    legacy_original = not marker.exists() and observed == '2.9.2'
    service_env = dict(env)
    for name in ('/etc/default/x-ui', '/etc/conf.d/x-ui', '/etc/sysconfig/x-ui'):
        p = pathlib.Path(name)
        if p.exists() or p.is_symlink():
            load_dotenv(p)
            break
    if legacy_original:
        # 2.9.2 does not load these files in Go. Reject any newly effective key
        # before 3.x can silently switch its DB, token encryption or paths.
        if any((key.startswith('XUI_') or key == 'NODE_TOKEN_ENCRYPTION') and service_env.get(key) != value for key, value in env.items()):
            reject('旧服务未采用的服务环境文件会改变 3.x 配置，请先人工消除冲突。')
        env = service_env
    dotenv = pathlib.Path(effective_workdir)/'.env'
    if dotenv.exists() or dotenv.is_symlink(): load_dotenv(dotenv)
    if legacy_no_workdir:
        if (app/'.env').exists() or (app/'.env').is_symlink() or pathlib.Path('/.env').exists():
            reject('缺少工作目录的旧服务存在 .env，不能安全改变相对路径。')
        if env.get('XUI_BIN_FOLDER', '') not in ('', 'bin', str(app/'bin')):
            reject('缺少工作目录的旧服务使用自定义 bin，拒绝改变资源解析位置。')
    if legacy_original and env.get('XUI_BIN_FOLDER', '') not in ('', 'bin', str(app/'bin')):
        reject('2.9.2 核心升级仅支持标准 bin；不会覆盖外部自定义执行文件。')
    if legacy_original:
        trusted(app/'bin', True)
        core = trusted(app/'bin/xray-linux-amd64')
        if not core.is_file() or core.stat().st_nlink != 1: reject('旧核心必须是独立普通文件。')
    workdir_dropin = pathlib.Path('/etc/systemd/system/x-ui.service.d/90-zh-working-directory.conf')
    if legacy_no_workdir and not runtime:
        trusted('/etc/systemd/system', True)
        if workdir_dropin.exists() or workdir_dropin.is_symlink(): reject('工作目录补丁已存在但未生效，请先核对服务配置。')
        if workdir_dropin.parent.exists(): trusted(workdir_dropin.parent, True)
    kind = env.get('XUI_DB_TYPE', 'sqlite').strip().lower()
    if kind in ('postgres', 'postgresql', 'pg') and not runtime:
        reject('PostgreSQL 可能有外部写入者，无法保证失败后无损恢复；本脚本拒绝写入，请先安排独占备份及恢复方案。')
    if kind not in ('sqlite', '', 'postgres', 'postgresql', 'pg') or (kind in ('sqlite', '') and env.get('XUI_DB_DSN', '')):
        reject('无法确认数据库后端。')
    db = env.get('XUI_DB_FOLDER') or '/etc/x-ui'
    if not re.fullmatch('/[-A-Za-z0-9_./]+', db) or '..' in pathlib.Path(db).parts or str(pathlib.Path(db)) != db:
        reject('数据库目录必须是规范绝对路径。')
    if db == str(app) or db.startswith(str(app)+'/') or str(app).startswith(db+'/'):
        reject('数据库与程序目录不能互相包含。')
    if db in ('/', '/etc', '/var', '/var/lib', '/var/log', '/usr', '/usr/local', '/opt', '/home', '/root', '/tmp', '/run', '/srv', '/mnt', '/media'):
        reject('数据库不能直接放在系统公共目录。')
    trusted(db, True)
    if kind in ('sqlite', ''):
        database = trusted(pathlib.Path(db)/'x-ui.db')
        if not database.is_file() or database.stat().st_nlink != 1:
            reject('数据库必须是独立普通文件，不能使用硬链接。')
        for suffix in ('-wal', '-shm', '-journal'):
            sidecar = pathlib.Path(str(database)+suffix)
            if sidecar.exists() or sidecar.is_symlink():
                trusted(sidecar)
                if not sidecar.is_file() or sidecar.stat().st_nlink != 1:
                    reject('SQLite 附属文件必须是独立普通文件。')
        with database.open('rb') as stream:
            if stream.read(16) != b'SQLite format 3\0': reject('实际数据库不是 SQLite，拒绝初始化空库。')
    if marker.exists() or marker.is_symlink():
        trusted(marker)
        if marker.read_text() != 'sqlite-preserved-v1\n': reject('接管标记不受支持。')
    elif (app/'.zh-lite-version').exists():
        reject('检测到新装版；请使用 update.sh。')
    elif runtime:
        reject('缺少可信接管标记。')
    # -v returns before InitDB. Drop all privileges and environment for this read-only check.
    if not runtime:
        if marker.exists():
            previous = (app/'.zh-lite-version').read_text().strip()
            if not re.fullmatch(r'v3\.11\.0-zh\.[1-9][0-9]*', previous) or observed != previous[1:]:
                reject('接管后的面板二进制与版本标记不一致。')
        elif observed not in ('2.9.2', '3.11.0'): reject('仅支持原版 v2.9.2 与 v3.11.0，不支持其他版本或定制版本。')
    cli = pathlib.Path('/usr/local/bin/x-ui')
    trusted(cli.parent, True)
    def cli_trusted(p):
        if not p.is_symlink(): return trusted(p)
        trusted(p.parent, True)
        if p.lstat().st_uid != 0: reject('命令符号链接必须属于 root。')
        target = pathlib.Path(os.path.realpath(str(p)))
        if str(target) not in (str(app/'x-ui.sh'), '/usr/local/bin/x-ui', '/usr/bin/x-ui') or str(target) == str(p):
            reject('命令符号链接目标不受支持。')
        trusted(target)
    if cli.exists() or cli.is_symlink(): cli_trusted(cli)
    upstream_cli = pathlib.Path('/usr/bin/x-ui')
    if upstream_cli.exists() or upstream_cli.is_symlink(): cli_trusted(upstream_cli)
    dropins = show('DropInPaths').split()
    for dropin in dropins: trusted(dropin)
    if not runtime and pathlib.Path('/etc/systemd/system/x-ui-zh-update.path').exists():
        reject('存在其他中文更新监听，无法保证迁移互斥。')
    state = {'environment': {k:v for k,v in env.items() if k.startswith('XUI_') or k == 'NODE_TOKEN_ENCRYPTION'}, 'database': db, 'database_kind': 'postgres' if kind in ('postgres', 'postgresql', 'pg') else 'sqlite', 'was_active': active == 'active', 'unit': str(fragment), 'cli_existed': cli.exists(), 'upstream_cli_existed': upstream_cli.exists(), 'config_files': sorted(set([str(fragment), *dropins, *extra_configs, *(p for p, _ in entries if pathlib.Path(p).exists())]))}
    state.update(source_version=source_version, legacy_upgrade=legacy_original, needs_workdir=legacy_no_workdir)
    if runtime: state['service_environment'] = env
    print(json.dumps(state, ensure_ascii=True))
except (OSError, ValueError, subprocess.SubprocessError) as error:
    reject('无法可靠读取服务或原安装：' + type(error).__name__)
PY
}

migrate_existing() (
    local program info config_files source_version legacy_upgrade needs_workdir stage backup db_dir was_active cli_existed upstream_cli_existed changed=0 committed=0 restored=1 database_dirty=0 cli_changed=0 upstream_cli_changed=0 unit_modified=0
    local workdir_dropin=/etc/systemd/system/x-ui.service.d/90-zh-working-directory.conf
    for program in python3 setpriv cp mv; do
        command -v "$program" >/dev/null || die "迁移需要 $program，请先安装此依赖。"
    done
    info=$(migration_inspect) || die '原安装预检未通过；未停止或替换服务。'
    # Environment snapshots can contain encryption keys; never put them in process argv.
    db_dir=$(python3 -I -c 'import json,sys; print(json.load(sys.stdin)["database"])' <<< "$info")
    was_active=$(python3 -I -c 'import json,sys; print(int(json.load(sys.stdin)["was_active"]))' <<< "$info")
    cli_existed=$(python3 -I -c 'import json,sys; print(int(json.load(sys.stdin)["cli_existed"]))' <<< "$info")
    upstream_cli_existed=$(python3 -I -c 'import json,sys; print(int(json.load(sys.stdin)["upstream_cli_existed"]))' <<< "$info")
    config_files=$(python3 -I -c 'import json,sys; print("\n".join(json.load(sys.stdin)["config_files"]))' <<< "$info")
    source_version=$(python3 -I -c 'import json,sys; print(json.load(sys.stdin)["source_version"])' <<< "$info")
    legacy_upgrade=$(python3 -I -c 'import json,sys; print(int(json.load(sys.stdin)["legacy_upgrade"]))' <<< "$info")
    needs_workdir=$(python3 -I -c 'import json,sys; print(int(json.load(sys.stdin)["needs_workdir"]))' <<< "$info")
    if [[ -f $app/.zh-lite-version ]]; then
        local previous
        previous=$(<"$app/.zh-lite-version")
        [[ $(printf '%s\n' "$previous" "$version" | sort -V | tail -n 1) == "$version" ]] || die '迁移更新拒绝降级。'
        if [[ $previous == "$version" ]] && cmp -s "$app/x-ui" "$package/x-ui/x-ui" \
            && cmp -s "$app/install-online.sh" "$package/install-online.sh" \
            && cmp -s "$app/zh-ops.sh" "$package/scripts/zh-ops.sh" \
            && cmp -s "$app/zh-helpers.sh" "$package/scripts/zh-helpers.sh" \
            && cmp -s "$app/zh-migrate.sh" "$package/scripts/zh-migrate.sh" \
            && cmp -s "$app/zh-update-request.sh" "$package/scripts/zh-update-request.sh" \
            && cmp -s "$command_path" "$package/x-ui.sh" \
            && { (( ! upstream_cli_existed )) || cmp -s /usr/bin/x-ui "$package/x-ui.sh"; }; then
            printf '已是 %s，程序与运维文件一致；未停服或重复升级数据库。\n' "$version"
            return 0
        fi
    fi
    # Root-only stage; never consume a database-controlled update request as root.
    stage=$(mktemp -d /usr/local/.x-ui-migrate.XXXXXXXX)
    chmod 0700 "$stage"
    backup=$stage/backup
    mkdir "$backup"
    printf '%s\n' "$info" > "$backup/state.json"
    rollback_migration() {
        local code=$?
        trap - EXIT
        if (( ! committed && changed )); then
            printf '迁移失败，正在恢复原程序、数据库和运行状态。\n' >&2
            set +e
            if ! systemctl stop x-ui.service || [[ $(systemctl show x-ui.service --property=MainPID --value) != 0 ]]; then
                printf '无法确认服务已停止，未覆盖运行中的程序或数据库；请人工恢复：%s\n' "$stage" >&2
                exit "$code"
            fi
            if [[ -d $stage/original-app ]]; then
                [[ ! -e $app ]] || mv -T -- "$app" "$stage/failed-app" || restored=0
                mv -T -- "$stage/original-app" "$app" || restored=0
            fi
            if (( database_dirty )); then
                mkdir "$stage/failed-database" || restored=0
                local suffix
                for suffix in '' -wal -shm -journal; do
                    if [[ -e $db_dir/x-ui.db$suffix || -L $db_dir/x-ui.db$suffix ]]; then
                        mv -- "$db_dir/x-ui.db$suffix" "$stage/failed-database/" || restored=0
                    fi
                    if [[ -f $backup/database/x-ui.db$suffix ]]; then
                        cp -a -- "$backup/database/x-ui.db$suffix" "$db_dir/" || restored=0
                    fi
                done
            fi
            if (( cli_changed && cli_existed )); then
                rm -f -- "$command_path"
                cp -a -- "$backup/cli" "$command_path" || restored=0
            elif (( cli_changed )); then
                rm -f -- "$command_path" || restored=0
            fi
            if (( upstream_cli_changed )); then rm -f -- /usr/bin/x-ui; cp -a -- "$backup/upstream-cli" /usr/bin/x-ui || restored=0; fi
            if (( unit_modified )); then rm -f -- "$workdir_dropin" || restored=0; systemctl daemon-reload || restored=0; fi
            if (( was_active )); then systemctl start x-ui.service || restored=0; fi
            if (( restored )); then printf '已恢复原安装及运行状态。\n' >&2
            else printf '自动恢复未完全成功，请保留备份并人工恢复：%s\n' "$stage" >&2; fi
        fi
        printf '迁移备份与本轮记录保留在：%s\n' "$stage" >&2
        exit "$code"
    }
    trap rollback_migration EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    # First and only live-state mutation before the snapshots is stopping the panel.
    changed=1
    systemctl stop x-ui.service
    [[ $(systemctl show x-ui.service --property=MainPID --value) == 0 ]] || die '停服后仍有主进程，已取消迁移。'
    python3 -I - "$db_dir" <<'PY'
import os, pathlib, stat, sys
directory = pathlib.Path(sys.argv[1])
identities = set()
for suffix in ('', '-wal', '-shm', '-journal'):
    path = directory / ('x-ui.db' + suffix)
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or info.st_mode & 0o022:
            sys.exit('停服后数据库文件类型或权限发生变化，拒绝继续。')
        identities.add((info.st_dev, info.st_ino))
for process in pathlib.Path('/proc').iterdir():
    if not process.name.isdecimal(): continue
    try:
        for fd in (process/'fd').iterdir():
            try:
                info = fd.stat()
                if (info.st_dev, info.st_ino) in identities:
                    sys.exit('停服后仍有进程占用 SQLite 文件，拒绝迁移。')
            except FileNotFoundError: pass
    except FileNotFoundError: pass
PY
    mkdir "$backup/database"
    local suffix
    for suffix in '' -wal -shm -journal; do
        if [[ -f $db_dir/x-ui.db$suffix ]]; then cp -a -- "$db_dir/x-ui.db$suffix" "$backup/database/"; fi
    done
    cp -a -- "$app" "$backup/app"
    if (( cli_existed )); then cp -a -- "$command_path" "$backup/cli"; fi
    if (( upstream_cli_existed )); then cp -a -- /usr/bin/x-ui "$backup/upstream-cli"; fi
    mkdir "$backup/configuration"
    while IFS= read -r program; do
        [[ -z $program ]] || cp -a --parents -- "$program" "$backup/configuration/"
    done <<< "$config_files"
    systemctl cat x-ui.service > "$backup/systemd-unit.txt"
    # Integrity check only on an expendable copy, including WAL/SHM; original bytes stay untouched.
    cp -a -- "$backup/database" "$stage/check-database"
    python3 -I - "$stage/check-database/x-ui.db" <<'PY'
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
if db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
    sys.exit('SQLite 完整性检查失败。')
if not db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
    sys.exit('数据库没有原账号，拒绝初始化新账号。')
db.close()
PY
    if (( legacy_upgrade )); then migration_upgrade_check "$stage" "$package/x-ui/x-ui" || die '2.9.2 数据升级校验未通过；未替换原程序和数据库。'; fi
    cp -a -- "$app" "$stage/program"
    # Preserve every original resource; replace only the verified release executable and Chinese tools.
    rm -f -- "$stage/program/install-online.sh" "$stage/program/zh-ops.sh" "$stage/program/zh-helpers.sh" "$stage/program/zh-update-request.sh" "$stage/program/zh-migrate.sh" \
        "$stage/program/.zh-lite-migration" "$stage/program/.zh-lite-version" "$stage/program/.zh-lite-migration-env.json" "$stage/program/.zh-lite-source-version"
    install -m 0755 -o root -g root "$package/x-ui/x-ui" "$stage/program/x-ui"
    install -m 0755 -o root -g root "$package/install-online.sh" "$stage/program/install-online.sh"
    install -m 0755 -o root -g root "$package/scripts/zh-ops.sh" "$stage/program/zh-ops.sh"
    install -m 0644 -o root -g root "$package/scripts/zh-helpers.sh" "$stage/program/zh-helpers.sh"
    install -m 0755 -o root -g root "$package/scripts/zh-update-request.sh" "$stage/program/zh-update-request.sh"
    install -m 0644 -o root -g root "$package/scripts/zh-migrate.sh" "$stage/program/zh-migrate.sh"
    if (( legacy_upgrade )); then
        rm -f -- "$stage/program/bin/xray-linux-amd64"
        install -m 0755 -o root -g root "$package/x-ui/bin/xray-linux-amd64" "$stage/program/bin/xray-linux-amd64"
        # Optional helpers are added only when absent; geodata and custom files win.
        for program in "$package/x-ui/bin/"*; do
            [[ -f $program && ! -L $program ]] || continue
            [[ ! -e $stage/program/bin/${program##*/} && ! -L $stage/program/bin/${program##*/} ]] || continue
            cp -a -- "$program" "$stage/program/bin/"
        done
    fi
    printf 'sqlite-preserved-v1\n' > "$stage/program/.zh-lite-migration"
    printf '%s\n' "$version" > "$stage/program/.zh-lite-version"
    printf '%s\n' "$source_version" > "$stage/program/.zh-lite-source-version"
    printf '%s\n' "$info" > "$stage/program/.zh-lite-migration-env.json"
    chown root:root "$stage/program/.zh-lite-migration" "$stage/program/.zh-lite-version" "$stage/program/.zh-lite-migration-env.json" "$stage/program/.zh-lite-source-version"
    chmod 0600 "$stage/program/.zh-lite-migration" "$stage/program/.zh-lite-version" "$stage/program/.zh-lite-migration-env.json" "$stage/program/.zh-lite-source-version"
    mv -T -- "$app" "$stage/original-app"
    mv -T -- "$stage/program" "$app"
    cli_changed=1
    rm -f -- "$command_path"
    install -m 0755 -o root -g root "$package/x-ui.sh" "$command_path"
    if (( upstream_cli_existed )); then
        upstream_cli_changed=1
        rm -f -- /usr/bin/x-ui
        install -m 0755 -o root -g root "$package/x-ui.sh" /usr/bin/x-ui
    fi
    if (( needs_workdir )); then
        unit_modified=1
        [[ -d ${workdir_dropin%/*} ]] || install -d -m 0755 "${workdir_dropin%/*}"
        printf '[Service]\nWorkingDirectory=/usr/local/x-ui\n' > "$workdir_dropin"
        chmod 0644 "$workdir_dropin"
        systemctl daemon-reload
    fi
    if (( legacy_upgrade )); then
        database_dirty=1
        for suffix in '' -wal -shm -journal; do rm -f -- "$db_dir/x-ui.db$suffix"; done
        cp -a -- "$stage/check-database/x-ui.db" "$db_dir/x-ui.db"
    fi
    if (( was_active )); then
        database_dirty=1
        systemctl start x-ui.service
        # Catch immediate exit/restart loops; do not change listen address, TLS, or settings to probe it.
        for ((program=0; program<5; program++)); do
            sleep 1
            systemctl is-active --quiet x-ui.service || die '中文面板未稳定运行。'
            [[ $(systemctl show x-ui.service --property=SubState --value) == running ]] || die '中文面板正在重启。'
        done
    fi
    committed=1
    printf '已接管到 %s；原服务身份、环境、账号、端口、访问路径、证书与 bin 资源保持原位。\n' "$version"
    printf '迁移前程序、SQLite 与服务配置备份：%s\n后续更新：sudo bash /usr/local/x-ui/install-online.sh --migrate\n中文运维：sudo x-ui\n' "$backup"
    if (( ! was_active )); then printf '原服务未运行，迁移后保持停止；启动前请检查原服务配置。\n'; fi
)

migration_upgrade_check() {
    python3 -I - "$1" "$2" <<'PY'
import hashlib, json, math, os, pathlib, re, sqlite3, subprocess, sys
stage, executable = pathlib.Path(sys.argv[1]), sys.argv[2]
state = json.loads((stage/'backup/state.json').read_text())
original = sqlite3.connect((stage/'backup/database/x-ui.db').as_uri()+'?mode=ro', uri=True)
original.row_factory = sqlite3.Row
upgraded = None
def rows(connection, table):
    order = 'client_id,inbound_id' if table == 'client_inbounds' else 'id'
    return [dict(row) for row in connection.execute('SELECT * FROM "'+table+'" ORDER BY '+order)]
def fail(message):
    sys.exit('2.9.2 数据保真检查失败：'+message)
def json_semantics(value):
    if value is None: return ('null',)
    if type(value) is bool: return ('boolean',value)
    if type(value) in (int,float):
        if type(value) is float and not math.isfinite(value): raise ValueError('non-finite JSON number')
        return ('number',value)
    if isinstance(value,str): return ('string',value)
    if isinstance(value,list): return ('array',tuple(json_semantics(item) for item in value))
    if isinstance(value,dict): return ('object',tuple(sorted((key,json_semantics(item)) for key,item in value.items())))
    raise ValueError('unsupported JSON value')
def client_integer(value, field):
    # Match NormalizeLegacyClientFields + Go int64 exactly for legal values.
    # Preserve limits/expiry/reset instead of confusing string normalization with loss.
    if isinstance(value,str):
        value = value.strip().replace(' ','')
        if value == '': return 0  # Go treats the empty legacy value as unset.
        if not re.fullmatch('[+-]?[0-9]+',value): fail('旧客户端字段不是合法整数：'+field)
        value = int(value,10)
    elif type(value) is float and math.isfinite(value) and value.is_integer(): value = int(value)
    elif type(value) is not int: fail('旧客户端字段不是合法整数：'+field)
    if not -(2**63) <= value < 2**63: fail('旧客户端字段超出 int64 范围：'+field)
    return value
try:
    users = rows(original, 'users')
    inbounds = rows(original, 'inbounds')
    settings = rows(original, 'settings')
    if not users or not any(row['seeder_name']=='UserPasswordHash' for row in rows(original, 'history_of_seeders')):
        fail('缺少原版账号或密码迁移记录，拒绝猜测账号格式。')
    for row in settings:
        if row['key'] in ('webCertFile', 'webKeyFile', 'subCertFile', 'subKeyFile') and row['value'] and not os.path.isabs(row['value']):
            fail('证书采用相对路径，无法确认升级后解析位置。')
    for inbound in inbounds:
        data = json.loads(inbound['settings'] or '{}')
        if not isinstance(data, dict) or not isinstance(data.get('clients', []), list): fail('入站 clients 格式不受支持。')
        seen = set()
        for client in data.get('clients', []):
            if not isinstance(client, dict) or not str(client.get('email', '')).strip(): fail('客户端缺少唯一 email。')
            key = client['email'].strip().lower()
            if key in seen: fail('入站含重复客户端，拒绝让 3.x 自动去重。')
            seen.add(key)
    environment = {k:v for k,v in os.environ.items() if not k.startswith('XUI_') and k != 'NODE_TOKEN_ENCRYPTION'}
    environment.update(state['environment'])
    environment.update(XUI_DB_FOLDER=str(stage/'check-database'), XUI_DB_TYPE='sqlite', XUI_DB_DSN='', XUI_LOG_FOLDER=str(stage/'check-log'))
    (stage/'schema-working-directory').mkdir()
    # The verified release executable performs its own supported AutoMigrate and
    # seeders on a disposable copy, with no Web/Xray/background server started.
    with (stage/'schema-check.log').open('wb') as log:
        result = subprocess.run([executable, 'setting', '-show', 'true'], cwd=str(stage/'schema-working-directory'), env=environment, stdout=log, stderr=log, timeout=180)
    if result.returncode: fail('新程序无法升级数据库副本，详见 root 备份中的 schema-check.log。')
    upgraded = sqlite3.connect(str(stage/'check-database/x-ui.db'))
    upgraded.row_factory = sqlite3.Row
    if upgraded.execute('PRAGMA quick_check').fetchall()[0][0] != 'ok': fail('升级后 SQLite 完整性不通过。')
    new_users = rows(upgraded, 'users')
    if [(r['id'],r['username'],r['password']) for r in users] != [(r['id'],r['username'],r['password']) for r in new_users]: fail('账号或密码发生变化。')
    new_settings = {row['key']:row['value'] for row in rows(upgraded, 'settings')}
    for setting in settings:
        key, value = setting['key'], setting['value']
        current = new_settings.get(key)
        if key == 'apiToken' and value:
            digest = hashlib.sha256(value.encode()).hexdigest()
            if not upgraded.execute('SELECT 1 FROM api_tokens WHERE token=? AND enabled=1', (digest,)).fetchone(): fail('原 API Token 未保留为有效令牌。')
            continue
        if key == 'xrayTemplateConfig':
            before = json.loads(value) if value.strip() else None
            after = json.loads(current) if isinstance(current, str) and current.strip() else None
            if json_semantics(before) != json_semantics(after):
                fail('Xray 模板的路由、DNS、outbounds 或其他配置发生语义差异，拒绝覆盖原配置。')
            continue
        if key in ('webBasePath', 'subPath', 'subJsonPath', 'subClashPath') and isinstance(current, str) and value.strip('/') == current.strip('/'): continue
        if current != value: fail('设置被改变或删除：'+key)
    new_inbounds = {row['id']:row for row in rows(upgraded, 'inbounds')}
    if set(new_inbounds) != {row['id'] for row in inbounds}: fail('入站数量或 ID 变化。')
    clients = {row['email']:row for row in rows(upgraded, 'clients')}
    links = {(row['client_id'],row['inbound_id']):row for row in rows(upgraded, 'client_inbounds')}
    mapping = {'id':'uuid','password':'password','auth':'auth','security':'security','email':'email','subId':'sub_id','enable':'enable','totalGB':'total_gb','expiryTime':'expiry_time','limitIp':'limit_ip','tgId':'tg_id','comment':'comment','reset':'reset','created_at':'created_at','updated_at':'updated_at'}
    integer_fields = {'totalGB','expiryTime','limitIp','tgId','reset','created_at','updated_at'}
    for inbound in inbounds:
        new = new_inbounds[inbound['id']]
        for key, value in inbound.items():
            if key in ('settings', 'stream_settings', 'sniffing'): continue
            if key == 'protocol' and value == 'socks' and new[key] == 'mixed': continue
            if key in new and new[key] != value: fail('入站字段被改变：'+str(inbound['id'])+'/'+key)
        for key in ('stream_settings', 'sniffing'):
            if json_semantics(json.loads(inbound.get(key) or '{}')) != json_semantics(json.loads(new.get(key) or '{}')):
                fail('传输或嗅探配置需要改写，不能保证原客户端兼容：'+str(inbound['id'])+'/'+key)
        old_settings = json.loads(inbound['settings'] or '{}')
        new_config = json.loads(new['settings'] or '{}')
        for key, value in old_settings.items():
            if key != 'clients' and json_semantics(new_config.get(key)) != json_semantics(value): fail('入站配置被改变：'+str(inbound['id'])+'/'+key)
        for client in old_settings.get('clients', []):
            row = clients.get(client['email'].strip())
            if row is None or (row['id'],inbound['id']) not in links: fail('客户端或入站关联缺失。')
            for key, column in mapping.items():
                if key not in client: continue
                value = client[key]
                if key in integer_fields:
                    value = client_integer(value,key)
                    if key in ('created_at','updated_at') and value == 0: continue  # unset timestamps are assigned by GORM
                elif key == 'enable':
                    if type(value) is not bool: fail('旧客户端 enable 不是布尔值。')
                    value = int(value)
                if row.get(column) != value: fail('客户端属性被改变：'+key)
            if client.get('flow', '') != links[(row['id'],inbound['id'])]['flow_override']: fail('客户端 flow 关联变化。')
    for table in ('client_traffics','outbound_traffics','inbound_client_ips','custom_geo_resources'):
        if not original.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone(): continue
        current = {row['id']:row for row in rows(upgraded, table)}
        for row in rows(original, table):
            if row['id'] not in current or any(current[row['id']].get(key) != value for key,value in row.items()):
                fail('原统计或自定义资源数据被改变：'+table)
    # Checkpoint only the disposable upgraded DB, never the original snapshot.
    upgraded.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    upgraded.close()
    original.close()
    for suffix in ('-wal','-shm','-journal'):
        path = stage/'check-database'/('x-ui.db'+suffix)
        if path.exists(): path.unlink()
except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError, KeyError, TypeError) as error:
    fail('数据库结构或升级输出不受支持：'+type(error).__name__)
finally:
    if upgraded is not None: upgraded.close()
    original.close()
PY
}
