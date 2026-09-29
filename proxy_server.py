"""
proxy_server.py — RevenueCat HTTPS Reverse Proxy với Gold Injection

Flow:
  iOS App -> DNS trỏ về VPS -> VPS (port 443, cert fake revenuecat.com)
  -> Forward tới api.revenuecat.com thật -> Nhận response -> Inject Gold -> Trả về App

Chạy: python proxy_server.py
Cần: pip install aiohttp aiohttp-socks cryptography
"""

import asyncio
import ssl
import json
import os
import re
import sqlite3
import logging
import datetime
import time
import uuid
from aiohttp import web, ClientSession, TCPConnector, ClientTimeout
import socket

# --------------- Config ---------------
LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 443
UPSTREAM_HOST = "api.revenuecat.com"
UPSTREAM_PORT = 443

CERTS_DIR = os.path.join(os.path.dirname(__file__), "certs")
SERVER_CERT = os.path.join(CERTS_DIR, "server.crt")
SERVER_KEY  = os.path.join(CERTS_DIR, "server.key")

MAIN_DB_PATH = os.path.join(os.path.dirname(__file__), "bot_data.db")

# Default Gold dates
FAKE_EXPIRES_DATE       = "2099-12-31T23:59:59Z"
FAKE_PURCHASE_DATE      = "2024-01-01T00:00:00Z"
FAKE_ORIGINAL_PUR_DATE  = "2024-01-01T00:00:00Z"

# Kiem tra quyen theo database (chi UID duoc admin bat tren Bot moi len Gold)
CHECK_DB_ACTIVATION = True

from collections import deque
RECENT_LOGS = deque(maxlen=200)

class BufferHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            RECENT_LOGS.append(msg)
        except Exception:
            pass

# --------------- Logging ---------------
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("proxy")
_buf_handler = BufferHandler()
_buf_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
log.addHandler(_buf_handler)


# --------------- In-Memory Cache (High Performance) ---------------
_UID_CACHE: dict[str, tuple[bool, str, str, float]] = {}  # uid -> (is_active, pur_date, exp_date, timestamp)
CACHE_TTL = 30  # 30 giây: phản ứng cực nhanh với thay đổi

# --------------- DB Helper ---------------
def init_db():
    try:
        os.makedirs(os.path.dirname(os.path.abspath(MAIN_DB_PATH)), exist_ok=True)
        conn = sqlite3.connect(MAIN_DB_PATH, timeout=10)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS upgrades (
                order_id TEXT PRIMARY KEY,
                locket_uid TEXT,
                locket_username TEXT,
                ctv_username TEXT,
                package TEXT,
                status TEXT DEFAULT 'success',
                expires_at TEXT,
                method TEXT DEFAULT 'dns_proxy',
                created_at TEXT
            )
        """)
        # Đảm bảo cột package tồn tại nếu DB cũ chưa có
        try:
            cur.execute("ALTER TABLE upgrades ADD COLUMN package TEXT")
        except Exception:
            pass
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning(f"init_db error: {e}")

init_db()

def is_uid_activated(uid: str) -> bool:
    """Kiểm tra xem uid/username có Gold đang active trong DB hệ thống không (hỗ trợ Memory Cache)."""
    if not CHECK_DB_ACTIVATION:
        return True  # free mode: inject tất cả
    if not uid:
        return True  # mặc định request từ Locket không có uid vẫn inject
        
    clean_target = str(uid).strip()
    # Tự động kích hoạt Gold cho cài đặt mới từ App Store (Anonymous User ID)
    if clean_target.startswith("$RCAnonymousID") or clean_target.startswith("anon_") or len(clean_target) == 32:
        return True

    now = time.time()
    cached = _UID_CACHE.get(clean_target) or _UID_CACHE.get(clean_target.lstrip('@'))
    if cached:
        # Nếu đang có Gold (True): dùng cache trong CACHE_TTL (30s)
        if cached[0] and (now - cached[3] < CACHE_TTL):
            return True
        # Nếu chưa có Gold (False): chỉ cache trong 2s để khi nâng lại ăn Gold ngay tức thì
        elif not cached[0] and (now - cached[3] < 2):
            return False

    if not os.path.exists(MAIN_DB_PATH):
        return False

    try:
        conn = sqlite3.connect(MAIN_DB_PATH, timeout=5)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT created_at, expires_at, status FROM upgrades 
            WHERE (locket_uid = ? OR locket_username = ? OR locket_username = ?)
            ORDER BY created_at DESC LIMIT 1
            """,
            (clean_target, clean_target, clean_target.lstrip('@'))
        )
        row = cur.fetchone()
        conn.close()
        
        if row and row[2] == 'success':
            pur = str(row[0]) if row[0] else datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            exp = str(row[1]) if row[1] else FAKE_EXPIRES_DATE
            _UID_CACHE[clean_target] = (True, pur, exp, now)
            _UID_CACHE[clean_target.lstrip('@')] = (True, pur, exp, now)
            return True
        else:
            _UID_CACHE[clean_target] = (False, "", "", now)
            _UID_CACHE[clean_target.lstrip('@')] = (False, "", "", now)
            return False
    except Exception as e:
        log.warning(f"DB check error for {uid}: {e}")
        return False


