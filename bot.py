"""
bot.py — Telegram Bot quan ly LocketGold DNS Proxy
Ho tro nhap: Username Locket (@abc, abc), Link moi (locket.cam/links/...), hoac UID truc tiep.
"""

import os, sys, json, asyncio, sqlite3, subprocess, aiohttp, ssl, uuid, re
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, ContextTypes,
    CallbackQueryHandler, MessageHandler, filters
)

# ===== CONFIG =====
BOT_TOKEN    = "8811544353:AAF_WvyiObO0SQ4eFntICTsIPAERfyVRW0Q"
ADMIN_IDS    = [6630785148]
PROXY_HOST   = os.environ.get("PROXY_HOST", "127.0.0.1")
PROXY_PORT   = int(os.environ.get("PROXY_PORT", "8443"))
SERVICE_NAME = "locketgold-proxy"
DB_PATH      = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "..", "bot_data.db"))


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def admin_only(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("Khong co quyen.")
            return
        return await func(update, ctx, *args, **kwargs)
    wrapper.__name__ = func.__name__
    return wrapper


# ===== Locket Resolver =====

def normalize_locket_input(val: str) -> str:
    if not val:
        return ""
    val = str(val).strip()

    m = re.search(r'/(?:links|invites)/([A-Za-z0-9]{28})', val)
    if m:
        return m.group(1)

    m_short = re.search(r'(https?://(?:locket\.cam|locket\.camera)/(?:links|invites)/[A-Za-z0-9]+)', val)
    if m_short:
        return m_short.group(1)

    val = re.sub(r'^https?://', '', val)
    val = re.sub(r'^www\.', '', val)

    m_short2 = re.match(r'(?:locket\.cam|locket\.camera)/((?:links|invites)/[A-Za-z0-9]+)$', val.split('?')[0].split('#')[0].strip('/'))
    if m_short2:
        return f"https://{val.split('?')[0].split('#')[0].strip('/')}"

    val = re.sub(r'^(?:locket\.cam|locket\.camera)/', '', val)
    val = val.split('?')[0].split('#')[0].strip('/')

    m = re.search(r'^(?:links|invites)/([A-Za-z0-9]{28})$', val)
    if m:
        return m.group(1)

    if val.startswith('@'):
        val = val[1:]

    return val


async def resolve_uid(username: str) -> tuple[str | None, str]:
    """
    Tra ve (uid, display_username).
    Neu target la UID san (28 ky tu): tra ve luon.
    Neu la username/link: crawl locket.cam de lay UID that.
    """
    if not username:
        return None, ""

    raw_input = username.strip()
    target = normalize_locket_input(raw_input)
    if not target:
        return None, ""

    # Da la UID (28 ky tu alphanumeric)
    if re.match(r'^[A-Za-z0-9]{28}$', target):
        return target, raw_input.lstrip('@')

    # Neu la link
    if target.startswith('http'):
        resolve_url = target
    else:
        resolve_url = f"https://locket.cam/{target}"

    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
        "Accept": "text/html"
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(resolve_url, headers=headers, allow_redirects=True, timeout=12) as res:
                html = await res.text()
                redirect_url = str(res.url)

                def extract(text):
                    if not text:
                        return None
                    m = re.search(r'/(?:invites|links)/([A-Za-z0-9]{28})', text)
                    if m:
                        return m.group(1)
                    lp = re.search(r'link=([^\s\"\'\\>]+)', text)
                    if lp:
                        try:
                            d = lp.group(1).replace('%3A', ':').replace('%2F', '/')
                            dm = re.search(r'/(?:invites|links)/([A-Za-z0-9]{28})', d)
                            if dm:
                                return dm.group(1)
                        except Exception:
                            pass
                    return None

                uid = extract(redirect_url) or extract(html)
                clean_name = raw_input.lstrip('@').replace('https://locket.cam/', '').replace('https://locket.camera/', '')
                return uid, clean_name
    except Exception:
        return None, raw_input


# ===== System & Proxy Helpers =====

def run_cmd(cmd: str) -> str:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        return (r.stdout + r.stderr).strip()
    except Exception as e:
        return str(e)


def get_service_status() -> str:
    return run_cmd(f"systemctl is-active {SERVICE_NAME}").strip()


def get_logs(n: int = 30) -> str:
    return run_cmd(f"journalctl -u {SERVICE_NAME} -n {n} --no-pager --output=short") or "Khong co log."


async def test_inject(uid: str = "HEALTHCHECK") -> dict:
    url = f"https://{PROXY_HOST}:{PROXY_PORT}/v1/subscribers/{uid}"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, ssl=ctx, timeout=aiohttp.ClientTimeout(total=8)) as r:
                data = await r.json()
                gold = (data.get("subscriber", {})
                            .get("entitlements", {})
                            .get("Gold"))
                return {
                    "ok": True,
                    "has_gold": gold is not None,
                    "expires": gold.get("expires_date") if gold else None,
                    "status": r.status
                }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ===== DB CRUD =====

