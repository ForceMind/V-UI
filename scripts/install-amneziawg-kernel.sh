#!/usr/bin/env bash
#
# Installs the AmneziaWG kernel module and tools, the prerequisite for an
# AmneziaWG inbound's "kernel mode" (internal/amneziawgkern). Without it the
# panel refuses to enable the setting and says why.
#
# Verified on Debian 13 (trixie), kernel 7.1.11-x64v3-xanmod1, x86_64.
set -eu

log() { printf '\n==> %s\n' "$*"; }
die() { printf '\n错误：%s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "请以 root 用户运行"
command -v apt-get >/dev/null || die "此安装脚本仅支持 Debian 和 Ubuntu；其他系统请手动安装 amneziawg-linux-kernel-module 和 amneziawg-tools 3.x"

KERNEL="$(uname -r)"
SRC_DIR="${SRC_DIR:-/usr/local/src}"
MODULE_REPO="https://github.com/amnezia-vpn/amneziawg-linux-kernel-module.git"
TOOLS_REPO="https://github.com/amnezia-vpn/amneziawg-tools.git"

# Pinned to tested releases and verified by commit, since this runs as root.
# Module and tools must share the 3.x series: 1.0 tools send u32 H1-H4 and
# `awg setconf` then fails with "Invalid argument".
MODULE_TAG="v3.1.20260906"
MODULE_COMMIT="4569c4c67f3a57414969260cafbbd04694fbaae0"
TOOLS_TAG="v3.1.20260812"
TOOLS_COMMIT="ee0f0a9aa34ff0a0da4b3433b9512781cfe02843"

log "当前内核：$KERNEL"
[ -d "/lib/modules/$KERNEL/build" ] || die "缺少内核头文件，请安装 linux-headers-$KERNEL"

log "以下安装和构建步骤保留软件包管理器、Git、编译器和 DKMS 的原始输出，便于排查错误。"
log "正在安装构建依赖"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq dkms build-essential git libmnl-dev pkg-config nftables

# A clang-built kernel makes DKMS invoke the module build with LLVM=1, which
# fails outright when only gcc is present. /proc/version names the compiler.
if grep -qi clang /proc/version; then
	log "当前内核由 clang 构建，正在安装配套的 LLVM 工具链"
	apt-get install -y -qq clang lld llvm
fi

log "内核模块版本 $MODULE_TAG，工具版本 $TOOLS_TAG"

mkdir -p "$SRC_DIR"
cd "$SRC_DIR"
rm -rf amneziawg-linux-kernel-module amneziawg-tools
git clone -q --depth 1 --branch "$MODULE_TAG" "$MODULE_REPO"
git clone -q --depth 1 --branch "$TOOLS_TAG" "$TOOLS_REPO"
[ "$(git -C amneziawg-linux-kernel-module rev-parse HEAD)" = "$MODULE_COMMIT" ] || die "内核模块 $MODULE_TAG 的提交与预期 $MODULE_COMMIT 不一致"
[ "$(git -C amneziawg-tools rev-parse HEAD)" = "$TOOLS_COMMIT" ] || die "工具 $TOOLS_TAG 的提交与预期 $TOOLS_COMMIT 不一致"

MODULE_SRC="$SRC_DIR/amneziawg-linux-kernel-module/src"

log "正在通过 DKMS 构建内核模块"
cd "$MODULE_SRC"
rm -rf /usr/src/amneziawg-1.0.0
make dkms-install
dkms status -m amneziawg -v 1.0.0 | grep -q . || dkms add -m amneziawg -v 1.0.0
dkms build -m amneziawg -v 1.0.0
dkms install -m amneziawg -v 1.0.0 --force

log "正在加载内核模块"
modprobe amneziawg
lsmod | grep -q '^amneziawg' || die "内核模块未能加载"

log "正在构建 amneziawg-tools"
cd "$SRC_DIR/amneziawg-tools/src"
make
make install

log "已完成"
printf '  内核模块：%s\n' "$(dkms status -m amneziawg)"
printf '  配套工具：%s\n' "$(awg --version)"
printf '\n请在面板的 AmneziaWG 入站中启用“内核模式”以使用此模块。\n'
printf '内核升级时 DKMS 会重新构建模块；启用安全启动（Secure Boot）的主机必须为模块签名。\n'
