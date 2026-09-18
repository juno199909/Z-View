Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$SpecPath = Join-Path $ProjectRoot "build_agent.spec"
$RequestedBuildRoot = [Environment]::GetEnvironmentVariable("ZVIEW_BUILD_ROOT")
if ([string]::IsNullOrWhiteSpace($RequestedBuildRoot)) {
    if (Test-Path -LiteralPath "D:\") {
        $BuildRoot = "D:\IT2026-temp\zview-build"
    } else {
        $BuildRoot = Join-Path $env:TEMP "Z-View-build"
    }
} else {
    $BuildRoot = $RequestedBuildRoot
}
$BuildRoot = [System.IO.Path]::GetFullPath($BuildRoot)
$ProjectRootFull = [System.IO.Path]::GetFullPath($ProjectRoot)
if ($BuildRoot.Equals($ProjectRootFull, [System.StringComparison]::OrdinalIgnoreCase) -or
    $BuildRoot.StartsWith($ProjectRootFull.TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Build root must not be the project directory or one of its children: $BuildRoot"
}
$DistDir = Join-Path $BuildRoot "dist"
$BuildDir = Join-Path $BuildRoot "build"
$CacheDir = Join-Path $BuildRoot "pyinstaller-cache"
$TempDir = Join-Path $BuildRoot "temp"
# V1.8.3+ onedir 布局：产物为 dist\Z-View\{Z-View.exe,_internal,...}
$ExePath = Join-Path $DistDir "Z-View\Z-View.exe"
$VerifyScript = Join-Path $ProjectRoot "verify_release_package.ps1"
$RuntimeConfigPath = Join-Path $ProjectRoot "config.json"
$PackageDir = Get-ChildItem -LiteralPath $ProjectRoot -Directory |
    Where-Object { $_.Name -like "GPO*" } |
    Select-Object -ExpandProperty FullName -First 1
$DriverSourceDir = Join-Path $ProjectRoot "Drivers"

function Resolve-BuildPython {
    $pyCommand = Get-Command py -ErrorAction SilentlyContinue
    if ($pyCommand) {
        try {
            & $pyCommand.Source -3.12 --version *> $null
            if ($LASTEXITCODE -eq 0) {
                return @($pyCommand.Source, "-3.12")
            }
        } catch {
        }
    }

    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        return @($pythonCommand.Source)
    }

    throw "No usable Python runtime found for packaging."
}

if (-not $PackageDir) {
    throw "Deployment package directory not found."
}

Write-Host ("==> Use isolated build root: {0}" -f $BuildRoot)
New-Item -ItemType Directory -Force -Path $BuildRoot, $TempDir, $CacheDir | Out-Null

# 中文注释：构建和临时解压都放到 D 盘，避免 PyInstaller 消耗系统盘空间。
$env:TEMP = $TempDir
$env:TMP = $TempDir
$env:PYINSTALLER_CONFIG_DIR = $CacheDir

Write-Host "==> Clean old isolated build directories"
if (Test-Path $DistDir) {
    Remove-Item -LiteralPath $DistDir -Recurse -Force
}
if (Test-Path $BuildDir) {
    Remove-Item -LiteralPath $BuildDir -Recurse -Force
}

Write-Host "==> Run PyInstaller"
Set-Location $ProjectRoot
$BuildPython = Resolve-BuildPython
Write-Host ("==> Packaging with: {0}" -f ($BuildPython -join " "))
$BuildPythonExe = $BuildPython[0]
$BuildPythonArgs = @()
if ($BuildPython.Count -gt 1) {
    $BuildPythonArgs += $BuildPython[1..($BuildPython.Count - 1)]
}
$BuildPythonArgs += @("-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", $DistDir, "--workpath", $BuildDir, $SpecPath)
& $BuildPythonExe $BuildPythonArgs

if (-not (Test-Path $ExePath)) {
    throw "Build output not found: $ExePath"
}

# 中文注释：Authenticode 代码签名（P0-05）。企业自建代码签名证书，
# 需配合 GPO 把证书分发到终端的 Root + TrustedPublisher 存储。
# 证书缺失时告警但不阻断构建（开发环境允许未签名）。
$SignCertThumbprint = [Environment]::GetEnvironmentVariable("ZVIEW_CODESIGN_THUMBPRINT")
if ([string]::IsNullOrWhiteSpace($SignCertThumbprint)) {
    $SignCertThumbprint = "93C05132E7AD481010C68B37BAF25A1DA71CEADD"
}
$SignCert = Get-ChildItem "Cert:\CurrentUser\My\$SignCertThumbprint" -ErrorAction SilentlyContinue
if (-not $SignCert) {
    $SignCert = Get-ChildItem "Cert:\LocalMachine\My\$SignCertThumbprint" -ErrorAction SilentlyContinue
}
if ($SignCert) {
    Write-Host "==> Authenticode signing (P0-05)"
    $Signature = Set-AuthenticodeSignature -FilePath $ExePath -Certificate $SignCert -HashAlgorithm SHA256
    if ($Signature.Status -ne "Valid") {
        throw "Authenticode signing failed: $($Signature.Status) $($Signature.StatusMessage)"
    }
    Write-Host ("    Signed by: {0}" -f $Signature.SignerCertificate.Subject)
} else {
    Write-Host "==> WARNING: code signing certificate not found (thumbprint $SignCertThumbprint); exe left UNSIGNED"
}
if (-not (Test-Path $VerifyScript)) {
    throw "Release verification script not found: $VerifyScript"
}
if (-not (Test-Path -LiteralPath $RuntimeConfigPath -PathType Leaf)) {
    throw "Local runtime config is required to build a deployable Agent package: $RuntimeConfigPath"
}

