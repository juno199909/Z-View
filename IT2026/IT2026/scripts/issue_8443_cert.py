# -*- coding: utf-8 -*-
"""#2/#17 联合收尾：8443 证书改由 WT 根 CA 签发 + Agent bundle 切换为根证书。

效果：
- Agent 的 runtime/ca-bundle.pem = WT 根证书（10 年稳定），今后 8443 证书轮换对 Agent 透明
- 浏览器观看端：新根已在 LocalMachine\Root（轮换时导入），8443 链路受信
- vite(5173/4173) 证书同步替换为同款 WT 根签发证书
"""
import datetime
import ipaddress
import shutil

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CERT_DIR = "wt_certs"
ROOT_CERT = CERT_DIR + "/zview-root.cer"
ROOT_KEY = CERT_DIR + "/zview-root-key.pem"
FRONT_CERT = "frontend/certs/zview-cert.pem"
FRONT_KEY = "frontend/certs/zview-key.pem"

SAN_DNS = ["localhost", "XXH-XXX"]
SAN_IPS = ["172.16.250.120", "127.0.0.1"]
SUBJECT_CN = "Z-View Platform"
DAYS = 1825

# 1) 加载 WT 根 CA
with open(ROOT_CERT, "rb") as f:
    root_cert = x509.load_pem_x509_certificate(f.read())
with open(ROOT_KEY, "rb") as f:
    root_key = serialization.load_pem_private_key(f.read(), password=None)
print("根 CA:", root_cert.subject.rfc4514_string(), flush=True)

# 2) 签发 8443 服务器证书
server_key = ec.generate_private_key(ec.SECP256R1())
server_name = x509.Name([
    x509.NameAttribute(NameOID.COMMON_NAME, SUBJECT_CN),
])
san = x509.SubjectAlternativeName(
    [x509.DNSName(d) for d in SAN_DNS] + [x509.IPAddress(ipaddress.ip_address(i)) for i in SAN_IPS]
)
now = datetime.datetime.now(datetime.timezone.utc)
server_cert = (
    x509.CertificateBuilder()
    .subject_name(server_name)
    .issuer_name(root_cert.subject)
    .public_key(server_key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(now - datetime.timedelta(days=2))
    .not_valid_after(now + datetime.timedelta(days=DAYS))
    .add_extension(san, critical=False)
    .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    .add_extension(x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()), critical=False)
    .add_extension(x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(
        root_cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value), critical=False)
    .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
    .add_extension(x509.KeyUsage(
        digital_signature=True, key_encipherment=True,
        content_commitment=False, data_encipherment=False,
        key_agreement=False, key_cert_sign=False, crl_sign=False,
        encipher_only=False, decipher_only=False,
    ), critical=False)
    .sign(root_key, hashes.SHA256())
)

# 3) 归档旧证书 + 写入新证书（覆盖 frontend/certs，assets_api 与 vite 共用）
shutil.copy2(FRONT_CERT, FRONT_CERT + ".pre-wtroot.bak")
shutil.copy2(FRONT_KEY, FRONT_KEY + ".pre-wtroot.bak")
with open(FRONT_CERT, "wb") as f:
    f.write(server_cert.public_bytes(serialization.Encoding.PEM))
with open(FRONT_KEY, "wb") as f:
    f.write(server_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
print("8443 证书已由 WT 根签发并写入 frontend/certs（旧证书已归档 .pre-wtroot.bak）", flush=True)

# 4) Agent runtime bundle ← WT 根证书
shutil.copy2(ROOT_CERT, r"C:\ProgramData\CMDB-Agent\runtime\ca-bundle.pem")
print("runtime ca-bundle.pem ← WT 根证书", flush=True)

# 5) 自验链
leaf_check = x509.load_pem_x509_certificate(open(FRONT_CERT, "rb").read())
print("新证书 issuer == 根 subject:", leaf_check.issuer == root_cert.subject, flush=True)
print("新证书 SAN:", [str(s) for s in leaf_check.extensions.get_extension_for_class(x509.SubjectAlternativeName).value], flush=True)
print("有效期至:", leaf_check.not_valid_after_utc.isoformat(), flush=True)
