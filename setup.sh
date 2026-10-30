#!/usr/bin/env bash
# Mutable convenience entry point. This script pins the published installer and
# complete checksum list; the raw setup.sh URL itself remains a trust boundary.
set -euo pipefail
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

setup_main() {
  set -euo pipefail
  umask 077
  local version=v0.4.11 version_seen=0 argument domain=
  local installer_sha=6c47bb6f05aafe8595bd1763fe227a3d2c4a08c7d74237a18fd82070a3d4f68b
  local kit_sha=8cf078530059c7483e294c1b6c7d61c3767ce8dd6a75e61f07c0ac276df5f5e6
  local -a installer_args=()
  while [[ "$#" -gt 0 ]]; do
    argument="$1"
    case "$argument" in
      --help|-h)
        cat <<'HELP'
V-UI v0.4.11 交互安装与全局 v-ui 命令（仅支持 Linux）。
用法：bash setup.sh [--version v0.4.11] [安装器选项]

使用 root 或已有 sudo 授权；不新增免密 sudo 规则。
支持的安装器选项：--domain、--email、--admin、--port、--bind、
--node-port、--node-udp-port、--open-firewall、--archive-temp-dir、--cert、
--key、--health-ca、--assume-external-ports-open、--accept-terms、--upgrade。
域名规范化并校验通过后才继续；无效输入会立即提示重新输入。
域名每行最多 4096 个 UTF-8 字节（含换行）。其他参数值原样转发。
管理员密码通过安全提示输入。
已有安装先显示本地管理菜单；只有选择在线升级或继续/修复时才下载套件。
本地管理不联网；升级沿用原配置，继续/修复可选择修改；回车退出。
控制组件损坏仍会安全拒绝，不覆盖不可信文件或删除原数据。
版本、交互模式、全局命令和可信套件设置不能覆盖。
此入口仅内置 v0.4.11 的可信摘要。你仍须信任可变的原始 setup.sh URL；
下载的发布资产会按固定摘要校验。
HELP
        return 0 ;;
      --version)
        [[ "$version_seen" -eq 0 && "$#" -ge 2 ]] || { printf '%s\n' '--version 必须指定一个受信任的版本，且不能重复。' >&2; return 2; }
        version="$2"; version_seen=1; shift 2 ;;
      --version=*)
        [[ "$version_seen" -eq 0 ]] || { printf '%s\n' '--version 不能重复。' >&2; return 2; }
        version="${argument#*=}"; version_seen=1; shift ;;
      --domain)
        [[ "$#" -ge 2 && "$2" != --* ]] || { printf '选项 %s 缺少参数值\n' "$argument" >&2; return 2; }
        domain="$2"; shift 2 ;;
      --domain=*) domain="${argument#*=}"; shift ;;
      --email|--admin|--port|--bind|--node-port|--node-udp-port|--open-firewall|--archive-temp-dir|--cert|--key|--health-ca)
        [[ "$#" -ge 2 && "$2" != --* ]] || { printf '选项 %s 缺少参数值\n' "$argument" >&2; return 2; }
        installer_args+=("$argument" "$2"); shift 2 ;;
      --email=*|--admin=*|--port=*|--bind=*|--node-port=*|--node-udp-port=*|--open-firewall=*|--archive-temp-dir=*|--cert=*|--key=*|--health-ca=*)
        installer_args+=("$argument"); shift ;;
      --assume-external-ports-open|--accept-terms|--upgrade)
        installer_args+=("$argument"); shift ;;
      *)
        printf '选项 %s 不受支持或由安装入口固定管理\n' "$argument" >&2
        return 2 ;;
    esac
  done
  [[ "$version" == v0.4.11 ]] || { printf '版本 %s 没有内置的可信发布摘要\n' "$version" >&2; return 2; }
  [[ "$(uname -s)" == Linux ]] || { printf '%s\n' 'V-UI 安装入口仅支持 Linux。' >&2; return 1; }
  # Re-enter the already loaded function as a command string, never through the
  # original file descriptor: sudo commonly closes process-substitution /dev/fd.
  if [[ "$EUID" -ne 0 ]]; then
    command -v sudo >/dev/null 2>&1 || { printf '%s\n' '请以 root 运行，或安装 sudo 后重新运行安装入口。' >&2; return 1; }
    exec sudo -- /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME=/root LANG=C.UTF-8 \
      PYTHONDONTWRITEBYTECODE=1 /bin/bash -c "$(declare -f setup_main); setup_main \"\$@\"" v-ui-setup --version "$version" "--domain=$domain" ${installer_args[@]+"${installer_args[@]}"}
  fi

  # Detect presence only. Never source or parse installation metadata in Bash.
  # Offer local management before dependency installation or release downloads.
  if [[ -e /etc/v-ui/service.json || -L /etc/v-ui/service.json || -e /usr/local/sbin/v-ui || -L /usr/local/sbin/v-ui || -e /usr/local/lib/v-ui/ops-control.json || -L /usr/local/lib/v-ui/ops-control.json ]]; then
    local selection
    if ! exec 9<>/dev/tty; then
      printf '%s\n' '已有安装需要控制终端选择管理、升级或修复；尚未下载或更改安装。' >&2
      return 1
    fi
    while true; do
      printf '%s\n' '' 'V-UI 已有安装管理菜单' \
        '1  打开本地管理菜单（状态、启停、备份等；不下载）' \
        '2  在线升级（下载已校验套件，沿用原配置和数据）' \
        '3  继续/修复安装（下载套件，读取原配置，可选择修改）' \
        '0  退出（不下载、不更改安装）' >&9
      printf '%s' '请选择 [1/2/3/0，回车退出]: ' >&9
      if ! IFS= read -r -u 9 selection; then selection=0; fi
      case "$selection" in
        ''|0)
          exec 9>&-
          printf '%s\n' '已退出，未下载套件，原配置和数据未更改。'
          return 0 ;;
        1)
          # Do not execute a PATH-selected, linked, writable or unreceipted
          # controller as root. The installed controller checks its full closure
          # again under its existing installation lock before any operation.
          if [[ -x /usr/bin/python3 ]] && /usr/bin/python3 -B -I -S - <<'PYLOCAL'