Write-Host "==> Sync deployment package"
$ReleasePackageDir = Join-Path $DistDir "GPO部署包"
Copy-Item -LiteralPath $PackageDir -Destination $ReleasePackageDir -Recurse -Force
Copy-Item -LiteralPath $ExePath -Destination (Join-Path $ReleasePackageDir "Z-View.exe") -Force
# V1.9.13 onedir 布局：发布包根 = Z-View.exe + _internal\ + updater\ + version.txt
# （deploy.bat 依赖 version.txt 解析版本号并安装到 versions\<ver>\ + current junction）
Copy-Item -LiteralPath (Join-Path $DistDir "Z-View\_internal") -Destination (Join-Path $ReleasePackageDir "_internal") -Recurse -Force
$InitSource = Get-Content -LiteralPath (Join-Path $ProjectRoot "zvagent\__init__.py") -Raw
if ($InitSource -notmatch '__version__\s*=\s*"([0-9][^"]*)"') {
    throw "Unable to resolve agent version from zvagent/__init__.py"
}
$AgentVersion = $Matches[1]
Set-Content -LiteralPath (Join-Path $ReleasePackageDir "version.txt") -Value $AgentVersion -Encoding Ascii
Write-Host ("==> Agent version: {0}" -f $AgentVersion)
$UpdaterSource = Join-Path $ProjectRoot "dist_updater\ZViewUpdater.exe"
if (-not (Test-Path -LiteralPath $UpdaterSource -PathType Leaf)) {
    throw "Updater build missing: $UpdaterSource (python -m PyInstaller --onefile --name ZViewUpdater --paths . updater/updater.py --distpath dist_updater --workpath build_updater_work --specpath build_updater_work)"
}
New-Item -ItemType Directory -Force -Path (Join-Path $ReleasePackageDir "updater") | Out-Null
Copy-Item -LiteralPath $UpdaterSource -Destination (Join-Path $ReleasePackageDir "updater\ZViewUpdater.exe") -Force
# P0 修复：updater 同步进 onedir 输出（升级 zip 载荷）——升级后 versions\<ver>\updater\
# 随版本目录保留，后续升级由新版 updater 执行（旧版无 401 重试/kill_stale）
New-Item -ItemType Directory -Force -Path (Join-Path $DistDir "Z-View\updater") | Out-Null
Copy-Item -LiteralPath $UpdaterSource -Destination (Join-Path $DistDir "Z-View\updater\ZViewUpdater.exe") -Force
Copy-Item -LiteralPath $RuntimeConfigPath -Destination (Join-Path $DistDir "config.json") -Force
Copy-Item -LiteralPath $RuntimeConfigPath -Destination (Join-Path $ReleasePackageDir "config.json") -Force
# P1-UDP 远程桌面：WebTransport 根 CA 随包分发（deploy.bat 导入受信任根存储）
$WtRootCertSource = Join-Path $ProjectRoot "wt_certs\zview-root.cer"
if (Test-Path $WtRootCertSource) {
    Copy-Item -LiteralPath $WtRootCertSource -Destination (Join-Path $ReleasePackageDir "zview-root.cer") -Force
}

# 中文注释：如果项目内存在虚拟显示驱动载荷，则在构建阶段同步到 dist 和部署包目录。
if (Test-Path $DriverSourceDir) {
    Copy-Item -LiteralPath $DriverSourceDir -Destination $DistDir -Recurse -Force
    Copy-Item -LiteralPath $DriverSourceDir -Destination $ReleasePackageDir -Recurse -Force
}

# 中文注释：P0-05 签名证书公钥随部署包分发，GPO 可用它填充终端受信任存储。
$SignCertPublicPath = Join-Path $ProjectRoot "signing\zview-codesign.cer"
if (Test-Path $SignCertPublicPath) {
    Copy-Item -LiteralPath $SignCertPublicPath -Destination $DistDir -Force
    Copy-Item -LiteralPath $SignCertPublicPath -Destination $ReleasePackageDir -Force
}

# 中文注释：复制完成后立即做静态验收，避免把不完整或损坏的部署包交付给 GPO。
Write-Host "==> Verify release package"
& $VerifyScript -BuildExePath $ExePath -PackageDir $ReleasePackageDir

$ExeInfo = Get-Item -LiteralPath $ExePath
$UpdatedAt = Get-Date $ExeInfo.LastWriteTime -Format "yyyy-MM-dd HH:mm:ss"
Write-Host ("==> Build completed: {0}" -f $ExeInfo.FullName)
Write-Host ("    Size: {0:N0} bytes" -f $ExeInfo.Length)
Write-Host ("    Updated: {0}" -f $UpdatedAt)
