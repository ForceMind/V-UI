#!/usr/bin/env bash
set -euo pipefail

package=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
case ${1:-} in
    -h|--help|帮助) printf '在已解压并校验的同版系安装包目录中运行 sudo bash update.sh。\n'; exit 0 ;;
esac
if [[ -e /usr/local/x-ui/.zh-lite-migration || -L /usr/local/x-ui/.zh-lite-migration ]]; then
    exec bash "$package/install.sh" --migrate "$@"
fi
exec bash "$package/install.sh" --update "$@"
