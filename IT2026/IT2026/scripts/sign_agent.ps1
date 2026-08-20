# Agent 发布包签名脚本（P0-05 签名校验链路的配套步骤）
# 用法: powershell -File scripts\sign_agent.ps1 -Path <exe 路径>（可多个）
# 证书: CurrentUser\My 中 CN=Z-View Enterprise（Thumbprint 93C05132...）
# 注意: 构建后未签名会被 Agent 的 Authenticode 护栏以 NotSigned 拒绝（1.8.3 发布时踩过）
param([Parameter(Mandatory = $true)][string[]]$Path)

$cert = Get-ChildItem Cert:\CurrentUser\My | Where-Object { $_.Subject -match 'Z-View' } | Sort-Object NotAfter -Descending | Select-Object -First 1
if (-not $cert) {
    Write-Error "Z-View signing certificate not found in CurrentUser\My"
    exit 1
}
foreach ($file in $Path) {
    $result = Set-AuthenticodeSignature -FilePath $file -Certificate $cert
    Write-Host ("{0} -> {1}" -f $file, $result.Status)
    if ($result.Status -ne 'Valid') { exit 1 }
}
