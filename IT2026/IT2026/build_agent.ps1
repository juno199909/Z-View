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
$ExePath = Join-Path $DistDir "Z-View.exe"
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
Copy-Item -LiteralPath $RuntimeConfigPath -Destination (Join-Path $DistDir "config.json") -Force
Copy-Item -LiteralPath $RuntimeConfigPath -Destination (Join-Path $ReleasePackageDir "config.json") -Force

# 中文注释：如果项目内存在虚拟显示驱动载荷，则在构建阶段同步到 dist 和部署包目录。
if (Test-Path $DriverSourceDir) {
    Copy-Item -LiteralPath $DriverSourceDir -Destination $DistDir -Recurse -Force
    Copy-Item -LiteralPath $DriverSourceDir -Destination $ReleasePackageDir -Recurse -Force
}

# 中文注释：复制完成后立即做静态验收，避免把不完整或损坏的部署包交付给 GPO。
Write-Host "==> Verify release package"
& $VerifyScript -BuildExePath $ExePath -PackageDir $ReleasePackageDir

$ExeInfo = Get-Item -LiteralPath $ExePath
$UpdatedAt = Get-Date $ExeInfo.LastWriteTime -Format "yyyy-MM-dd HH:mm:ss"
Write-Host ("==> Build completed: {0}" -f $ExeInfo.FullName)
Write-Host ("    Size: {0:N0} bytes" -f $ExeInfo.Length)
Write-Host ("    Updated: {0}" -f $UpdatedAt)