def db_activate(uid: str, username: str, days: int) -> dict:
    start_date = datetime.utcnow()
    if days >= 99999:
        expires_dt = datetime(2099, 12, 31, 23, 59, 59)
    else:
        expires_dt = start_date + timedelta(days=days)

    start_str   = start_date.strftime("%Y-%m-%dT%H:%M:%SZ")
    expires_str = expires_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    order_id    = f"DNS-{uuid.uuid4().hex[:8].upper()}"

    # 1. Lưu vào các DB SQLite trên server
    possible_paths = [
        DB_PATH,
        os.path.join(os.path.dirname(__file__), "bot_data.db"),
        os.path.join(os.path.dirname(__file__), "..", "bot_data.db")
    ]
    seen = set()
    for p in possible_paths:
        abs_p = os.path.abspath(p)
        if abs_p in seen:
            continue
        seen.add(abs_p)
        try:
            os.makedirs(os.path.dirname(abs_p), exist_ok=True)
            conn = sqlite3.connect(abs_p, timeout=5)
            cur  = conn.cursor()
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
                INSERT INTO upgrades
                    (order_id, locket_uid, locket_username, ctv_username, package,
                     status, expires_at, method, created_at)
                VALUES (?, ?, ?, 'admin', 'yearly', 'success', ?, 'dns_proxy', ?)
                ON CONFLICT(order_id) DO UPDATE SET
                    status='success', expires_at=excluded.expires_at
            """, (order_id, uid, username, expires_str, start_str))
            conn.commit()
            conn.close()
        except Exception:
            pass

    # 2. Bắn sang Proxy Server (port 443 localhost) để nạp RAM Cache lập tức không độ trễ
    try:
        import urllib.request, ssl, json
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        payload = json.dumps({
            "uid": uid,
            "username": username,
            "package": "yearly",
            "start_at": start_str,
            "expires_at": expires_str,
            "order_id": order_id
        }).encode("utf-8")
        req = urllib.request.Request("https://127.0.0.1/internal/activate", data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, context=ctx, timeout=3)
    except Exception:
        pass

    # 3. Đồng bộ sang Web Server aaPanel để web cập nhật DB ngay lập tức
    try:
        import urllib.request, ssl, json
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        payload = json.dumps({
            "uid": uid,
            "username": username,
            "order_id": order_id,
            "package": "yearly",
            "start_at": start_str,
            "expires_at": expires_str
        }).encode("utf-8")
        req = urllib.request.Request("https://locketgold.shop/api/internal/activate", data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, context=ctx, timeout=3)
    except Exception:
        pass

    return {
        "success": True, "order_id": order_id,
        "start": start_str, "expires": expires_str
    }


def db_deactivate(target: str) -> bool:
    clean_target = str(target).strip()
    norm = clean_target.lstrip('@')
    affected = 0
    resolved_uid = ""
    resolved_user = norm

    # 1. Cập nhật các file SQLite có thể có trên server và tìm UID
    possible_paths = [
        DB_PATH,
        os.path.join(os.path.dirname(__file__), "bot_data.db"),
        os.path.join(os.path.dirname(__file__), "..", "bot_data.db")
    ]
    seen = set()
    for p in possible_paths:
        abs_p = os.path.abspath(p)
        if abs_p in seen or not os.path.exists(abs_p):
            continue
        seen.add(abs_p)
        try:
            conn = sqlite3.connect(abs_p, timeout=5)
            cur  = conn.cursor()
            cur.execute("""
                SELECT locket_uid, locket_username FROM upgrades
                WHERE (locket_uid=? OR locket_username=? OR locket_username=?)
            """, (clean_target, clean_target, norm))
            row = cur.fetchone()
            if row:
                if row[0]: resolved_uid = row[0]
                if row[1]: resolved_user = row[1]

            cur.execute("""
                UPDATE upgrades SET status='deactivated'
                WHERE (locket_uid=? OR locket_username=? OR locket_username=?)
            """, (clean_target, clean_target, norm))
            affected += cur.rowcount
            conn.commit()
            conn.close()
        except Exception:
            pass

    # 2. Bắn sang Proxy Server (port 443 localhost) để xóa sạch RAM Cache ngay tức thì
    try:
        import urllib.request, ssl, json
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        payload = json.dumps({"uid": resolved_uid or clean_target, "username": resolved_user or norm}).encode("utf-8")
        req = urllib.request.Request("https://127.0.0.1/internal/deactivate", data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, context=ctx, timeout=3)
    except Exception:
        pass

    # 3. Đồng bộ sang Web Server aaPanel (https://locketgold.shop/api/internal/deactivate)
    try:
        import urllib.request, ssl, json
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        payload = json.dumps({"target": clean_target, "uid": resolved_uid, "username": resolved_user}).encode("utf-8")
        req = urllib.request.Request("https://locketgold.shop/api/internal/deactivate", data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, context=ctx, timeout=3)
    except Exception:
        pass

    return affected > 0 or True


def db_list_active() -> list:
    if not os.path.exists(DB_PATH):
        return []
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cur  = conn.cursor()
        cur.execute("""
            SELECT locket_uid, locket_username, expires_at, created_at
            FROM upgrades WHERE status='success'
            ORDER BY created_at DESC
        """)
        rows = cur.fetchall()
        conn.close()
        return rows
    except Exception:
        return []


# ===== Keyboards =====

def kb_duration(uid: str, username: str) -> InlineKeyboardMarkup:
    # callback format: dur|<days>|<uid>|<username>
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("1 Nam (365 ngay)", callback_data=f"dur|365|{uid}|{username}"),
            InlineKeyboardButton("Vinh Vien",        callback_data=f"dur|99999|{uid}|{username}"),
        ],
        [InlineKeyboardButton("Huy bo",              callback_data="dur|cancel||")]
    ])


def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Trang thai",  callback_data="menu|status"),
            InlineKeyboardButton("Danh sach",   callback_data="menu|list"),
            InlineKeyboardButton("Test proxy",  callback_data="menu|test"),
        ],
        [
            InlineKeyboardButton("📥 Lay File DNS Profile", callback_data="menu|dns"),
        ]
    ])


# ===== Handlers =====

@admin_only
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "*LocketGold DNS Proxy Bot*\n\n"
        "Lenh thao tac:\n"
        "/up `<username/link/uid>` — Kich hoat Gold\n"
        "/activate `<username/link/uid>` — Kich hoat Gold\n"
        "/status — Kiem tra proxy & service\n"
        "/test `[uid]` — Test inject response\n"
        "/logs `[n]` — Xem log he thong\n"
        "/list — Danh sach acc dang co Gold\n"
        "/dns — Lay file cau hinh DNS Profile (.mobileconfig)\n"
        "/deactivate `<username/uid>` — Huy Gold\n"
        "/restart — Khoi dong lai proxy\n\n"
        "_Co the gui truc tiep username hoac link Locket vao chat de nang Gold._"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb_main())


async def handle_activate_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE, raw_input: str):
    raw_input = raw_input.strip()
    if not raw_input:
        await update.message.reply_text("Vui long nhap username, link hoac UID Locket.")
        return

    msg = await update.message.reply_text(f"Dang tim UID cho: `{raw_input}`...", parse_mode="Markdown")

    uid, clean_user = await resolve_uid(raw_input)

    if not uid:
        await msg.edit_text(
            f"❌ Khong tim thay UID cua `{raw_input}`!\n"
            f"Kiem tra lai username hoac thu gui full link moi Locket.",
            parse_mode="Markdown"
        )
        return

    now_str = datetime.now().strftime("%d/%m/%Y")
    display_name = f"@{clean_user}" if clean_user else "N/A"

    text = (
        f"🎯 *Thong tin tai khoan Locket:*\n\n"
        f"• Username: *{display_name}*\n"
        f"• UID: `{uid}`\n"
        f"• Ngay bat dau: `{now_str}`\n\n"
        f"Chon thoi han can nang cap:"
    )

    await msg.edit_text(text, parse_mode="Markdown", reply_markup=kb_duration(uid, clean_user))


@admin_only
async def cmd_activate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text(
            "Cach dung: `/activate <username_hoac_link_hoac_uid>`\n\n"
            "Vi du:\n"
            "• `/activate @nguyenvana`\n"
            "• `/activate locket.cam/links/abcdef...`\n"
            "• `/activate 28_ky_tu_uid_o_day`",
            parse_mode="Markdown"
        )
        return

    target = " ".join(ctx.args)
    await handle_activate_input(update, ctx, target)


@admin_only
async def handle_direct_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Khi admin gui truc tiep username/link vao chat khong can go lenh."""
    text = update.message.text.strip()
    if text.startswith("/"):
        return
    await handle_activate_input(update, ctx, text)


