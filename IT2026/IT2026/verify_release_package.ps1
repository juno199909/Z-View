[CmdletBinding()]
param(
    [string]$BuildExePath = "",
    [string]$PackageDir = "",
    [switch]$AllowTemplateConfig,
    [switch]$RequireVirtualDisplayDriver
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($PackageDir)) {
    $PackageDir = Get-ChildItem -LiteralPath $ProjectRoot -Directory |
        Where-Object { $_.Name -like "GPO*" } |
        Select-Object -ExpandProperty FullName -First 1
} else {
    $PackageDir = [System.IO.Path]::GetFullPath($PackageDir)
}
$Errors = New-Object System.Collections.Generic.List[string]
$Warnings = New-Object System.Collections.Generic.List[string]

function Add-VerificationError {
    param([string]$Message)

    $Errors.Add($Message)
    Write-Host ("[FAIL] {0}" -f $Message) -ForegroundColor Red
}

function Add-VerificationWarning {
    param([string]$Message)

    $Warnings.Add($Message)
    Write-Host ("[WARN] {0}" -f $Message) -ForegroundColor Yellow
}

function Test-RequiredFile {
    param(
        [string]$Path,
        [string]$Description
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        Add-VerificationError ("缺少{0}: {1}" -f $Description, $Path)
        return $false
    }

    $FileInfo = Get-Item -LiteralPath $Path
    if ($FileInfo.Length -le 0) {
        Add-VerificationError ("{0}为空文件: {1}" -f $Description, $Path)
        return $false
    }

    Write-Host ("[PASS] {0}: {1} ({2:N0} 字节)" -f $Description, $Path, $FileInfo.Length)
    return $true
}

Write-Host ("==> 开始验证 Z-View 发布包: {0}" -f $ProjectRoot)
if (-not $PackageDir) {
    throw "未找到 GPO 部署包目录。"
}

# 中文注释：先检查构建入口和运行入口，避免发布包完整但源码恢复不完整。
$ProjectFiles = [ordered]@{
    "Agent 主入口" = "cmdb_agent_unified_v2.py"
    "桌面上下文模块" = "desktop_context.py"
    "PyInstaller 配置" = "build_agent.spec"
    "Agent 配置" = "config.json"
}
foreach ($Entry in $ProjectFiles.GetEnumerator()) {
    Test-RequiredFile -Path (Join-Path $ProjectRoot $Entry.Value) -Description $Entry.Key | Out-Null
}

$PackageFiles = [ordered]@{
    "Agent 可执行文件" = "Z-View.exe"
    "部署配置" = "config.json"
    "交互安装脚本" = "install.bat"
    "GPO 部署脚本" = "deploy.bat"
    "卸载脚本" = "uninstall.bat"
    "诊断入口" = "diagnostic.bat"
    "诊断脚本" = "diagnostic.ps1"
    "服务管道等待脚本" = "wait_privileged_service_pipe.ps1"
    "远控连续性检查脚本" = "check_remote_desktop_continuity.ps1"
    "远控帧活性检查脚本" = "verify_remote_desktop_frame_liveness.ps1"
    "虚拟显示载荷同步脚本" = "sync_virtual_display_payload.ps1"
}
foreach ($Entry in $PackageFiles.GetEnumerator()) {
    Test-RequiredFile -Path (Join-Path $PackageDir $Entry.Value) -Description $Entry.Key | Out-Null
}

# 中文注释：配置只校验必需结构，不输出 token，避免验收日志泄露凭据。
$ConfigPath = Join-Path $PackageDir "config.json"
if (Test-Path -LiteralPath $ConfigPath -PathType Leaf) {
    try {
        $Config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $ConfigProperties = @($Config.PSObject.Properties.Name)
        if ($ConfigProperties -notcontains "server_url" -or [string]::IsNullOrWhiteSpace([string]$Config.server_url)) {
            Add-VerificationError "config.json 缺少有效的 server_url。"
        }
        if ($ConfigProperties -notcontains "token" -or [string]::IsNullOrWhiteSpace([string]$Config.token)) {
            Add-VerificationError "config.json 缺少有效的 token。"
        } elseif (
            [string]$Config.token -match "replace-with|change-me|example" -and
            -not $AllowTemplateConfig
        ) {
            Add-VerificationError "config.json 使用了模板 token，不能作为可部署发布包。"
        }
        Write-Host "[PASS] config.json JSON 结构可解析。"
        if ($ConfigProperties -notcontains "software_management") {
            Write-Host "[PASS] 软件服务地址将由 Agent 根据 server_url 自动派生。"
        }
    } catch {
        Add-VerificationError ("config.json 无法解析: {0}" -f $_.Exception.Message)
    }
}

