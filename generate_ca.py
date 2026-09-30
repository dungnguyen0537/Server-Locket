"""
generate_ca.py — Tạo Root CA + Server Certificate cho api.revenuecat.com
Chạy 1 lần trên VPS để tạo cert dùng cho MITM proxy.

Yêu cầu: pip install cryptography
Output:
  certs/root_ca.crt   — Root CA cert (gửi cho khách cài .mobileconfig)
  certs/root_ca.key   — Root CA private key (giữ bí mật trên VPS)
  certs/server.crt    — Server cert cho api.revenuecat.com
  certs/server.key    — Server private key
"""

import os
import datetime
from cryptography import x509
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend

CERTS_DIR = os.path.join(os.path.dirname(__file__), "certs")
os.makedirs(CERTS_DIR, exist_ok=True)


def generate_rsa_key(bits=4096):
    return rsa.generate_private_key(
        public_exponent=65537,
        key_size=bits,
        backend=default_backend()
    )


def save_key(key, path):
    with open(path, "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        ))
    print(f"[+] Key saved: {path}")


def save_cert(cert, path):
    with open(path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    print(f"[+] Cert saved: {path}")


def build_root_ca():
    ca_key = generate_rsa_key()
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "LocketGold CA"),
        x509.NameAttribute(NameOID.COMMON_NAME, "LocketGold Root CA"),
    ])

    now = datetime.datetime.utcnow()
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))  # 10 years
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=False,
                key_encipherment=False, data_encipherment=False,
                key_agreement=False, key_cert_sign=True,
                crl_sign=True, encipher_only=False, decipher_only=False
            ), critical=True
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
            critical=False
        )
        .sign(ca_key, hashes.SHA256(), default_backend())
    )

    save_key(ca_key, os.path.join(CERTS_DIR, "root_ca.key"))
    save_cert(ca_cert, os.path.join(CERTS_DIR, "root_ca.crt"))
    return ca_key, ca_cert


def load_or_build_root_ca():
    ca_key_path = os.path.join(CERTS_DIR, "root_ca.key")
    ca_cert_path = os.path.join(CERTS_DIR, "root_ca.crt")
    if os.path.exists(ca_key_path) and os.path.exists(ca_cert_path):
        print("[*] Reusing existing Root CA from certs/...")
        with open(ca_key_path, "rb") as f:
            ca_key = serialization.load_pem_private_key(f.read(), password=None, backend=default_backend())
        with open(ca_cert_path, "rb") as f:
            ca_cert = x509.load_pem_x509_certificate(f.read(), backend=default_backend())
        return ca_key, ca_cert
    print("[*] Generating new Root CA...")
    return build_root_ca()


def build_server_cert(ca_key, ca_cert):
    server_key = generate_rsa_key(2048)
    subject = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "LocketGold Proxy"),
        x509.NameAttribute(NameOID.COMMON_NAME, "api.revenuecat.com"),
    ])

    now = datetime.datetime.utcnow()
    import ipaddress
    # SANs cover RevenueCat + Firebase Remote Config + Locket Camera + DoH domains
    san = x509.SubjectAlternativeName([
        x509.DNSName("api.revenuecat.com"),
        x509.DNSName("*.revenuecat.com"),
        x509.DNSName("firebaseremoteconfig.googleapis.com"),
        x509.DNSName("*.firebaseremoteconfig.googleapis.com"),
        x509.DNSName("firebaseremoteconfigrealtime.googleapis.com"),
        x509.DNSName("*.firebaseremoteconfigrealtime.googleapis.com"),
        x509.DNSName("firebaselogging.googleapis.com"),
        x509.DNSName("*.firebaselogging.googleapis.com"),
        x509.DNSName("api.locketcamera.com"),
        x509.DNSName("*.locketcamera.com"),
        x509.DNSName("dns.locketgold.shop"),
        x509.DNSName("*.locketgold.shop"),
        x509.DNSName("locketgold.shop"),
        x509.IPAddress(ipaddress.IPv4Address("54.179.86.163")),
    ])

    server_cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=825))  # iOS max TLS cert validity
        .add_extension(san, critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=False,
                key_encipherment=True, data_encipherment=False,
                key_agreement=False, key_cert_sign=False,
                crl_sign=False, encipher_only=False, decipher_only=False
            ), critical=True
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False
        )
        .sign(ca_key, hashes.SHA256(), default_backend())
    )

    save_key(server_key, os.path.join(CERTS_DIR, "server.key"))
    save_cert(server_cert, os.path.join(CERTS_DIR, "server.crt"))
    return server_key, server_cert


def main():
    ca_key, ca_cert = load_or_build_root_ca()

    print("[*] Generating Server Certificate with RevenueCat + Firebase SANs...")
    build_server_cert(ca_key, ca_cert)

    print("\n[DONE] Certs generated in ./certs/")
    print("  root_ca.crt  -> embed into .mobileconfig (send to customers)")
    print("  root_ca.key  -> keep secret on VPS")
    print("  server.crt   -> used by proxy_server.py")
    print("  server.key   -> used by proxy_server.py")


if __name__ == "__main__":
    main()
