#!/bin/bash
# setup.sh — Tự động cài đặt LocketGold DNS Proxy trên VPS Ubuntu/Debian
# Chạy với quyền root: sudo bash setup.sh

set -e

VPS_IP=$(curl -s ifconfig.me || curl -s icanhazip.com)
INSTALL_DIR="/opt/locketgold-proxy"
SERVICE_NAME="locketgold-proxy"

echo "================================================="
echo " LocketGold DNS Proxy Setup"
echo " VPS IP: $VPS_IP"
echo "================================================="

# 1. Cài Python 3.11+ và pip
echo "[1/7] Installing Python dependencies..."
apt-get update -qq
apt-get install -y python3 python3-pip python3-venv curl wget -qq

# 2. Tạo thư mục cài đặt
echo "[2/7] Setting up directory..."
mkdir -p $INSTALL_DIR
cp -r . $INSTALL_DIR/
cd $INSTALL_DIR

# 3. Tạo virtualenv và cài packages
echo "[3/7] Installing Python packages..."
python3 -m venv venv
./venv/bin/pip install -q cryptography aiohttp

# 4. Tạo CA và cert
echo "[4/7] Generating certificates..."
./venv/bin/python generate_ca.py

# 5. Tạo .mobileconfig
echo "[5/7] Generating iOS profile..."
./venv/bin/python generate_profile.py --vps-ip $VPS_IP --out locketgold.mobileconfig

# Copy profile vào thư mục web (để serve qua HTTP)
PROFILE_WEB_DIR="/var/www/html/profile"
mkdir -p $PROFILE_WEB_DIR
cp locketgold.mobileconfig $PROFILE_WEB_DIR/
echo "[+] Profile available at: http://$VPS_IP/profile/locketgold.mobileconfig"

# 6. Mở port 53 (DNS) và 443 (HTTPS)
echo "[6/7] Configuring firewall..."
if command -v ufw &>/dev/null; then
    ufw allow 53/udp
    ufw allow 53/tcp
    ufw allow 443/tcp
    ufw reload || true
fi

# Allow port 443 binding cho non-root (nếu cần)
# Python cần CAP_NET_BIND_SERVICE để bind port < 1024
setcap cap_net_bind_service=+eip $(./venv/bin/python -c "import sys; print(sys.executable)") || true

# 7. Tạo systemd service
echo "[7/7] Creating systemd service..."
cat > /etc/systemd/system/$SERVICE_NAME.service << EOF
[Unit]
Description=LocketGold DNS + HTTPS Proxy
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python proxy_server.py --vps-ip $VPS_IP
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable $SERVICE_NAME
systemctl start $SERVICE_NAME

echo ""
echo "================================================="
echo " Setup COMPLETE!"
echo "================================================="
echo ""
echo " Service status:"
systemctl status $SERVICE_NAME --no-pager
echo ""
echo " Certs location:  $INSTALL_DIR/certs/"
echo " Profile:         http://$VPS_IP/profile/locketgold.mobileconfig"
echo ""
echo " Phan phoi cho khach hang:"
echo "   1. Gui link: http://$VPS_IP/profile/locketgold.mobileconfig"
echo "   2. Khach mo link tren Safari -> Install profile"
echo "   3. Gold se duoc kich hoat sau khi mo app Locket"
echo ""
echo " Quan ly service:"
echo "   systemctl status $SERVICE_NAME"
echo "   systemctl restart $SERVICE_NAME"
echo "   journalctl -u $SERVICE_NAME -f"
