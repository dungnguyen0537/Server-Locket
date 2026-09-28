"""
dns_activation.py — Bridge giữa hệ thống web và DNS Proxy

Thay thế hoàn toàn inject_gold() cũ (dùng RevenueCat alias/receipt).
Giờ chỉ cần ghi UID vào DB là proxy sẽ tự động inject Gold khi app gọi RevenueCat.

Cách hoạt động:
  1. CTV/Admin bấm kích hoạt Gold cho user
  2. resolve_uid(username) -> lấy UID
  3. activate_dns_gold(uid, duration_days) -> ghi vào DB bảng upgrades
  4. Khách mở Locket -> app gọi GET /v1/subscribers/{uid}
  5. Proxy chặn, check DB -> uid có trong upgrades -> inject Gold
  6. Locket hiển thị Gold active

Ưu điểm:
  - Không cần token RevenueCat thật
  - Không bị giới hạn alias hay receipt
  - Có thể fake bất kỳ ngày hết hạn nào
  - Hoạt động vĩnh viễn dù Locket update
"""

import sqlite3
import os
import datetime
from typing import Optional

# DB chính của hệ thống
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "bot_data.db")


def activate_dns_gold(
    uid: str,
    duration_days: int = 365,
    locket_username: str = "",
    ctv_username: str = "",
    order_id: str = "",
) -> dict:
    """
    Kích hoạt Gold cho UID thông qua DNS proxy mode.
    Ghi vào bảng upgrades với status='success'.
    Proxy sẽ tự động inject Gold khi Locket gọi RevenueCat.

    Returns: dict với keys: success, order_id, expires_at, message
    """
    now = datetime.datetime.utcnow()
    expires_dt = now + datetime.timedelta(days=duration_days)
    expires_at = expires_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    if not order_id:
        import uuid
        order_id = f"DNS-{uuid.uuid4().hex[:8].upper()}"

    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        cur = conn.cursor()

        # Upsert: nếu UID đã có -> cập nhật expires, nếu chưa -> insert mới
        cur.execute(
            """
            INSERT INTO upgrades (order_id, locket_uid, locket_username, ctv_username,
                                   status, expires_at, method, created_at)
            VALUES (?, ?, ?, ?, 'success', ?, 'dns_proxy', datetime('now'))
            ON CONFLICT(order_id) DO UPDATE SET
                status='success',
                expires_at=excluded.expires_at,
                method='dns_proxy'
            """,
            (order_id, uid, locket_username, ctv_username, expires_at)
        )
        conn.commit()
        conn.close()

        return {
            "success": True,
            "order_id": order_id,
            "uid": uid,
            "expires_at": expires_at,
            "method": "dns_proxy",
            "message": f"Gold da duoc kich hoat qua DNS Proxy den {expires_dt.strftime('%d/%m/%Y')}"
        }

    except sqlite3.OperationalError as e:
        # Bảng upgrades chưa có cột method hoặc expires_at -> fallback insert đơn giản
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            cur = conn.cursor()
            cur.execute(
                """
                INSERT OR REPLACE INTO upgrades
                    (order_id, locket_uid, locket_username, ctv_username, status, created_at)
                VALUES (?, ?, ?, ?, 'success', datetime('now'))
                """,
                (order_id, uid, locket_username, ctv_username)
            )
            conn.commit()
            conn.close()
            return {
                "success": True,
                "order_id": order_id,
                "uid": uid,
                "expires_at": expires_at,
                "method": "dns_proxy",
                "message": "Gold da duoc kich hoat qua DNS Proxy"
            }
        except Exception as e2:
            return {
                "success": False,
                "order_id": order_id,
                "uid": uid,
                "message": f"DB error: {e2}"
            }
    except Exception as e:
        return {
            "success": False,
            "order_id": order_id,
            "uid": uid,
            "message": f"Error: {e}"
        }


def deactivate_dns_gold(uid: str) -> bool:
    """Hủy kích hoạt Gold cho UID (đặt status='deactivated')."""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        cur = conn.cursor()
        cur.execute(
            "UPDATE upgrades SET status='deactivated' WHERE locket_uid=? AND status='success'",
            (uid,)
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def get_active_uids() -> list:
    """Lấy danh sách tất cả UID đang có Gold active trong DB."""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cur = conn.cursor()
        cur.execute(
            "SELECT DISTINCT locket_uid FROM upgrades WHERE status='success'"
        )
        rows = cur.fetchall()
        conn.close()
        return [r[0] for r in rows if r[0]]
    except Exception:
        return []


def ensure_upgrades_table_has_columns():
    """
    Đảm bảo bảng upgrades có đủ cột cần thiết.
    Gọi khi khởi động proxy để tránh lỗi schema.
    """
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        cur = conn.cursor()

        # Thêm cột expires_at nếu chưa có
        try:
            cur.execute("ALTER TABLE upgrades ADD COLUMN expires_at TEXT")
        except sqlite3.OperationalError:
            pass  # cột đã tồn tại

        # Thêm cột method nếu chưa có
        try:
            cur.execute("ALTER TABLE upgrades ADD COLUMN method TEXT DEFAULT 'receipt'")
        except sqlite3.OperationalError:
            pass

        conn.commit()
        conn.close()
    except Exception:
        pass