@admin_only
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("Dang kiem tra he thong...")
    svc  = get_service_status()
    res  = await test_inject()
    e_svc   = "✅" if svc == "active" else "❌"
    e_proxy = "✅" if (res.get("ok") and res.get("has_gold")) else "❌"

    text = (
        f"*Trang thai He Thong:*\n\n"
        f"• Service: {e_svc} `{svc}`\n"
        f"• Inject Gold: {e_proxy}\n"
        f"• Proxy Port: `{PROXY_HOST}:{PROXY_PORT}`"
    )
    if res.get("error"):
        text += f"\n• Chi tiet loi: `{res['error']}`"

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Refresh", callback_data="menu|status"),
        InlineKeyboardButton("Restart", callback_data="menu|restart"),
    ]])
    await msg.edit_text(text, parse_mode="Markdown", reply_markup=kb)


@admin_only
async def cmd_test(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = ctx.args[0] if ctx.args else "TESTUID_BOT"
    msg = await update.message.reply_text(f"Dang test UID: `{uid}`...", parse_mode="Markdown")
    res = await test_inject(uid)
    if res.get("ok") and res.get("has_gold"):
        text = f"✅ *Thanh cong!*\nUID: `{uid}`\nExpires: `{res['expires']}`"
    elif res.get("ok"):
        text = f"⚠️ Proxy OK nhung khong thay Gold\nHTTP: `{res['status']}`"
    else:
        text = f"❌ Loi: `{res.get('error')}`"
    await msg.edit_text(text, parse_mode="Markdown")


@admin_only
async def cmd_logs(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    n    = int(ctx.args[0]) if ctx.args else 25
    logs = get_logs(n)
    if len(logs) > 3800:
        logs = "...(truncated)\n" + logs[-3800:]
    await update.message.reply_text(f"```\n{logs}\n```", parse_mode="Markdown")


@admin_only
async def cmd_deactivate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Dung: `/deactivate <username_hoac_uid>`", parse_mode="Markdown")
        return
    target = ctx.args[0].strip()
    ok = db_deactivate(target)
    if ok:
        await update.message.reply_text(f"✅ Da huy Gold cho: `{target}`", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ Khong tim thay hoac loi khi huy `{target}`.", parse_mode="Markdown")


@admin_only
async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows = db_list_active()
    if not rows:
        await update.message.reply_text("Chua co UID nao duoc kich hoat.")
        return
    lines = [f"*Gold Active ({len(rows)} tai khoan):*\n"]
    for uid, username, expires, created in rows[:20]:
        name  = f"@{username}" if username else "N/A"
        start = created[:10] if created else "?"
        exp   = expires[:10] if expires else "Vinh vien"
        short_uid = uid[:18] + "..." if len(uid) > 18 else uid
        lines.append(f"• *{name}* (`{short_uid}`)\n  Tu: {start} ➔ Den: {exp}")
    if len(rows) > 20:
        lines.append(f"\n_...va {len(rows)-20} tai khoan khac_")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@admin_only
async def cmd_restart(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("Dang restart proxy service...")
    run_cmd(f"systemctl restart {SERVICE_NAME}")
    await asyncio.sleep(2)
    svc = get_service_status()
    e   = "✅" if svc == "active" else "❌"
    await msg.edit_text(f"{e} Service: `{svc}`", parse_mode="Markdown")


@admin_only
async def cmd_dns(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    profile_path = os.path.join(os.path.dirname(__file__), "locketgold.mobileconfig")
    if not os.path.exists(profile_path):
        await update.message.reply_text("❌ Chưa tìm thấy file `locketgold.mobileconfig`.")
        return

    guide = (
        "📥 *FILE CẤU HÌNH DNS LOCKET GOLD*\n\n"
        "👉 *Các bước cài đặt trên iPhone:*\n"
        "1. Bấm vào file đính kèm này để tải về\n"
        "2. Vào *Cài đặt (Settings)* ➔ Nhấn vào *Đã tải về hồ sơ* (Profile Downloaded)\n"
        "3. Bấm *Cài đặt (Install)* góc trên bên phải\n"
        "4. Vào *Cài đặt chung* ➔ *Quản lý VPN & Thiết bị* ➔ Kiểm tra đã chọn *LocketGold DNS*\n"
        "5. Tắt ứng dụng Locket rồi mở lại ➔ Đã có Gold! ✨"
    )
    with open(profile_path, "rb") as f_doc:
        await update.message.reply_document(
            document=f_doc,
            filename="LocketGold.mobileconfig",
            caption=guide,
            parse_mode="Markdown"
        )


# ===== Callbacks =====

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data  = query.data

    # Duration selection: dur|<days>|<uid>|<username>
    if data.startswith("dur|"):
        parts = data.split("|")
        days_str = parts[1]

        if days_str == "cancel":
            await query.message.edit_text("Da huy thao tac.")
            return

        uid      = parts[2]
        username = parts[3] if len(parts) > 3 else ""
        days     = int(days_str)
        label    = "Vinh Vien" if days >= 99999 else "1 Nam"

        msg = await query.message.edit_text(
            f"Dang kich hoat Gold ({label}) cho `{username or uid}`...",
            parse_mode="Markdown"
        )

        result = db_activate(uid, username, days)
        now_str = datetime.now().strftime("%d/%m/%Y %H:%M")

        if result["success"]:
            if days >= 99999:
                exp_display = "Vinh Vien (2099)"
            else:
                exp_dt = datetime.utcnow() + timedelta(days=days)
                exp_display = exp_dt.strftime("%d/%m/%Y")

            uname_txt = f"@{username}" if username else "N/A"
            text = (
                f"✅ *KICH HOAT GOLD THANH CONG!*\n\n"
                f"• Tai khoan: *{uname_txt}*\n"
                f"• UID: `{uid}`\n"
                f"• Goi nang cap: *{label}*\n"
                f"• Ngay bat dau: `{now_str}`\n"
                f"• Ngay ket thuc: `{exp_display}`\n"
                f"• Ma don: `{result['order_id']}`\n\n"
                f"_Khach chi can cai DNS Profile la Gold tu len ngay lap tuc._"
            )
            await msg.edit_text(text, parse_mode="Markdown")

            profile_path = os.path.join(os.path.dirname(__file__), "locketgold.mobileconfig")
            if os.path.exists(profile_path):
                guide = (
                    "📥 *FILE CẤU HÌNH DNS LOCKET GOLD*\n\n"
                    "👉 *Các bước cài đặt trên iPhone:*\n"
                    "1. Bấm vào file đính kèm này để tải về\n"
                    "2. Vào *Cài đặt (Settings)* ➔ Nhấn vào *Đã tải về hồ sơ* (Profile Downloaded)\n"
                    "3. Bấm *Cài đặt (Install)* góc trên bên phải\n"
                    "4. Vào *Cài đặt chung* ➔ *Quản lý VPN & Thiết bị* ➔ Kiểm tra đã chọn *LocketGold DNS*\n"
                    "5. Tắt ứng dụng Locket rồi mở lại ➔ Đã có Gold! ✨"
                )
                with open(profile_path, "rb") as f_doc:
                    await ctx.bot.send_document(
                        chat_id=query.message.chat_id,
                        document=f_doc,
                        filename="LocketGold.mobileconfig",
                        caption=guide,
                        parse_mode="Markdown"
                    )
            return
        else:
            text = f"❌ That bai: `{result.get('error')}`"

        await msg.edit_text(text, parse_mode="Markdown")
        return

    # Menu
    if data.startswith("menu|"):
        action = data.split("|")[1]
        if action == "status":
            svc = get_service_status()
            res = await test_inject()
            e_s = "✅" if svc == "active" else "❌"
            e_p = "✅" if (res.get("ok") and res.get("has_gold")) else "❌"
            text = f"*Trang thai:*\nService: {e_s} `{svc}`\nInject Gold: {e_p}"
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("Refresh", callback_data="menu|status"),
                InlineKeyboardButton("Restart", callback_data="menu|restart"),
            ]])
            await query.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)

        elif action == "list":
            rows = db_list_active()
            if not rows:
                await query.message.reply_text("Chua co UID nao duoc kich hoat.")
            else:
                lines = [f"*Gold Active ({len(rows)} UID):*\n"]
                for uid, username, expires, created in rows[:15]:
                    name  = f"@{username}" if username else "N/A"
                    start = created[:10] if created else "?"
                    exp   = expires[:10] if expires else "Vinh vien"
                    short = uid[:18] + "..." if len(uid) > 18 else uid
                    lines.append(f"• *{name}* (`{short}`)\n  {start} ➔ {exp}")
                await query.message.reply_text("\n".join(lines), parse_mode="Markdown")

        elif action == "test":
            res = await test_inject("MENU_TEST_UID")
            e   = "✅" if (res.get("ok") and res.get("has_gold")) else "❌"
            text = f"{e} Inject Gold: {'OK' if res.get('has_gold') else 'FAIL'}"
            if res.get("expires"):
                text += f"\nExpires: `{res['expires']}`"
            await query.message.reply_text(text, parse_mode="Markdown")

        elif action == "restart":
            run_cmd(f"systemctl restart {SERVICE_NAME}")
            await asyncio.sleep(2)
            svc = get_service_status()
            e   = "✅" if svc == "active" else "❌"
            await query.message.reply_text(f"{e} Restarted. Status: `{svc}`", parse_mode="Markdown")

        elif action == "dns":
            profile_path = os.path.join(os.path.dirname(__file__), "locketgold.mobileconfig")
            if not os.path.exists(profile_path):
                await query.message.reply_text("❌ Chưa tìm thấy file `locketgold.mobileconfig`.")
                return

            guide = (
                "📥 *FILE CẤU HÌNH DNS LOCKET GOLD*\n\n"
                "👉 *Các bước cài đặt trên iPhone:*\n"
                "1. Bấm vào file đính kèm này để tải về\n"
                "2. Vào *Cài đặt (Settings)* ➔ Nhấn vào *Đã tải về hồ sơ* (Profile Downloaded)\n"
                "3. Bấm *Cài đặt (Install)* góc trên bên phải\n"
                "4. Vào *Cài đặt chung* ➔ *Quản lý VPN & Thiết bị* ➔ Kiểm tra đã chọn *LocketGold DNS*\n"
                "5. Tắt ứng dụng Locket rồi mở lại ➔ Đã có Gold! ✨"
            )
            with open(profile_path, "rb") as f_doc:
                await ctx.bot.send_document(
                    chat_id=query.message.chat_id,
                    document=f_doc,
                    filename="LocketGold.mobileconfig",
                    caption=guide,
                    parse_mode="Markdown"
                )


# ===== Main =====

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("status",     cmd_status))
    app.add_handler(CommandHandler("test",       cmd_test))
    app.add_handler(CommandHandler("logs",       cmd_logs))
    app.add_handler(CommandHandler("up",         cmd_activate))
    app.add_handler(CommandHandler("activate",   cmd_activate))
    app.add_handler(CommandHandler("deactivate", cmd_deactivate))
    app.add_handler(CommandHandler("list",       cmd_list))
    app.add_handler(CommandHandler("restart",    cmd_restart))
    app.add_handler(CommandHandler("dns",        cmd_dns))
    app.add_handler(CallbackQueryHandler(on_callback))
    # Ho tro nhan tin nhan username truc tiep
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_direct_text))

    print(f"[*] Bot started. Admin: {ADMIN_IDS}")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