import hashlib
import json
import os
import stat
import subprocess
import sys


def trusted(path, limit):
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('untrusted root')
        parts = path.strip('/').split('/')
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError('untrusted directory')
        child = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            info = os.fstat(child)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
                    or info.st_nlink != 1 or info.st_size > limit):
                raise ValueError('untrusted file')
            with os.fdopen(child, 'rb', closefd=False) as stream:
                raw = stream.read(limit + 1)
            if len(raw) > limit:
                raise ValueError('oversized file')
            return raw
        finally:
            os.close(child)
    finally:
        os.close(fd)


try:
    if sys.version_info < (3, 9):
        raise ValueError('unsupported OS Python')
    receipt = json.loads(trusted('/usr/local/lib/v-ui/ops-control.json', 16384))
    if type(receipt.get('schema')) is not int or receipt['schema'] != 1:
        raise ValueError('invalid receipt')
    for path in ('/usr/local/sbin/v-ui', '/usr/local/lib/v-ui/system_ops.py'):
        raw = trusted(path, 4_000_000)
        if hashlib.sha256(raw).hexdigest() != receipt['files'][path]:
            raise ValueError('controller drift')
except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
    print('本地管理入口缺失或校验失败；尚未下载、未更改配置和数据。', file=sys.stderr)
    raise SystemExit(1) from None
with open('/dev/tty', 'rb+', buffering=0) as terminal:
    result = subprocess.run(['/usr/local/sbin/v-ui'], stdin=terminal, stdout=terminal, stderr=terminal,
                            env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/',
                                 'LANG': 'C', 'LC_ALL': 'C', 'PYTHONDONTWRITEBYTECODE': '1'})
raise SystemExit(result.returncode)
PYLOCAL
          then :
          else
            printf '%s\n' '本地管理未完成。安装中断可选 3 继续；控制组件损坏须人工核查，安装器不会绕过校验。' >&9
          fi ;;
        2)
          installer_args+=(--upgrade)
          exec 9>&-
          break ;;
        3)
          exec 9>&-
          break ;;
        *) printf '%s\n' '无效选项，请输入 1、2、3 或 0。' >&9 ;;
      esac
    done
  fi

  # EXIT traps run after a function's local variables have left scope.
  setup_dir=''
  local allocating=0 pending_signal=0 allocation_status=0
  setup_cleanup() {
    local status="$1"
    trap - EXIT
    trap '' INT TERM
    if [[ -n "$setup_dir" ]]; then
      rm -rf -- "$setup_dir" || printf '警告：无法删除安装入口的临时目录：%s\n' "$setup_dir" >&2 || :
    fi
    exit "$status"
  }
  setup_signal() {
    if [[ "$allocating" -eq 1 ]]; then
      if [[ "$pending_signal" -eq 0 ]]; then pending_signal="$1"; fi
    else
      exit "$1"
    fi
  }
  trap 'setup_cleanup "$?"' EXIT
  trap 'setup_signal 130' INT
  trap 'setup_signal 143' TERM

  setup_python_ready() {
    [[ -x /usr/bin/python3 ]] && /usr/bin/python3 -B -I -S -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'
  }
  if ! command -v curl >/dev/null 2>&1 || ! command -v sha256sum >/dev/null 2>&1 || ! setup_python_ready; then
    if command -v apt-get >/dev/null 2>&1; then apt-get update; DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl bash coreutils python3
    elif command -v apk >/dev/null 2>&1; then apk add --no-cache ca-certificates curl bash coreutils python3
    elif command -v dnf >/dev/null 2>&1; then dnf install -y ca-certificates curl bash coreutils python3
    elif command -v yum >/dev/null 2>&1; then yum install -y ca-certificates curl bash coreutils python3
    elif command -v zypper >/dev/null 2>&1; then zypper --non-interactive install ca-certificates curl bash coreutils python3
    elif command -v pacman >/dev/null 2>&1; then pacman -Sy --noconfirm --needed ca-certificates curl bash coreutils python
    elif command -v xbps-install >/dev/null 2>&1; then xbps-install -Sy ca-certificates curl bash coreutils python3
    else printf '%s\n' '请安装 curl、sha256sum 和系统 /usr/bin/python3（3.9 或更高版本），然后重新运行安装入口。' >&2; exit 1
    fi
  fi
  setup_python_ready || { printf '%s\n' '全局 v-ui 需要系统 /usr/bin/python3（3.9 或更高版本）；安装无法继续。' >&2; exit 1; }
  # Use the fixed OS interpreter, with isolated imports and a separate terminal.
  # stdin remains available for the frozen installer's password input channel.
  # Existing installations choose upgrade/resume/edit in the verified installer,
  # before any first-install questions. Never parse or source this file in Bash.
  if [[ ! -e /etc/v-ui/service.json && ! -L /etc/v-ui/service.json ]]; then
  domain="$(/usr/bin/python3 -B -I -S - "$domain" <<'PYDOMAIN'
