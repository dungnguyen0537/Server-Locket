"""
generate_profile.py — Tạo file .mobileconfig cho khách hàng iOS cài đặt

File này sẽ:
  1. Cài Root CA của chúng ta (để iOS tin cert fake của VPS)
  2. Cài DNS profile trỏ api.revenuecat.com về VPS (DoH hoặc classic DNS)

Cách dùng:
  python generate_profile.py --vps-ip 160.191.245.200 --out locketgold.mobileconfig

Gửi file .mobileconfig cho khách:
  - iOS nhận file -> Settings > Profile Downloaded -> Install
  - Sau khi cài, toàn bộ DNS được xử lý qua VPS, RevenueCat bị MITM
"""

import os
import sys
import base64
import uuid
import argparse
import datetime


CERTS_DIR = os.path.join(os.path.dirname(__file__), "certs")
ROOT_CA_PATH = os.path.join(CERTS_DIR, "root_ca.crt")


def load_ca_b64() -> str:
    with open(ROOT_CA_PATH, "rb") as f:
        raw = f.read()
    # Bỏ header/footer PEM, chỉ lấy base64 content
    lines = raw.decode().strip().splitlines()
    b64 = "".join(l for l in lines if not l.startswith("-----"))
    return b64


def generate_uuid() -> str:
    return str(uuid.uuid4()).upper()


def build_mobileconfig(
    vps_ip: str = "160.22.107.114",
    org_name: str = "LocketGold",
    nextdns_url: str = "https://dns.nextdns.io/8f4cb9",
) -> str:
    ca_b64 = load_ca_b64()

    # UUIDs cho từng payload
    profile_uuid = generate_uuid()
    ca_uuid      = generate_uuid()
    dns_uuid     = generate_uuid()

    now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    # DNS Settings payload with SupplementalMatchDomains
    doh_url = nextdns_url or f"https://dns.nextdns.io/8f4cb9"
    dns_payload = f"""
    <dict>
        <key>PayloadType</key>
        <string>com.apple.dnsSettings.managed</string>
        <key>PayloadVersion</key>
        <integer>1</integer>
        <key>PayloadIdentifier</key>
        <string>com.locketgold.dns.{dns_uuid}</string>
        <key>PayloadUUID</key>
        <string>{dns_uuid}</string>
        <key>PayloadDisplayName</key>
        <string>Locket Gold DNS</string>
        <key>PayloadDescription</key>
        <string>Kich hoat Locket Gold, video 15s va dang rieng tu</string>
        <key>DNSSettings</key>
        <dict>
            <key>DNSProtocol</key>
            <string>HTTPS</string>
            <key>ServerURL</key>
            <string>{doh_url}</string>
            <key>SupplementalMatchDomains</key>
            <array>
                <string>api.revenuecat.com</string>
                <string>*.revenuecat.com</string>
                <string>firebaseremoteconfig.googleapis.com</string>
                <string>*.firebaseremoteconfig.googleapis.com</string>
                <string>firebaseremoteconfigrealtime.googleapis.com</string>
                <string>*.firebaseremoteconfigrealtime.googleapis.com</string>
                <string>firebaselogging.googleapis.com</string>
                <string>*.firebaselogging.googleapis.com</string>
            </array>
        </dict>
        <key>ProhibitDisablement</key>
        <false/>
    </dict>"""

    mobileconfig = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>PayloadContent</key>
    <array>
        <!-- Payload 1: Trust Root CA Certificate -->
        <dict>
            <key>PayloadType</key>
            <string>com.apple.security.root</string>
            <key>PayloadVersion</key>
            <integer>1</integer>
            <key>PayloadIdentifier</key>
            <string>com.locketgold.ca.{ca_uuid}</string>
            <key>PayloadUUID</key>
            <string>{ca_uuid}</string>
            <key>PayloadDisplayName</key>
            <string>LocketGold Certificate Authority</string>
            <key>PayloadDescription</key>
            <string>Kich hoat Locket Gold va cac tinh nang mo rong</string>
            <key>PayloadCertificateFileName</key>
            <string>LocketGold_CA.crt</string>
            <key>PayloadContent</key>
            <data>
{ca_b64}
            </data>
        </dict>
        <!-- Payload 2: DNS Settings -->
        {dns_payload}
    </array>
    <key>PayloadDisplayName</key>
    <string>Locket Gold</string>
    <key>PayloadDescription</key>
    <string>Kich hoat locket gold vui long khong xoa. Khi cai dat xong vao Gioi thieu keo xuong cuoi va chon cai dat tin cay chung nhan roi gat nut cong tac cua Locket Gold len.</string>
    <key>PayloadIdentifier</key>
    <string>com.locketgold.profile.{profile_uuid}</string>
    <key>PayloadOrganization</key>
    <string>{org_name}</string>
    <key>PayloadRemovalDisallowed</key>
    <false/>
    <key>PayloadType</key>
    <string>Configuration</string>
    <key>PayloadUUID</key>
    <string>{profile_uuid}</string>
    <key>PayloadVersion</key>
    <integer>1</integer>
</dict>
</plist>"""

    return mobileconfig


def main():
    parser = argparse.ArgumentParser(description="Generate iOS .mobileconfig for LocketGold DNS proxy")
    parser.add_argument("--vps-ip", default="160.22.107.114", help="VPS public IP address")
    parser.add_argument("--nextdns", default="https://dns.nextdns.io/8f4cb9", help="NextDNS DoH URL")
    parser.add_argument("--org", default="LocketGold", help="Organization name in profile")
    parser.add_argument("--out", default="locketgold.mobileconfig", help="Output filename")
    args = parser.parse_args()

    if not os.path.exists(ROOT_CA_PATH):
        print(f"[ERROR] Root CA not found: {ROOT_CA_PATH}")
        print("        Run generate_ca.py first!")
        sys.exit(1)

    print(f"[*] Generating .mobileconfig with NextDNS DoH: {args.nextdns}")
    config = build_mobileconfig(args.vps_ip, args.org, args.nextdns)

    out_path = os.path.join(os.path.dirname(__file__), args.out)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(config)

    print(f"[+] Profile saved: {out_path}")
    print()
    print("Cach phan phoi cho khach hang:")
    print(f"  1. Upload file len server: https://locketgold.shop/profile/locketgold.mobileconfig")
    print(f"  2. Khach mo link tren Safari iOS -> Settings > Profile Downloaded -> Install")
    print(f"  3. Sau khi cai: Settings > General > VPN & Device Management > Profiles")
    print(f"     -> Verify la 'LocketGold Profile' da duoc Trust")
    print()
    print("Luu y quan trong:")
    print("  - Khach PHAI mo link tren SAFARI (Chrome/Firefox khong ho tro cai profile)")
    print("  - iOS se canh bao 'Unverified Profile' -> binh thuong, bam Install")
    print("  - Server vps-ip PHAI chay proxy_server.py va mo port 443 + 53")


if __name__ == "__main__":
    main()
