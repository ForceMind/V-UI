#!/bin/bash

# V-UI Binary Installer
# Usage: bash <(curl -Ls https://raw.githubusercontent.com/ForceMind/V-UI/master/install-bin.sh)

set -e
GREEN='\033[0;32m'
NC='\033[0m'

INSTALL_DIR="/usr/local/v-ui"
BIN_URL="https://github.com/ForceMind/V-UI/releases/latest/download/v-ui"

echo -e "${GREEN}Installing V-UI (Binary Version)...${NC}"

# 1. Prepare Directory
mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

# 2. Download Binary
echo -e "${GREEN}Downloading V-UI Binary...${NC}"
curl -L -o v-ui "$BIN_URL"
chmod +x v-ui

# 3. Download Xray Core (Still needed as external dependency)
echo -e "${GREEN}Downloading Xray Core...${NC}"
mkdir -p bin
curl -L -o /tmp/xray.zip https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip
unzip -o /tmp/xray.zip -d /tmp/xray
mv /tmp/xray/xray bin/xray
chmod +x bin/xray
rm -rf /tmp/xray*

# 4. Create Systemd Service
echo -e "${GREEN}Creating Service...${NC}"
cat > /etc/systemd/system/v-ui.service <<EOF
[Unit]
Description=V-UI Panel Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/v-ui
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable v-ui
systemctl start v-ui

# 5. Install Management Script
echo -e "${GREEN}Installing Management Tool...${NC}"
curl -Ls https://raw.githubusercontent.com/ForceMind/V-UI/master/v-ui.sh -o /usr/bin/v-ui
chmod +x /usr/bin/v-ui

echo -e "${GREEN}Installation Complete!${NC}"
echo -e "Panel: http://<IP>:2053/ui"