def get_uid_dates(uid: str) -> tuple[str, str]:
    """Lấy ngày kích hoạt (purchase_date) và ngày hết hạn (expires_at) từ Cache hoặc DB."""
    cached = _UID_CACHE.get(uid)
    if cached and cached[0]:
        return cached[1], cached[2]

    # Nếu chưa có trong cache thì gọi is_uid_activated để nạp
    is_uid_activated(uid)
    cached = _UID_CACHE.get(uid)
    if cached and cached[0]:
        return cached[1], cached[2]

    pur_date = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    exp_date = FAKE_EXPIRES_DATE
    return pur_date, exp_date


# --------------- Gold Injection ---------------
FAKE_ENTITLEMENT = {
    "expires_date": FAKE_EXPIRES_DATE,
    "grace_period_expires_date": None,
    "product_identifier": "com.locket02.premium.yearly",
    "product_plan_identifier": None,
    "purchase_date": FAKE_PURCHASE_DATE,
    "store": "app_store",
    "unsubscribe_detected_at": None,
    "billing_issues_detected_at": None,
    "is_sandbox": False,
    "original_purchase_date": FAKE_ORIGINAL_PUR_DATE,
    "ownership_type": "PURCHASED",
    "period_type": "normal"
}

FAKE_SUBSCRIPTION = {
    "billing_issues_detected_at": None,
    "expires_date": FAKE_EXPIRES_DATE,
    "grace_period_expires_date": None,
    "is_sandbox": False,
    "original_purchase_date": FAKE_ORIGINAL_PUR_DATE,
    "ownership_type": "PURCHASED",
    "period_type": "normal",
    "product_plan_identifier": None,
    "purchase_date": FAKE_PURCHASE_DATE,
    "refunded_at": None,
    "store": "app_store",
    "store_transaction_id": "2000000000000001",
    "unsubscribe_detected_at": None
}

