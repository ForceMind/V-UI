#!/bin/bash
set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

INSTALL_DIR="/usr/local/v-ui"
REPO_URL="https://github.com/ForceMind/V-UI.git"

echo -e "${GREEN}Starting V-UI Installation...${NC}"

if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}Please run as root${NC}"
  exit 1
fi

apt-get update
apt-get install -y python3 python3-pip python3-venv git curl socat unzip tar

if [ -d "$INSTALL_DIR" ]; then
    mv "$INSTALL_DIR" "${INSTALL_DIR}_backup_$(date +%s)"
fi

git clone "$REPO_URL" "$INSTALL_DIR"
cd "$INSTALL_DIR"

python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

mkdir -p data bin
python3 bin/init_db.py

ARCH_RAW="$(uname -m)"
case "$ARCH_RAW" in
    x86_64|amd64)
        SB_ARCH="amd64"
        XRAY_ASSET="Xray-linux-64.zip"
        ;;
    aarch64|arm64)
        SB_ARCH="arm64"
        XRAY_ASSET="Xray-linux-arm64-v8a.zip"
        ;;
    *)
        echo -e "${RED}Unsupported architecture: ${ARCH_RAW}${NC}"
        exit 1
        ;;
esac

echo -e "${GREEN}Downloading Xray Core...${NC}"
curl -fL -o /tmp/xray.zip     "https://github.com/XTLS/Xray-core/releases/latest/download/${XRAY_ASSET}"
rm -rf /tmp/xray
mkdir -p /tmp/xray
unzip -oq /tmp/xray.zip -d /tmp/xray
mv /tmp/xray/xray bin/xray
chmod +x bin/xray
rm -rf /tmp/xray /tmp/xray.zip

echo -e "${GREEN}Downloading sing-box Core...${NC}"
SB_VERSION="$(
    curl -fsSL https://api.github.com/repos/SagerNet/sing-box/releases/latest     | grep '"tag_name"'     | head -n1     | cut -d '"' -f4     | sed 's/^v//'
)"
if [ -z "$SB_VERSION" ]; then
    echo -e "${RED}Unable to resolve latest sing-box version${NC}"
    exit 1
fi

SB_DIR="sing-box-${SB_VERSION}-linux-${SB_ARCH}"
curl -fL -o /tmp/sing-box.tar.gz     "https://github.com/SagerNet/sing-box/releases/download/v${SB_VERSION}/${SB_DIR}.tar.gz"
rm -rf "/tmp/${SB_DIR}"
tar -xzf /tmp/sing-box.tar.gz -C /tmp
mv "/tmp/${SB_DIR}/sing-box" bin/sing-box
chmod +x bin/sing-box
rm -rf "/tmp/${SB_DIR}" /tmp/sing-box.tar.gz

cat > /etc/systemd/system/v-ui.service <<EOF
[Unit]
Description=V-UI Panel Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/venv/bin/python ${INSTALL_DIR}/main.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable v-ui
systemctl restart v-ui

if [ -f "v-ui.sh" ]; then
    cp v-ui.sh /usr/bin/v-ui
    chmod +x /usr/bin/v-ui
fi

echo -e "${GREEN}V-UI installed successfully.${NC}"
echo -e "Panel: http://<YOUR_SERVER_IP>:2053/ui"
echo -e "Cores: Xray + sing-box"
