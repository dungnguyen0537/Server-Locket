# Server-Locket (Standalone DNS Proxy & Telegram Bot)

Hệ thống Reverse Proxy giải mã và tiêm Gold Locket vĩnh viễn / 1 năm cho khách hàng thông qua DNS profile và Telegram Bot.

## Cài đặt trên VPS Ubuntu 24.04 (Chạy 2 lệnh là xong):

```bash
git clone https://github.com/dungnguyen0537/Server-Locket.git
cd Server-Locket && sudo bash setup.sh
```

---

## Sau khi cài đặt:
1. Proxy server sẽ tự động chạy ngầm ở cổng 443.
2. Bot Telegram sẽ tự động online và nhận lệnh.
3. Trên trang quản lý **NextDNS**:
   - Trỏ rule Rewrite: `api.revenuecat.com` ➔ `IP_CỦA_VPS_NÀY`
4. Vào Bot Telegram gõ `/dns` để tải file `.mobileconfig` cài vào iPhone.
