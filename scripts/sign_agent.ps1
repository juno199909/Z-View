# Agent 发布包签名脚本（P0-05 签名校验链路的配套步骤）
# 用法: powershell -File scripts\sign_agent.ps1 -Path <exe 路径>（可多个）
# 证书: 默认自动选择仍有效且带私钥的最新 CN=Z-View 证书；可用
# ZVIEW_CODESIGN_THUMBPRINT 固定指定发布证书。
# 注意: 构建后未签名会被 Agent 的 Authenticode 护栏以 NotSigned 拒绝（1.8.3 发布时踩过）
param([Parameter(Mandatory = $true)][string[]]$Path)

$configuredThumbprint = [Environment]::GetEnvironmentVariable("ZVIEW_CODESIGN_THUMBPRINT")
if (-not [string]::IsNullOrWhiteSpace($configuredThumbprint)) {
    $cert = @(
        Get-ChildItem "Cert:\CurrentUser\My\$configuredThumbprint" -ErrorAction SilentlyContinue
        Get-ChildItem "Cert:\LocalMachine\My\$configuredThumbprint" -ErrorAction SilentlyContinue
    ) | Where-Object { $_.HasPrivateKey -and $_.NotAfter -gt (Get-Date) } | Select-Object -First 1
} else {
    $cert = @(
        Get-ChildItem Cert:\CurrentUser\My -ErrorAction SilentlyContinue
        Get-ChildItem Cert:\LocalMachine\My -ErrorAction SilentlyContinue
    ) |
        Where-Object {
            $_.Subject -match 'Z-View' -and
            $_.HasPrivateKey -and
            $_.NotAfter -gt (Get-Date)
        } |
        Sort-Object NotAfter -Descending |
        Select-Object -First 1
}
if (-not $cert) {
    Write-Error "Z-View signing certificate not found or expired"
    exit 1
}
foreach ($file in $Path) {
    $result = Set-AuthenticodeSignature -FilePath $file -Certificate $cert -HashAlgorithm SHA256
    Write-Host ("{0} -> {1} ({2})" -f $file, $result.Status, $cert.Thumbprint)
    if ($result.Status -ne 'Valid') { exit 1 }
}