$PackageExePath = Join-Path $PackageDir "Z-View.exe"
if (Test-Path -LiteralPath $PackageExePath -PathType Leaf) {
    $ExeInfo = Get-Item -LiteralPath $PackageExePath
    if ($ExeInfo.Length -lt 1MB) {
        Add-VerificationError ("Z-View.exe 体积异常: {0:N0} 字节。" -f $ExeInfo.Length)
    }

    # 中文注释：只读取 PE 文件头的两个字节，避免把整个 EXE 载入内存。
    $Stream = [System.IO.File]::OpenRead($PackageExePath)
    try {
        $FirstByte = $Stream.ReadByte()
        $SecondByte = $Stream.ReadByte()
    } finally {
        $Stream.Dispose()
    }
    if ($FirstByte -ne 0x4D -or $SecondByte -ne 0x5A) {
        Add-VerificationError "Z-View.exe 不包含有效的 Windows PE MZ 文件头。"
    } else {
        Write-Host "[PASS] Z-View.exe Windows PE 文件头有效。"
    }

    $PackageHash = (Get-FileHash -LiteralPath $PackageExePath -Algorithm SHA256).Hash
    Write-Host ("[PASS] GPO 包 EXE SHA256: {0}" -f $PackageHash)

    if (-not [string]::IsNullOrWhiteSpace($BuildExePath)) {
        if (-not (Test-Path -LiteralPath $BuildExePath -PathType Leaf)) {
            Add-VerificationError ("构建 EXE 不存在: {0}" -f $BuildExePath)
        } else {
            $BuildHash = (Get-FileHash -LiteralPath $BuildExePath -Algorithm SHA256).Hash
            if ($BuildHash -ne $PackageHash) {
                Add-VerificationError "构建 EXE 与 GPO 包 EXE 的 SHA256 不一致。"
            } else {
                Write-Host "[PASS] 构建 EXE 与 GPO 包 EXE 完全一致。"
            }
        }
    }
}

# 中文注释：发布前解析所有交付 PowerShell，提前拦截脚本语法错误。
$PowerShellFiles = @(
    (Join-Path $ProjectRoot "build_agent.ps1")
) + @(Get-ChildItem -LiteralPath $PackageDir -Filter "*.ps1" -File | Select-Object -ExpandProperty FullName)
foreach ($PowerShellFile in $PowerShellFiles) {
    $Tokens = $null
    $ParseErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        $PowerShellFile,
        [ref]$Tokens,
        [ref]$ParseErrors
    ) | Out-Null
    if ($ParseErrors.Count -gt 0) {
        $Messages = ($ParseErrors | ForEach-Object { $_.Message }) -join "; "
        Add-VerificationError ("PowerShell 语法错误: {0}: {1}" -f $PowerShellFile, $Messages)
    } else {
        Write-Host ("[PASS] PowerShell 语法: {0}" -f $PowerShellFile)
    }
}

$DriverRoot = Join-Path $PackageDir "Drivers\VirtualDisplay"
$DriverManifest = Join-Path $DriverRoot "driver_manifest.json"
$DriverInf = @(Get-ChildItem -LiteralPath $DriverRoot -Filter "*.inf" -File -ErrorAction SilentlyContinue)
$DriverCat = @(Get-ChildItem -LiteralPath $DriverRoot -Filter "*.cat" -File -ErrorAction SilentlyContinue)
$DriverSys = @(Get-ChildItem -LiteralPath $DriverRoot -Filter "*.sys" -File -ErrorAction SilentlyContinue)
$DriverReady = (Test-Path -LiteralPath $DriverManifest -PathType Leaf) -and
    $DriverInf.Count -gt 0 -and $DriverCat.Count -gt 0 -and $DriverSys.Count -gt 0
if ($DriverReady) {
    Write-Host "[PASS] 已包含虚拟显示驱动 manifest、INF、CAT 和 SYS。"
} else {
    $DriverMessage = "未包含完整的签名虚拟显示驱动载荷；无物理显示器的终端无法保证远控连续抓帧。"
    if ($RequireVirtualDisplayDriver) {
        Add-VerificationError $DriverMessage
    } else {
        Add-VerificationWarning $DriverMessage
    }
}

Write-Host ("==> 验证完成: 失败 {0} 项，警告 {1} 项。" -f $Errors.Count, $Warnings.Count)
if ($Errors.Count -gt 0) {
    throw ("发布包验收失败，共 {0} 项错误。" -f $Errors.Count)
}

Write-Host "发布包静态验收通过。" -ForegroundColor Green
