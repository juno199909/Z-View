# -*- coding: utf-8 -*-
"""WebTransport QUIC 证书管理。

Chrome 的 serverCertificateHashes 机制要求：
- ECDSA P-256 证书（SHA-256 签名）
- 有效期 ≤ 14 天
- 非 CA

因此 QUIC 证书独立于前端 TLS 证书，按需生成并在到期前自动续期。
"""
from __future__ import annotations

import datetime
import ipaddress
import os
import socket
import threading

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

CERT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wt_certs")
CERT_FILE = os.path.join(CERT_DIR, "wt-cert.pem")
KEY_FILE = os.path.join(CERT_DIR, "wt-key.pem")
CERT_LIFETIME = datetime.timedelta(days=11)  # Chrome hash pinning 要求 ≤14 天；留足时钟偏差余量
RENEW_BEFORE = datetime.timedelta(days=2)

_lock = threading.Lock()


def _local_addresses() -> list[str]:
    """收集本机所有 IPv4 地址 + 主机名，写入 SAN。"""
    ips = {"127.0.0.1"}
    try:
        host = socket.gethostname()
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            ips.add(info[4][0])
    except Exception:
        pass
    try:
        # UDP socket connect 技巧获取默认路由出口 IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    return sorted(ips)


def _generate() -> None:
    os.makedirs(CERT_DIR, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    hostname = socket.gethostname() or "zview-host"
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "Z-View WebTransport"),
    ])
    san = x509.SubjectAlternativeName(
        [x509.DNSName("localhost")] + [x509.IPAddress(ipaddress.ip_address(ip)) for ip in _local_addresses()]
        + [x509.DNSName(hostname)]
    )
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=2))
        # Chrome serverCertificateHashes 要求总有效期 ≤ 14 天（336 小时），
        # 故止点为 13 天 23 小时（起点回拨 1 小时后总跨度不超限）
        .not_valid_after(now + CERT_LIFETIME - datetime.timedelta(hours=1))
        .add_extension(san, critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        # Chrome serverCertificateHashes 硬性要求：EKU 必须含 serverAuth（缺失即 CERTIFICATE_VERIFY_FAILED）
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_encipherment=True, content_commitment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=False,
                crl_sign=False, encipher_only=False, decipher_only=False,
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    tmp_cert, tmp_key = CERT_FILE + ".tmp", KEY_FILE + ".tmp"
    with open(tmp_cert, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(tmp_key, "wb") as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
    os.replace(tmp_cert, CERT_FILE)
    os.replace(tmp_key, KEY_FILE)


def _load_cert() -> x509.Certificate:
    with open(CERT_FILE, "rb") as f:
        return x509.load_pem_x509_certificate(f.read())


def ensure_wt_cert() -> tuple[str, str]:
    """确保存在有效的 QUIC 证书（到期前 2 天自动续期）。返回 (cert_path, key_path)。"""
    with _lock:
        renew = False
        if not (os.path.exists(CERT_FILE) and os.path.exists(KEY_FILE)):
            renew = True
        else:
            try:
                cert = _load_cert()
                remaining = cert.not_valid_after_utc - datetime.datetime.now(datetime.timezone.utc)
                if remaining < RENEW_BEFORE:
                    renew = True
            except Exception:
                renew = True
        if renew:
            _generate()
    return CERT_FILE, KEY_FILE


def get_cert_der() -> bytes:
    """当前证书的 DER 编码（供 hash pinning 与 create_session 响应）。"""
    ensure_wt_cert()
    with open(CERT_FILE, "rb") as f:
        cert = x509.load_pem_x509_certificate(f.read())
    return cert.public_bytes(serialization.Encoding.DER)


def get_cert_hash_hex() -> str:
    """证书 DER 的 SHA-256 十六进制（观看端 serverCertificateHashes 用）。"""
    import hashlib

    return hashlib.sha256(get_cert_der()).hexdigest()


if __name__ == "__main__":
    c, k = ensure_wt_cert()
    print("cert:", c)
    print("key:", k)
    print("hash:", get_cert_hash_hex())
