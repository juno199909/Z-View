# 将 Z-View 代码签名证书写入本机受信任存储（需管理员权限）。
# 用途：配合 GPO 启动脚本在全部终端执行，使 Authenticode 签名可信，
# 企业杀软（含 360 天擎）对签名后的 Z-View.exe 不再按未知程序查杀。
# 用法：powershell -ExecutionPolicy Bypass -File install_trusted_cert.ps1
$ErrorActionPreference = "Stop"

$CertPath = Join-Path $PSScriptRoot "zview-codesign.cer"
if (-not (Test-Path $CertPath)) {
    throw "Certificate not found: $CertPath"
}

certutil -addstore -f Root $CertPath
certutil -addstore -f TrustedPublisher $CertPath

Write-Host "Z-View code signing certificate installed to Root + TrustedPublisher."
