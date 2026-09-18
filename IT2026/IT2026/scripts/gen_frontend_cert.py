#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate self-signed TLS cert for frontend dev server (vite https).

Produces frontend/certs/zview-cert.pem and zview-key.pem.
Run once after a fresh clone, or whenever the cert expires.

Usage:
    python scripts/gen_frontend_cert.py
"""
from __future__ import annotations

import datetime
import os
import socket
import sys

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _local_addresses() -> list[str]:
    ips = {"127.0.0.1", "::1"}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        ips.add(socket.gethostname())
    except Exception:
        pass
    return sorted(ips)


def generate(cert_dir: str, days: int = 365) -> tuple[str, str]:
    os.makedirs(cert_dir, exist_ok=True)
    cert_path = os.path.join(cert_dir, "zview-cert.pem")
    key_path = os.path.join(cert_dir, "zview-key.pem")

    key = ec.generate_private_key(ec.SECP256R1())
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "Z-View Dev"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Z-View"),
    ])

    san_list: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
    ]
    for addr in _local_addresses():
        try:
            import ipaddress
            ipaddress.ip_address(addr)
            san_list.append(x509.IPAddress(ipaddress.ip_address(addr)))
        except ValueError:
            san_list.append(x509.DNSName(addr))

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=days))
        .add_extension(
            x509.SubjectAlternativeName(san_list),
            critical=False,
        )
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=True,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([
                x509.oid.ExtendedKeyUsageOID.SERVER_AUTH,
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))

    os.chmod(key_path, 0o600)
    return cert_path, key_path


if __name__ == "__main__":
    cert_dir = os.path.join(_project_root(), "frontend", "certs")
    cert_path, key_path = generate(cert_dir)
    print(f"Cert: {cert_path}")
    print(f"Key:  {key_path}")
    print("Done. Private key is gitignored — never commit it.")
    sys.exit(0)
