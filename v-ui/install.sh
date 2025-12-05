#!/bin/bash

# V-UI Installer for Ubuntu/Debian
# Usage: sudo bash install.sh

set -e

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}Starting V-UI Installation...${NC}"

# 1. Check Root
if [ "$EUID" -ne 0 ]; then 
  echo -e "${RED}Please run as root${NC}"
  exit 1
fi

# 2. Install Dependencies
echo -e "${GREEN}Installing System Dependencies...${NC}"
apt-get update
apt-get install -y python3 python3-pip python3-venv git curl socat

# 3. Setup Directory
INSTALL_DIR="/usr/local/v-ui"
echo -e "${GREEN}Setting up directory at ${INSTALL_DIR}...${NC}"

if [ -d "$INSTALL_DIR" ]; then
    echo "Directory exists, backing up..."
    mv "$INSTALL_DIR" "${INSTALL_DIR}_backup_$(date +%s)"
fi

mkdir -p "$INSTALL_DIR"
# Copy current files to install dir (Assuming script is run from the repo root or files are uploaded)
# In a real scenario, we might git clone here.
# cp -r ./* "$INSTALL_DIR"
# For now, we assume the user uploads the 'v-ui' folder content to /usr/local/v-ui
# Or we clone from a repo if provided.

echo "Please ensure the V-UI files are placed in ${INSTALL_DIR}"

# 4. Setup Python Environment
cd "$INSTALL_DIR"
echo -e "${GREEN}Creating Python Virtual Environment...${NC}"
python3 -m venv venv
source venv/bin/activate

echo -e "${GREEN}Installing Python Requirements...${NC}"
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
else
    echo -e "${RED}requirements.txt not found!${NC}"
fi

# 5. Initialize Database
echo -e "${GREEN}Initializing Database...${NC}"
python3 bin/init_db.py

# 6. Setup Systemd Service
echo -e "${GREEN}Creating Systemd Service...${NC}"
cat > /etc/systemd/system/v-ui.service <<EOF
[Unit]
Description=V-UI Panel Service
After=network.target

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
systemctl start v-ui

echo -e "${GREEN}V-UI Installed and Started Successfully!${NC}"
echo -e "Access the panel at: http://<YOUR_SERVER_IP>:2053/ui"
