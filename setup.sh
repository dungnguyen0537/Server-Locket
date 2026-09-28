#!/bin/bash
# setup.sh — Cài đặt tự động Locket Server & Bot Telegram trên Ubuntu 24.04
# Chạy với quyền root: sudo bash setup.sh

set -e

INSTALL_DIR="/opt/Server-Locket"

echo "================================================="
echo "   CÀI ĐẶT LOCKET GOLD SERVER & BOT TELEGRAM"
echo "================================================="

# 1. Cập nhật hệ thống và cài package cơ bản
echo "[1/6] Cập nhật và cài đặt dependencies..."
apt-get update -qq
apt-get install -y python3 python3-pip python3-venv git curl -qq

# 2. Setup thư mục chạy
echo "[2/6] Đồng bộ thư mục..."
mkdir -p $INSTALL_DIR
cp -r ./* $INSTALL_DIR/ 2>/dev/null || true
cd $INSTALL_DIR

# 3. Tạo virtual environment và cài thư viện
echo "[3/6] Cài đặt Python packages..."
python3 -m venv venv
./venv/bin/pip install -q --upgrade pip
./venv/bin/pip install -q cryptography aiohttp python-telegram-bot

# 4. Kiểm tra chứng chỉ SSL
echo "[4/6] Khởi tạo chứng chỉ..."
if [ ! -f "certs/server.crt" ]; then
    ./venv/bin/python generate_ca.py
fi

# 5. Tạo file cấu hình iOS (.mobileconfig) với NextDNS DoH
echo "[5/6] Tạo file cấu hình DNS iOS..."
./venv/bin/python generate_profile.py

# Mở port 443 trên Firewall (nếu có UFW)
if command -v ufw &>/dev/null; then
    ufw allow 443/tcp 2>/dev/null || true
    ufw reload 2>/dev/null || true
fi

# 6. Cấu hình 2 Systemd Services chạy ngầm vĩnh viễn
echo "[6/6] Thiết lập Systemd Services..."

# Service 1: Proxy Server (Cổng 443)
cat > /etc/systemd/system/locket-proxy.service << EOF
[Unit]
Description=LocketGold HTTPS Proxy Server (Port 443)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python proxy_server.py
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Service 2: Bot Telegram
cat > /etc/systemd/system/locket-bot.service << EOF
[Unit]
Description=LocketGold Telegram Bot
After=network.target locket-proxy.service

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python bot.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable locket-proxy locket-bot
systemctl restart locket-proxy locket-bot

echo ""
echo "================================================="
echo "            CÀI ĐẶT HOÀN TẤT 100%!"
echo "================================================="
echo ""
echo " Trạng thái Proxy: $(systemctl is-active locket-proxy)"
echo " Trạng thái Bot:   $(systemctl is-active locket-bot)"
echo ""
echo " Lệnh quản lý:"
echo "   Xem log Proxy: journalctl -u locket-proxy -f"
echo "   Xem log Bot:   journalctl -u locket-bot -f"
echo "   Restart cả 2:  systemctl restart locket-proxy locket-bot"
echo "================================================="
