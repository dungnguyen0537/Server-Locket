# LocketGold DNS Proxy System

## Tổng quan

Hệ thống mới thay thế hoàn toàn cơ chế inject receipt/alias (đã bị Locket vô hiệu hóa).

### Cơ chế hoạt động

```
[Locket App] -> gọi api.revenuecat.com
     |
     v (DNS profile trên điện thoại khách)
[DNS trỏ về VPS của ta]
     |
     v
[proxy_server.py trên VPS : 443]
     |
     +-- Check DB: UID có được kích hoạt không?
     |        |
     |        +-- YES -> Forward request + Inject Gold vào response
     |        +-- NO  -> Forward request nguyên si (Gold inactive)
     v
[api.revenuecat.com thật]
     |
     v (response đã được modify)
[Locket App] -> thấy Gold Active đến 2099
```

---

## Files

| File | Chức năng |
|---|---|
| `generate_ca.py` | Tạo Root CA + SSL cert cho api.revenuecat.com |
| `proxy_server.py` | Reverse proxy chính — HTTPS + DNS server |
| `generate_profile.py` | Tạo file .mobileconfig cho khách iOS |
| `dns_activation.py` | Bridge với hệ thống web — kích hoạt/hủy Gold |
| `setup.sh` | Script setup tự động trên VPS |
| `requirements.txt` | Python dependencies |

---

## Deploy lên VPS

```bash
# 1. Copy toàn bộ folder dns_proxy lên VPS
scp -r dns_proxy/ root@160.191.245.200:/opt/locketgold-proxy/

# 2. SSH vào VPS
ssh root@160.191.245.200

# 3. Chạy setup tự động
cd /opt/locketgold-proxy
bash setup.sh
```

Setup sẽ tự động:
- Cài Python + dependencies
- Tạo Root CA + server cert
- Tạo file .mobileconfig
- Mở port 53 (DNS) + 443 (HTTPS)
- Tạo systemd service và khởi động

---

## Phân phối cho khách hàng

Sau khi setup, gửi cho khách link:
```
http://160.191.245.200/profile/locketgold.mobileconfig
```

Hoặc host qua domain:
```
https://locketgold.shop/profile/locketgold.mobileconfig
```

**Khách cài đặt (3 bước):**
1. Mở link trên Safari iOS (không dùng Chrome)
2. Bấm "Allow" -> vào Settings > Profile Downloaded > Install
3. Vào Settings > General > VPN & Device Management -> Trust profile

---

## Tích hợp với hệ thống web

Thay thế `inject_gold()` trong `api.py`:

```python
# Cũ (không còn hoạt động):
# from app.services.locket import inject_gold
# success, msg = await inject_gold(uid, token_config)

# Mới — DNS Proxy mode:
from dns_proxy.dns_activation import activate_dns_gold
result = activate_dns_gold(
    uid=uid,
    duration_days=365,
    locket_username=username,
    ctv_username=ctv_user,
    order_id=order_id
)
success = result["success"]
```

---

## Tùy chọn khởi động proxy

```bash
# Chạy thủ công (dev/test)
python proxy_server.py --vps-ip 160.191.245.200

# Free mode (inject Gold cho TẤT CẢ - test only)
python proxy_server.py --vps-ip 160.191.245.200 --free-mode

# Không chạy DNS server (dùng DNS riêng)
python proxy_server.py --no-dns

# Xem logs
journalctl -u locketgold-proxy -f
```

---

## Lưu ý quan trọng

### Về iOS Certificate Trust
- iOS 13+: Root CA phải được trust thủ công trong Settings
- iOS tự động trust CA trong .mobileconfig khi cài profile
- Cert server phải có SAN cho `api.revenuecat.com` (đã được generate đúng)
- Cert validity tối đa 825 ngày (đã set)

### Về DNS
- Port 53 cần mở cả TCP và UDP
- iOS DNS profile (Do53) override toàn bộ DNS kể cả WiFi/4G
- Chỉ forward query cho `api.revenuecat.com` về VPS, còn lại qua 8.8.8.8

### Về bảo mật
- `root_ca.key` và `server.key` phải giữ bí mật tuyệt đối trên VPS
- Không bao giờ commit key vào git
- DB chứa UID khách — là nguồn kiểm soát ai được Gold

### Về fake date
Để thay đổi ngày hết hạn Gold, sửa trong `proxy_server.py`:
```python
FAKE_EXPIRES_DATE = "2099-12-31T23:59:59Z"   # Gold đến 2099
# hoặc dynamic từ DB:
# expires_at được lưu khi gọi activate_dns_gold(duration_days=365)
```

---

## Troubleshoot

**Locket vẫn thấy Gold inactive:**
- Kiểm tra profile đã được cài và trust chưa
- Thử tắt/mở lại app Locket
- Kiểm tra proxy đang chạy: `systemctl status locketgold-proxy`
- Check logs: `journalctl -u locketgold-proxy -f`
- Test DNS: `nslookup api.revenuecat.com` từ điện thoại phải trả về VPS IP

**SSL error khi Locket connect:**
- Kiểm tra CA đã được trust chưa (Settings > General > About > Certificate Trust Settings)
- Regenerate cert: `python generate_ca.py && python generate_profile.py --vps-ip IP`
- Cài lại profile cho khách

**Port 443 đang bị chiếm:**
- Kiểm tra Nginx: `nginx -t && systemctl stop nginx`
- Proxy cần bind port 443 trực tiếp
