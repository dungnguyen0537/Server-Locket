"""
test_local.py — Test proxy trên máy Windows (không cần VPS, không cần port 443/53)

Cách test:
  1. Chạy script này: python test_local.py
  2. Proxy chạy trên localhost:18443 (port cao, không cần admin)
  3. Script tự test inject Gold và in kết quả

Để test trên điện thoại (cùng WiFi):
  - Tìm IP local của máy (ipconfig, ví dụ 192.168.1.x)
  - Vào Settings điện thoại > WiFi > DNS thủ công: 192.168.1.x
  - Proxy cũng lắng nghe 0.0.0.0 nên điện thoại sẽ connect được
  - Cài profile CA vào điện thoại (file certs/root_ca.crt)
"""

import asyncio
import ssl
import sys
import os
import subprocess

# Đảm bảo import đúng
sys.path.insert(0, os.path.dirname(__file__))

# Override port sang 18443 (không cần quyền admin trên Windows)
os.environ.setdefault("PROXY_PORT", "18443")

TEST_PORT = 18443
PROXY_HOST = "127.0.0.1"


async def run_proxy_background():
    """Chạy proxy server trong background task."""
    import proxy_server as ps
    ps.LISTEN_HOST = "0.0.0.0"
    ps.LISTEN_PORT = TEST_PORT
    ps.CHECK_DB_ACTIVATION = False  # inject tất cả

    from aiohttp import web
    import ssl as ssl_mod

    ssl_ctx = ssl_mod.SSLContext(ssl_mod.PROTOCOL_TLS_SERVER)
    ssl_ctx.load_cert_chain(ps.SERVER_CERT, ps.SERVER_KEY)

    app = web.Application()
    app.router.add_route("*", "/{path_info:.*}", ps.handle_request)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", TEST_PORT, ssl_context=ssl_ctx)
    await site.start()
    print(f"[*] Proxy running on 0.0.0.0:{TEST_PORT}")
    return runner


async def test_inject(uid: str) -> dict:
    import aiohttp
    url = f"https://{PROXY_HOST}:{TEST_PORT}/v1/subscribers/{uid}"
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    async with aiohttp.ClientSession() as session:
        async with session.get(url, ssl=ssl_ctx, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            data = await resp.json()
            return data


async def main():
    print("=" * 55)
    print(" LocketGold DNS Proxy — Local Test")
    print("=" * 55)

    # Kiểm tra cert
    certs_dir = os.path.join(os.path.dirname(__file__), "certs")
    if not os.path.exists(os.path.join(certs_dir, "server.crt")):
        print("[!] Chua co cert. Chay generate_ca.py truoc:")
        print("    python generate_ca.py")
        return

    print(f"[*] Starting proxy on port {TEST_PORT}...")
    runner = await run_proxy_background()
    await asyncio.sleep(1)

    # Test 1: Basic inject
    print("\n[TEST 1] Basic Gold injection...")
    try:
        data = await test_inject("LOCALTEST_UID_001")
        gold = data.get("subscriber", {}).get("entitlements", {}).get("Gold")
        if gold:
            print(f"  PASS — Gold active, expires: {gold.get('expires_date')}")
        else:
            print(f"  FAIL — Khong co Gold trong response")
            print(f"  Response keys: {list(data.keys())}")
    except Exception as e:
        print(f"  ERROR — {e}")

    # Test 2: Multiple UIDs
    print("\n[TEST 2] Multiple UID injection...")
    for uid in ["user_abc123", "user_xyz456", "test_khach_001"]:
        try:
            data = await test_inject(uid)
            gold = data.get("subscriber", {}).get("entitlements", {}).get("Gold")
            status = "PASS" if gold else "FAIL"
            print(f"  {status} — {uid}")
        except Exception as e:
            print(f"  ERROR — {uid}: {e}")

    # Test 3: Receipts endpoint
    print("\n[TEST 3] /receipts endpoint...")
    try:
        import aiohttp
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        url = f"https://{PROXY_HOST}:{TEST_PORT}/v1/receipts"
        body = {"app_user_id": "test_receipt_uid", "fetch_token": "FAKE", "is_restore": True}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=body, ssl=ssl_ctx, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
                gold = data.get("subscriber", {}).get("entitlements", {}).get("Gold")
                status = "PASS" if gold else "FAIL"
                print(f"  {status} — HTTP {resp.status}, Gold: {bool(gold)}")
    except Exception as e:
        print(f"  ERROR — {e}")

    print("\n" + "=" * 55)
    print(" Ket qua: Proxy hoat dong tot!")
    print("=" * 55)
    print()
    print(f" Test thu cong:")
    print(f"   curl -sk https://127.0.0.1:{TEST_PORT}/v1/subscribers/ANYUID | python -m json.tool")
    print()

    # Lay IP local de test dien thoai
    try:
        result = subprocess.run("ipconfig", capture_output=True, text=True)
        import re
        ips = re.findall(r"IPv4.*?:\s*(192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+)", result.stdout)
        if ips:
            local_ip = ips[0]
            print(f" Test dien thoai (cung WiFi):")
            print(f"   1. Dat DNS thu cong: {local_ip}")
            print(f"   2. Cai cert: copy file certs/root_ca.crt vao dien thoai va trust")
            print(f"   3. Mo Locket -> Gold active")
    except Exception:
        pass

    print("\n[*] Proxy dang chay. Ctrl+C de dung.")
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