def inject_gold_into_subscriber(data: dict, uid: str) -> dict:
    """Sửa response subscriber để inject Gold entitlement chuẩn locket_gold_annual của đối thủ."""
    pur_date, exp_date = get_uid_dates(uid)
    if not exp_date or exp_date == FAKE_EXPIRES_DATE:
        exp_date = "2099-12-31T23:59:59Z"

    now_iso = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
    now_ms = int(time.time() * 1000)

    data["request_date"] = now_iso
    data["request_date_ms"] = now_ms

    subscriber = data.setdefault("subscriber", {})

    # Khớp chính xác 100% cấu trúc của đối thủ dns.nodns.vn
    subscriber["aliases"] = [uid]
    subscriber["app_user_id"] = uid
    subscriber["original_app_user_id"] = uid
    subscriber["first_seen"] = "2020-01-01T00:00:00Z"
    subscriber["last_seen"] = now_iso
    subscriber["original_purchase_date"] = "2020-01-01T00:00:00Z"
    subscriber["original_application_version"] = None
    subscriber["management_url"] = None
    subscriber["subscriber_attributes"] = {}
    subscriber["non_subscriptions"] = {}
    subscriber["other_purchases"] = {}

    # Entitlement: product_identifier bắt buộc là 'locket_gold_annual'
    subscriber["entitlements"] = {
        "Gold": {
            "expires_date": exp_date,
            "grace_period_expires_date": None,
            "product_identifier": "locket_gold_annual",
            "purchase_date": "2020-01-01T00:00:00Z"
        }
    }

    # Subscriptions: 'locket_gold_annual' là mã SKU gốc mở Hiện mã QR, Quay video 10s & Đăng riêng tư
    subscriber["subscriptions"] = {
        "locket_gold_annual": {
            "auto_resume_date": None,
            "billing_issues_detected_at": None,
            "expires_date": exp_date,
            "grace_period_expires_date": None,
            "is_sandbox": False,
            "original_purchase_date": "2020-01-01T00:00:00Z",
            "ownership_type": "PURCHASED",
            "period_type": "normal",
            "product_plan_identifier": None,
            "purchase_date": "2020-01-01T00:00:00Z",
            "refunded_at": None,
            "store": "app_store",
            "unsubscribe_detected_at": None
        }
    }

    # Bỏ các header/trường lạ không chuẩn
    data.pop("Attention", None)

    log.info(f"[INJECT] Gold (locket_gold_annual) injected for uid={uid}, expires={exp_date}")
    return data


def inject_gold_into_receipt(data: dict, uid: str) -> dict:
    """Sửa response POST /receipts để báo Gold active."""
    return inject_gold_into_subscriber(data, uid)


# --------------- Regex Patterns ---------------
# Match GET /v1/subscribers/{uid}  (uid không chứa /)
RE_SUBSCRIBERS = re.compile(r"^/v1/subscribers/([^/]+)$")
# Match POST /v1/subscribers/{uid}/receipts  hoặc  POST /v1/receipts
RE_RECEIPTS    = re.compile(r"^(/v1/subscribers/[^/]+)?/v1/receipts$|^/v1/receipts$")


def extract_uid_from_path(path: str) -> str | None:
    """Lấy app_user_id từ URL path."""
    m = RE_SUBSCRIBERS.match(path)
    if m:
        from urllib.parse import unquote
        return unquote(m.group(1))
    return None


# --------------- Upstream Request ---------------
DEFAULT_VPS_IP = "54.179.86.163"
INTERCEPT_DOMAINS = [
    "api.revenuecat.com",
    "firebaseremoteconfig.googleapis.com",
    "firebaseremoteconfigrealtime.googleapis.com",
    "firebaselogging.googleapis.com",
    "api.locketcamera.com",
]

def is_intercepted_domain(qname: str) -> bool:
    if not qname:
        return False
    q = qname.lower().strip(".")
    for d in INTERCEPT_DOMAINS:
        if q == d or q.endswith("." + d):
            return True
    return False

async def forward_to_upstream(target_host: str, method: str, path: str, headers: dict, body: bytes) -> tuple:
    """Gửi request tới upstream host thật, trả về (status, headers, body_bytes)."""
    url = f"https://{target_host}{path}"

    fwd_headers = {
        k: v for k, v in headers.items()
        if k.lower() not in (
            "host", "content-length", "transfer-encoding", "connection", "accept-encoding",
            "if-none-match", "if-modified-since", "x-revenuecat-etag"
        )
    }
    fwd_headers["Host"] = target_host
    fwd_headers["Accept-Encoding"] = "gzip, deflate"
    # Giả lập IP và vị trí địa lý US để vượt kiểm tra vùng địa lý của Google Firebase & RevenueCat
    fwd_headers["X-Forwarded-For"] = "1.1.1.1"
    fwd_headers["CF-IPCountry"] = "US"
    fwd_headers["X-Country-Code"] = "US"

    connector = TCPConnector(family=socket.AF_INET)
    timeout = ClientTimeout(total=20)

    async with ClientSession(connector=connector, timeout=timeout) as session:
        req_kwargs = {
            "headers": fwd_headers,
            "allow_redirects": False,
            "ssl": True,
        }
        if body:
            req_kwargs["data"] = body

        async with session.request(method, url, **req_kwargs) as resp:
            resp_body = await resp.read()
            resp_headers = dict(resp.headers)
            for k in list(resp_headers.keys()):
                if k.lower() in ("content-encoding", "content-length", "etag", "x-revenuecat-etag", "last-modified"):
                    del resp_headers[k]
            resp_headers["Content-Length"] = str(len(resp_body))
            return resp.status, resp_headers, resp_body

