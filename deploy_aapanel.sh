#!/bin/bash
# deploy_aapanel.sh — Deploy LocketGold DNS Proxy trên aaPanel
# Không làm hỏng web đang chạy
#
# Cách dùng: bash deploy_aapanel.sh
# VPS IP: 160.22.107.114

set -e

VPS_IP="160.22.107.114"
PROXY_DIR="/opt/locketgold-proxy"
NGINX_CONF_DIR="/www/server/nginx/conf"
SERVICE_NAME="locketgold-proxy"

echo "================================================="
echo " LocketGold DNS Proxy — aaPanel Deploy"
echo " VPS: $VPS_IP"
echo "================================================="

# ===== BƯỚC 1: Cài Python dependencies =====
echo ""
echo "[1/6] Cài Python packages..."
pip3 install cryptography aiohttp --quiet
echo "[+] Done"

# ===== BƯỚC 2: Tạo thư mục và copy code =====
echo ""
echo "[2/6] Setup thư mục..."
mkdir -p $PROXY_DIR
cp -r $(dirname "$0")/* $PROXY_DIR/
cd $PROXY_DIR
echo "[+] Code copied to $PROXY_DIR"

# ===== BƯỚC 3: Tạo CA + cert =====
echo ""
echo "[3/6] Generate certificates..."
python3 generate_ca.py
echo "[+] Certs created in $PROXY_DIR/certs/"

# ===== BƯỚC 4: Tạo .mobileconfig =====
echo ""
echo "[4/6] Generate iOS profile..."
python3 generate_profile.py --vps-ip $VPS_IP --out locketgold.mobileconfig

# Serve profile qua web aaPanel
PROFILE_DIR="/www/wwwroot/locketgold.shop/profile"
mkdir -p $PROFILE_DIR
cp locketgold.mobileconfig $PROFILE_DIR/
chmod 644 $PROFILE_DIR/locketgold.mobileconfig
echo "[+] Profile: http://locketgold.shop/profile/locketgold.mobileconfig"

# ===== BƯỚC 5: Cấu hình Nginx stream (SNI routing) =====
echo ""
echo "[5/6] Cấu hình Nginx SNI routing..."

# Backup nginx.conf
cp $NGINX_CONF_DIR/nginx.conf $NGINX_CONF_DIR/nginx.conf.bak.$(date +%Y%m%d_%H%M%S)
echo "[+] Nginx backup saved"

# Tạo stream config
cat > $NGINX_CONF_DIR/stream.conf << 'EOF'
stream {
    ssl_preread on;

    map $ssl_preread_server_name $backend_addr {
        api.revenuecat.com  127.0.0.1:8443;
        default             127.0.0.1:4443;
    }

    server {
        listen 443;
        listen [::]:443;
        proxy_pass $backend_addr;
        ssl_preread on;
        proxy_connect_timeout 10s;
        proxy_timeout 60s;
    }
}
EOF

# Thêm include vào nginx.conf nếu chưa có
if ! grep -q "stream.conf" $NGINX_CONF_DIR/nginx.conf; then
    echo "" >> $NGINX_CONF_DIR/nginx.conf
    echo "include $NGINX_CONF_DIR/stream.conf;" >> $NGINX_CONF_DIR/nginx.conf
    echo "[+] Added stream include to nginx.conf"
fi

# Đổi port listen HTTPS của các site từ 443 -> 4443 (internal)
# aaPanel sites thường ở /www/server/nginx/conf/vhost/
echo ""
echo "[!] QUAN TRỌNG: Cần đổi port listen của các site từ 443 sang 4443"
echo "    File config site thường ở: $NGINX_CONF_DIR/vhost/"
echo "    Dùng lệnh: sed -i 's/listen 443/listen 4443/g' $NGINX_CONF_DIR/vhost/*.conf"
echo ""
read -p "Tự động đổi port 443->4443 cho tất cả site? (y/n): " CONFIRM
if [ "$CONFIRM" = "y" ] || [ "$CONFIRM" = "Y" ]; then
    sed -i 's/listen 443 ssl/listen 4443 ssl/g' $NGINX_CONF_DIR/vhost/*.conf 2>/dev/null || true
    sed -i 's/listen \[::\]:443 ssl/listen [::]:4443 ssl/g' $NGINX_CONF_DIR/vhost/*.conf 2>/dev/null || true
    echo "[+] Ports updated"
else
    echo "[!] Bỏ qua — nhớ tự đổi thủ công"
fi

# Test và reload Nginx
nginx -t && nginx -s reload
echo "[+] Nginx reloaded"

# ===== BƯỚC 6: Tạo systemd service =====
echo ""
echo "[6/6] Tạo systemd service..."

cat > /etc/systemd/system/$SERVICE_NAME.service << EOF
[Unit]
Description=LocketGold DNS + HTTPS Proxy
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$PROXY_DIR
ExecStart=/usr/bin/python3 $PROXY_DIR/proxy_server.py --vps-ip $VPS_IP
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

# Mở port 53
if command -v ufw &>/dev/null; then
    ufw allow 53/udp
    ufw allow 53/tcp
    ufw reload 2>/dev/null || true
fi

echo ""
echo "================================================="
echo " DEPLOY HOÀN TẤT"
echo "================================================="
echo ""
echo " Service: systemctl status $SERVICE_NAME"
echo " Logs:    journalctl -u $SERVICE_NAME -f"
echo " Profile: http://locketgold.shop/profile/locketgold.mobileconfig"
echo ""
echo " Test DNS:"
echo "   nslookup api.revenuecat.com $VPS_IP"
echo "   (phải trả về $VPS_IP)"
echo ""
echo " Test proxy:"
echo "   curl -k https://$VPS_IP:8443/v1/subscribers/TEST"
echo "   (phải trả về JSON có Gold active)"