import re
import sys


def fqdn(value):
    try:
        name = value.strip().rstrip('.').encode('idna').decode('ascii').lower()
    except (AttributeError, UnicodeError):
        raise ValueError('域名无效') from None
    if (len(name) > 253 or '.' not in name or name.split('.')[-1].isdigit()
            or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', p) for p in name.split('.'))):
        raise ValueError('请使用 DNS 域名，不含 URL、路径、端口、IP 或通配符')
    return name


invalid = '域名无效，请重新输入 DNS 域名（不含 URL、路径、端口、IP 或通配符）。\n'
value = sys.argv[1]
try:
    name = fqdn(value)
except ValueError:
    try:
        with open('/dev/tty', 'rb+', buffering=0) as terminal:
            if value:
                terminal.write(invalid.encode('utf-8'))
            while True:
                terminal.write('面板域名 / Panel domain: '.encode('utf-8'))
                # Limit each physical input line to 4096 bytes, including LF.
                # Read one extra byte to detect overflow, then discard the rest
                # in bounded chunks so a valid-looking suffix cannot become input.
                raw = terminal.readline(4097)
                if not raw:
                    raise SystemExit('域名输入已结束；安装已取消。')
                if len(raw) > 4096:
                    while not raw.endswith(b'\n'):
                        raw = terminal.readline(4097)
                        if not raw:
                            raise SystemExit('域名输入已结束；安装已取消。')
                    terminal.write(invalid.encode('utf-8'))
                    continue
                try:
                    name = fqdn(raw.decode('utf-8'))
                except (ValueError, UnicodeError):
                    terminal.write(invalid.encode('utf-8'))
                    continue
                break
    except OSError as error:
        raise SystemExit('域名输入需要可用的控制终端：' + str(error)) from None
    except KeyboardInterrupt:
        print('\n域名输入已中断；安装已取消。', file=sys.stderr)
        raise SystemExit(130) from None
print(name)
PYDOMAIN
)"
  fi
  # Own even a path printed by a failing/interrupted mktemp before handling signals.
  allocating=1
  if setup_dir="$(mktemp -d /root/vui-bootstrap.XXXXXXXX)"; then :; else allocation_status=$?; fi
  allocating=0
  if [[ "$pending_signal" -ne 0 ]]; then exit "$pending_signal"; fi
  [[ "$allocation_status" -eq 0 ]] || exit "$allocation_status"
  [[ -n "$setup_dir" ]] || { printf '%s\n' '无法创建安装入口的私有临时目录。' >&2; exit 1; }
  curl --proto '=https' --proto-redir '=https' --tlsv1.2 --fail --location --retry 2 --connect-timeout 15 --max-time 300 \
    "https://github.com/ForceMind/V-UI/releases/download/$version/install.sh" -o "$setup_dir/install.sh"
  printf '%s  %s\n' "$installer_sha" "$setup_dir/install.sh" | sha256sum -c -
  local status=0
  if /bin/bash "$setup_dir/install.sh" --version "$version" --with-global-cli --kit-sha256 "$kit_sha" --interactive --domain "$domain" ${installer_args[@]+"${installer_args[@]}"}; then
    :
  else
    status=$?
  fi
  exit "$status"
}

# Sanitize root execution too. No caller-controlled shell startup, proxy, Python,
# temporary-directory or loader settings are carried into the managed installer.
exec /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME=/root LANG=C.UTF-8 \
  PYTHONDONTWRITEBYTECODE=1 /bin/bash -c "$(declare -f setup_main); setup_main \"\$@\"" v-ui-setup "$@"