async def forward_to_revenuecat(method: str, path: str, headers: dict, body: bytes) -> tuple:
    return await forward_to_upstream(UPSTREAM_HOST, method, path, headers, body)


# --------------- Request Handler ---------------
async def handle_request(request: web.Request) -> web.Response:
    path = request.path
    if request.query_string:
        path = f"{path}?{request.query_string}"

    method = request.method
    body = await request.read()
    headers = dict(request.headers)
    clean_path = request.path
    raw_host = headers.get("Host", "").split(":")[0].strip().lower()

    log.info(f"[{method}] Host={raw_host} {path}")

    # 0. DoH (DNS over HTTPS RFC 8484)
    if clean_path == "/dns-query":
        return await handle_doh(request)

    # Xử lý API nội bộ từ Web server (160.22.107.114) đồng bộ kích hoạt
    if clean_path == "/internal/activate" and method == "POST":
        try:
            payload = json.loads(body)
            u_id = payload.get("uid")
            username = payload.get("username", "")
            package = payload.get("package", "yearly")
            expires_at = payload.get("expires_at")
            created_at = payload.get("created_at") or datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            order_id = payload.get("order_id") or f"WEB-{uuid.uuid4().hex[:8].upper()}"

            if not u_id or not expires_at:
                return web.json_response({"status": "error", "message": "Missing uid or expires_at"}, status=400)

            # Cập nhật ngay vào RAM Cache để người dùng mở app ăn Gold lập tức không độ trễ
            _UID_CACHE[u_id] = (True, created_at, expires_at, time.time())
            if username:
                _UID_CACHE[username] = (True, created_at, expires_at, time.time())
                _UID_CACHE[username.lstrip('@')] = (True, created_at, expires_at, time.time())

            try:
                conn = sqlite3.connect(MAIN_DB_PATH, timeout=5)
                cur = conn.cursor()
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS upgrades (
                        order_id TEXT PRIMARY KEY,
                        locket_uid TEXT,
                        locket_username TEXT,
                        ctv_username TEXT,
                        package TEXT,
                        status TEXT DEFAULT 'success',
                        expires_at TEXT,
                        method TEXT DEFAULT 'dns_proxy',
                        created_at TEXT
                    )
                """)
                try:
                    cur.execute("ALTER TABLE upgrades ADD COLUMN package TEXT")
                except Exception:
                    pass
                cur.execute("""
                    INSERT INTO upgrades (order_id, locket_uid, locket_username, ctv_username, package, status, expires_at, method, created_at)
                    VALUES (?, ?, ?, 'web_api', ?, 'success', ?, 'dns_proxy', ?)
                    ON CONFLICT(order_id) DO UPDATE SET
                        locket_uid=excluded.locket_uid,
                        locket_username=excluded.locket_username,
                        package=excluded.package,
                        status='success',
                        expires_at=excluded.expires_at,
                        created_at=excluded.created_at
                """, (order_id, u_id, username, package, expires_at, created_at))
                conn.commit()
                conn.close()
            except Exception as db_err:
                log.warning(f"[INTERNAL_ACTIVATE] DB persist warning: {db_err}")

            log.info(f"[INTERNAL_ACTIVATE] Synced uid={u_id} (@{username}) pkg={package} exp={expires_at}")
            return web.json_response({"status": "success", "message": "Synced to proxy", "order_id": order_id})
        except Exception as ex:
            log.error(f"[INTERNAL_ACTIVATE] Error: {ex}")
            return web.json_response({"status": "error", "message": str(ex)}, status=500)

    # -------------------------------------------------------------
    # API nội bộ: Nhận lệnh hủy kích hoạt từ Telegram Bot hoặc Web Server
    # -------------------------------------------------------------
    if path == "/internal/deactivate" and method == "POST":
        try:
            data = json.loads(body.decode("utf-8")) if body else {}
            u_id = str(data.get("uid", "")).strip()
            username = str(data.get("username", "")).strip()
            target = u_id or username
            if not target:
                return web.json_response({"status": "error", "message": "Missing uid or username"}, status=400)

            # 1. Truy vấn DB để tìm tất cả UID và Username liên quan
            conn = sqlite3.connect(MAIN_DB_PATH, timeout=5)
            cur = conn.cursor()
            cur.execute("""
                SELECT locket_uid, locket_username FROM upgrades
                WHERE (locket_uid = ? OR locket_username = ? OR locket_username = ?)
            """, (u_id, username, username.lstrip('@')))
            rows = cur.fetchall()

            # 2. Xóa và áp đặt negative cache ngay tức thì cho mọi alias
            now = time.time()
            all_keys = {u_id, username, username.lstrip('@'), target, target.lstrip('@')}
            for r_uid, r_user in rows:
                if r_uid:
                    all_keys.add(r_uid.strip())
                if r_user:
                    all_keys.add(r_user.strip())
                    all_keys.add(r_user.strip().lstrip('@'))

            for k in all_keys:
                if k:
                    _UID_CACHE[k] = (False, "", "", now)

            # 3. Cập nhật trạng thái trong SQLite
            cur.execute("""
                UPDATE upgrades SET status='deactivated'
                WHERE (locket_uid = ? OR locket_username = ? OR locket_username = ?)
            """, (u_id, username, username.lstrip('@')))
            affected = cur.rowcount
            conn.commit()
            conn.close()

            log.info(f"[INTERNAL_DEACTIVATE] Deactivated target={target} (affected={affected}, purged_keys={len(all_keys)})")
            return web.json_response({"status": "success", "message": f"Deactivated {target}", "affected": affected})
        except Exception as ex:
            log.error(f"[INTERNAL_DEACTIVATE] Error: {ex}")
            return web.json_response({"status": "error", "message": str(ex)}, status=500)

    # -------------------------------------------------------------
    # API nội bộ: Tra cứu trạng thái Gold thời gian thực từ VPS Proxy cho Web
    # -------------------------------------------------------------
    if clean_path == "/internal/check" and method in ("GET", "POST"):
        try:
            target = request.query.get("target", "").strip()
            if not target and body:
                try:
                    data = json.loads(body.decode("utf-8"))
                    target = str(data.get("target") or data.get("uid") or data.get("username") or "").strip()
                except Exception:
                    pass
            clean_target = str(target).strip()
            is_active = is_uid_activated(clean_target)
            pur, exp = get_uid_dates(clean_target) if is_active else ("", "")
            return web.json_response({
                "status": "success",
                "target": clean_target,
                "is_gold": is_active,
                "expires_at": exp,
                "start_at": pur
            })
        except Exception as ex:
            log.error(f"[INTERNAL_CHECK] Error: {ex}")
            return web.json_response({"status": "error", "message": str(ex)}, status=500)

    # -------------------------------------------------------------
    # API nội bộ: Xem nhật ký real-time của Proxy Server
    # -------------------------------------------------------------
    if clean_path == "/internal/logs" and method == "GET":
        try:
            log_text = "\n".join(str(x) for x in list(RECENT_LOGS))
            return web.Response(text=log_text or "No logs recorded yet.", content_type="text/plain", charset="utf-8")
        except Exception as e:
            return web.Response(text=f"Error reading logs: {e}", content_type="text/plain", charset="utf-8")

    # -------------------------------------------------------------
    # API nội bộ: Kiểm tra phiên bản proxy đang chạy trên VPS
    # -------------------------------------------------------------
    if clean_path == "/internal/version" and method == "GET":
        return web.json_response({
            "version": "2.4.0",
            "features": "locket_gold_annual, check_db_true, exact_competitor_match, qr_code, 10s_video, private_post",
            "time": datetime.datetime.utcnow().isoformat()
        })

    # -------------------------------------------------------------
    # Firebase Logging: Chặn 200 OK ngay lập tức (giống competitor)
    # -------------------------------------------------------------
    if "firebaselogging" in raw_host:
        return web.Response(status=200, content_type="application/json", text="{}")

    # -------------------------------------------------------------
    # Firebase Remote Config Realtime: Chặn hoàn toàn HTTP 404 (giống competitor dns.nodns.vn)
    # Ngăn chặn Google Realtime SSE stream push đè config gốc làm mất 10s video, đăng riêng tư và QR
    # -------------------------------------------------------------
    if "firebaseremoteconfigrealtime" in raw_host:
        return web.Response(status=404, text="Not found")

    # -------------------------------------------------------------
    # Firebase Remote Config: Forward sạch tới Google kèm US Spoofing (giống competitor)
    if "firebaseremoteconfig" in raw_host or "firebaseremoteconfig" in path:
        target_upstream = "firebaseremoteconfig.googleapis.com"
        try:
            status, resp_headers, resp_body = await forward_to_upstream(target_upstream, method, path, headers, body)
            log.info(f"[FIREBASE] Upstream status {status}, size {len(resp_body)} bytes")
        except Exception as e:
            log.error(f"[FIREBASE] Upstream error: {e}")
            status = 502
            resp_headers = {}
            resp_body = b""

        clean_resp_headers = {
            k: v for k, v in resp_headers.items()
            if k.lower() not in (
                "transfer-encoding", "connection", "keep-alive",
                "proxy-authenticate", "proxy-authorization", "te", "trailers", "upgrade", "content-encoding"
            )
        }
        return web.Response(status=status, headers=clean_resp_headers, body=resp_body)

    # -------------------------------------------------------------
    # Transparent Forward cho api.locketcamera.com
    # -------------------------------------------------------------
    if "locketcamera" in raw_host:
        target_upstream = "api.locketcamera.com"
        try:
            status, resp_headers, resp_body = await forward_to_upstream(target_upstream, method, path, headers, body)
        except Exception as e:
            log.error(f"[LOCKET_API] Upstream error: {e}")
            return web.Response(status=502, text=f"Locket upstream error: {e}")

        clean_resp_headers = {
            k: v for k, v in resp_headers.items()
            if k.lower() not in (
                "transfer-encoding", "connection", "keep-alive",
                "proxy-authenticate", "proxy-authorization", "te", "trailers", "upgrade", "content-encoding"
            )
        }
        return web.Response(status=status, headers=clean_resp_headers, body=resp_body)

    # Forward request tới RevenueCat thật
    try:
        status, resp_headers, resp_body = await forward_to_revenuecat(method, path, headers, body)
    except Exception as e:
        log.error(f"Upstream error: {e}")
        return web.Response(status=502, text=f"Upstream error: {e}")

    # Quyết định có inject không
    should_inject = False
    uid = None

    clean_path = request.path  # không có query string

    m_sub = RE_SUBSCRIBERS.match(clean_path)
    if m_sub and method == "GET":
        from urllib.parse import unquote
        uid = unquote(m_sub.group(1))
        should_inject = is_uid_activated(uid)

    elif clean_path.endswith("/receipts") and method == "POST":
        # Lấy uid từ request body hoặc path
        try:
            req_json = json.loads(body)
            uid = req_json.get("app_user_id", "")
        except Exception:
            uid = ""
        if not uid:
            # thử lấy từ path /v1/subscribers/{uid}/receipts
            m2 = re.match(r"^/v1/subscribers/([^/]+)/receipts$", clean_path)
            if m2:
                from urllib.parse import unquote
                uid = unquote(m2.group(1))
        should_inject = uid and is_uid_activated(uid)

    if should_inject and status in (200, 201):
        try:
            data = json.loads(resp_body)
            if clean_path.endswith("/receipts"):
                data = inject_gold_into_receipt(data, uid)
            else:
                data = inject_gold_into_subscriber(data, uid)
            resp_body = json.dumps(data).encode("utf-8")
            resp_headers["Content-Length"] = str(len(resp_body))
            resp_headers.pop("Content-Encoding", None)  # body đã decode, bỏ gzip
        except Exception as e:
            log.error(f"Inject error for {uid}: {e}")
    elif should_inject:
        # RevenueCat trả lỗi nhưng ta vẫn cần trả fake Gold
        # Tạo fake subscriber response hoàn toàn
        log.info(f"[INJECT OVERRIDE] Upstream {status}, generating clean fake response for {uid}")
        fake = inject_gold_into_subscriber({}, uid)
        resp_body = json.dumps(fake).encode("utf-8")
        status = 200
        resp_headers = {
            "Content-Type": "application/json",
            "Content-Length": str(len(resp_body))
        }

    # Lọc hop-by-hop headers trước khi trả về
    clean_resp_headers = {
        k: v for k, v in resp_headers.items()
        if k.lower() not in (
            "transfer-encoding", "connection", "keep-alive",
            "proxy-authenticate", "proxy-authorization", "te", "trailers", "upgrade", "content-encoding"
        )
    }

    return web.Response(
        status=status,
        headers=clean_resp_headers,
        body=resp_body
    )


# --------------- DNS Server (UDP port 53 & DoH HTTPS) ---------------
class DnsProtocol(asyncio.DatagramProtocol):
    """
    DNS server hỗ trợ cả UDP 53 và DoH RFC 8484:
    Trả về VPS IP cho các domain RevenueCat, Firebase Remote Config, Locket Camera;
    Forward tất cả query khác tới 8.8.8.8 (Google DNS).
    """

    UPSTREAM_DNS = ("8.8.8.8", 53)

    def __init__(self, vps_ip: str):
        self.vps_ip = vps_ip or DEFAULT_VPS_IP
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data: bytes, addr):
        asyncio.ensure_future(self._handle(data, addr))

    async def _handle(self, data: bytes, addr):
        try:
            if len(data) < 13:
                return

            qname = self._parse_qname(data, 12)
            if is_intercepted_domain(qname):
                log.info(f"[DNS UDP] {addr[0]} asked for {qname} -> {self.vps_ip}")
                response = self._build_a_response(data, self.vps_ip)
                self.transport.sendto(response, addr)
            else:
                result = await self.forward_dns_packet(data)
                if result:
                    self.transport.sendto(result, addr)
        except Exception as e:
            log.debug(f"DNS error: {e}")

    @staticmethod
    def _parse_qname(data: bytes, offset: int) -> str:
        labels = []
        while offset < len(data):
            length = data[offset]
            if length == 0:
                break
            if length & 0xC0 == 0xC0:  # pointer
                ptr = ((length & 0x3F) << 8) | data[offset + 1]
                labels.append(DnsProtocol._parse_qname(data, ptr))
                break
            offset += 1
            labels.append(data[offset:offset + length].decode("ascii", errors="ignore"))
            offset += length
        return ".".join(labels)

    @staticmethod
    def _build_a_response(query: bytes, ip: str) -> bytes:
        txid = query[:2]
        flags = b"\x81\x80"  # standard response
        counts = b"\x00\x01\x00\x01\x00\x00\x00\x00"
        i = 12
        while i < len(query) and query[i] != 0:
            if query[i] & 0xC0 == 0xC0:
                i += 2
                break
            i += query[i] + 1
        else:
            i += 1
        question = query[12:i + 4]

        ptr = b"\xC0\x0C"
        type_a = b"\x00\x01"
        class_in = b"\x00\x01"
        ttl = b"\x00\x00\x00\x3C"  # 60s
        rdlen = b"\x00\x04"
        rdata = bytes(int(x) for x in ip.split("."))
        answer = ptr + type_a + class_in + ttl + rdlen + rdata

        return txid + flags + counts + question + answer

    @classmethod
    async def forward_dns_packet(cls, data: bytes) -> bytes | None:
        try:
            loop = asyncio.get_running_loop()
            fut = loop.create_future()

            class _Proto(asyncio.DatagramProtocol):
                def datagram_received(self, d, _):
                    if not fut.done():
                        fut.set_result(d)

            transport, _ = await loop.create_datagram_endpoint(
                _Proto, remote_addr=cls.UPSTREAM_DNS
            )
            transport.sendto(data)
            try:
                result = await asyncio.wait_for(fut, timeout=3.0)
                return result
            except asyncio.TimeoutError:
                return None
            finally:
                transport.close()
        except Exception:
            return None


async def handle_doh(request: web.Request) -> web.Response:
    vps_ip = request.app.get("vps_ip", DEFAULT_VPS_IP)
    query_data = None
    if request.method == "POST":
        query_data = await request.read()
    elif request.method == "GET":
        dns_param = request.query.get("dns")
        if dns_param:
            import base64
            rem = len(dns_param) % 4
            if rem > 0:
                dns_param += "=" * (4 - rem)
            try:
                query_data = base64.urlsafe_b64decode(dns_param)
            except Exception:
                query_data = None

    if not query_data:
        return web.Response(status=400, text="Missing or invalid DNS query payload")

    try:
        qname = DnsProtocol._parse_qname(query_data, 12)
        if is_intercepted_domain(qname):
            log.info(f"[DoH HTTPS] Intercepted {qname} -> {vps_ip}")
            resp_bytes = DnsProtocol._build_a_response(query_data, vps_ip)
            return web.Response(
                body=resp_bytes,
                content_type="application/dns-message",
                headers={"Cache-Control": "max-age=60"}
            )

        upstream_resp = await DnsProtocol.forward_dns_packet(query_data)
        if upstream_resp:
            return web.Response(
                body=upstream_resp,
                content_type="application/dns-message",
                headers={"Cache-Control": "max-age=60"}
            )
    except Exception as e:
        log.error(f"[DoH Error] {e}")

    return web.Response(status=504, text="DNS Resolution Failed")


# --------------- Main ---------------
async def main():
    import argparse
    parser = argparse.ArgumentParser(description="LocketGold DNS+HTTPS Proxy")
    parser.add_argument("--host", default=LISTEN_HOST)
    parser.add_argument("--port", type=int, default=LISTEN_PORT)
    parser.add_argument("--no-dns", action="store_true", help="Disable built-in DNS server")
    parser.add_argument("--free-mode", action="store_true", help="Inject Gold for ALL users (no DB check)")
    parser.add_argument("--vps-ip", default=DEFAULT_VPS_IP, help="VPS public IP (for DNS responses)")
    args = parser.parse_args()

    global CHECK_DB_ACTIVATION
    if args.free_mode:
        CHECK_DB_ACTIVATION = False
        log.warning("[!] FREE MODE: Injecting Gold for ALL users, no DB check!")

    # SSL context
    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_ctx.load_cert_chain(SERVER_CERT, SERVER_KEY)

    # HTTPS server
    app = web.Application()
    app["vps_ip"] = args.vps_ip or DEFAULT_VPS_IP
    app.router.add_route("*", "/{path_info:.*}", handle_request)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, args.host, args.port, ssl_context=ssl_ctx)
    await site.start()
    log.info(f"[*] HTTPS Proxy listening on {args.host}:{args.port}")

    # DNS server
    if not args.no_dns and args.vps_ip:
        loop = asyncio.get_running_loop()
        await loop.create_datagram_endpoint(
            lambda: DnsProtocol(args.vps_ip),
            local_addr=("0.0.0.0", 53)
        )
        log.info(f"[*] DNS Server listening on 0.0.0.0:53 -> VPS IP: {args.vps_ip}")
    elif not args.no_dns and not args.vps_ip:
        log.warning("[!] DNS server skipped: --vps-ip not provided. Use --vps-ip <your_vps_public_ip>")

    log.info("[*] Proxy ready. Waiting for connections...")
    await asyncio.Event().wait()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
