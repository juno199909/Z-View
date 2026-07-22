[CmdletBinding()]
param(
    [switch]$SkipFrontendBuild,
    [switch]$RequireSupportedPython,
    [switch]$RequireVirtualDisplayDriver,
    [switch]$RequireCleanGitBaseline
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$FrontendRoot = Join-Path $ProjectRoot "frontend"
$VerifyPackageScript = Join-Path $ProjectRoot "verify_release_package.ps1"
$Warnings = New-Object System.Collections.Generic.List[string]
$Errors = New-Object System.Collections.Generic.List[string]

function Add-CheckWarning {
    param([string]$Message)

    $Warnings.Add($Message)
    Write-Host ("[WARN] {0}" -f $Message) -ForegroundColor Yellow
}

function Add-CheckError {
    param([string]$Message)

    $Errors.Add($Message)
    Write-Host ("[FAIL] {0}" -f $Message) -ForegroundColor Red
}

function Invoke-CheckedCommand {
    param(
        [string]$Description,
        [scriptblock]$Command
    )

    Write-Host ("==> {0}" -f $Description)
    try {
        & $Command
        Write-Host ("[PASS] {0}" -f $Description)
    } catch {
        Add-CheckError ("{0}: {1}" -f $Description, $_.Exception.Message)
    }
}

function Get-ReadinessTempRoot {
    $RequestedRoot = [Environment]::GetEnvironmentVariable("ZVIEW_READINESS_TEMP")
    if (-not [string]::IsNullOrWhiteSpace($RequestedRoot)) {
        return [System.IO.Path]::GetFullPath($RequestedRoot)
    }
    if (Test-Path -LiteralPath "D:\") {
        return "D:\IT2026-temp\release-readiness"
    }
    return (Join-Path $env:TEMP "Z-View-release-readiness")
}

function Test-PythonVersion {
    Write-Host "==> Check Python version"
    $Python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $Python) {
        Add-CheckError "python command not found."
        return
    }

    $VersionText = (& python -B -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')").Trim()
    Write-Host ("[INFO] Python version: {0}" -f $VersionText)
    $Parts = $VersionText.Split(".")
    $Major = [int]$Parts[0]
    $Minor = [int]$Parts[1]
    $Supported = ($Major -eq 3 -and $Minor -ge 10 -and $Minor -le 12)
    if (-not $Supported) {
        $Message = "Python 3.10-3.12 required for release validation, current is $VersionText."
        if ($RequireSupportedPython) {
            Add-CheckError $Message
        } else {
            Add-CheckWarning $Message
        }
    } else {
        Write-Host "[PASS] Python version is in supported range."
    }
}

function Test-PythonSyntax {
    param([string]$TempRoot)

    # 中文注释：只做内存 compile，不写 __pycache__，避免再次占用 C 盘。
    $env:PYTHONDONTWRITEBYTECODE = "1"
    $env:PYTHONPYCACHEPREFIX = Join-Path $TempRoot "pycache"
    New-Item -ItemType Directory -Force -Path $env:PYTHONPYCACHEPREFIX | Out-Null

    $PythonScript = @'
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
excluded_parts = {
    ".git",
    ".trae",
    "__pycache__",
    "node_modules",
    "dist",
}
failed = []
checked = 0
for path in root.rglob("*.py"):
    if any(part in excluded_parts for part in path.parts):
        continue
    try:
        source = path.read_text(encoding="utf-8-sig")
        compile(source, str(path), "exec")
        checked += 1
    except Exception as exc:
        failed.append(f"{path}: {exc}")

if failed:
    print("\n".join(failed), file=sys.stderr)
    sys.exit(1)
print(f"python_syntax_ok files={checked}")
'@

    Write-Host "==> Python in-memory syntax check"
    try {
        # 中文注释：通过标准输入传给 python，避免 python -c 在 Windows PowerShell 5.1 下破坏引号。
        $PythonScript | & python -B - $ProjectRoot
        if ($LASTEXITCODE -ne 0) {
            throw "python syntax check exited with code $LASTEXITCODE"
        }
        Write-Host "[PASS] Python in-memory syntax check"
    } catch {
        Add-CheckError ("Python in-memory syntax check: {0}" -f $_.Exception.Message)
    }
}

function Test-PowerShellSyntax {
    # 中文注释：解析所有交付脚本，提前发现语法错误，不执行脚本正文。
    Write-Host "==> Check PowerShell syntax"
    $ParseFailures = New-Object System.Collections.Generic.List[string]
    $PowerShellFiles = Get-ChildItem -LiteralPath $ProjectRoot -Recurse -Filter "*.ps1" -File |
        Where-Object {
            $_.FullName -notmatch "\\frontend\\node_modules\\" -and
            $_.FullName -notmatch "\\frontend\\dist\\"
        }

    foreach ($PowerShellFile in $PowerShellFiles) {
        $Tokens = $null
        $ParseErrors = $null
        [System.Management.Automation.Language.Parser]::ParseFile(
            $PowerShellFile.FullName,
            [ref]$Tokens,
            [ref]$ParseErrors
        ) | Out-Null
        if ($ParseErrors.Count -gt 0) {
            $Messages = ($ParseErrors | ForEach-Object { $_.Message }) -join "; "
            $ParseFailures.Add(("{0}: {1}" -f $PowerShellFile.FullName, $Messages))
        }
    }

    if ($ParseFailures.Count -gt 0) {
        Add-CheckError ("PowerShell syntax failures: {0}" -f ($ParseFailures -join " | "))
    } else {
        Write-Host ("[PASS] PowerShell syntax ok files={0}" -f @($PowerShellFiles).Count)
    }
}

function Test-FrontendAssets {
    # 中文注释：前端资源曾出现 0 字节占位文件，这里在构建前做硬检查，避免页面破图或打包图标失败。
    Write-Host "==> Check frontend static assets"
    $AssetErrorCount = 0
    $FrontendRootsToCheck = @($FrontendRoot)
    $MirrorFrontendRoot = Join-Path $ProjectRoot "IT\frontend"
    if (Test-Path -LiteralPath $MirrorFrontendRoot -PathType Container) {
        # 中文注释：项目内保留了 IT 副本目录，资源检查同步覆盖，避免主目录通过但副本仍是空文件。
        $FrontendRootsToCheck += $MirrorFrontendRoot
    }

    $RequiredAssets = @(
        @{ RelativePath = "public\favicon.ico"; Signature = [byte[]](0x00, 0x00, 0x01, 0x00); Name = "favicon.ico" },
        @{ RelativePath = "public\zview-logo.png"; Signature = [byte[]](0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A); Name = "zview-logo.png" }
    )

    foreach ($RootToCheck in $FrontendRootsToCheck) {
        foreach ($Asset in $RequiredAssets) {
            $AssetPath = Join-Path $RootToCheck ([string]$Asset.RelativePath)
            if (-not (Test-Path -LiteralPath $AssetPath -PathType Leaf)) {
                Add-CheckError ("Frontend asset missing: {0}" -f $AssetPath)
                $AssetErrorCount++
                continue
            }

            $Item = Get-Item -LiteralPath $AssetPath
            if ($Item.Length -le 0) {
                Add-CheckError ("Frontend asset is empty: {0}" -f $AssetPath)
                $AssetErrorCount++
                continue
            }

            $ExpectedSignature = [byte[]]$Asset.Signature
            $ActualSignature = [System.IO.File]::ReadAllBytes($AssetPath)
            if ($ActualSignature.Length -lt $ExpectedSignature.Length) {
                Add-CheckError ("Frontend asset is too small: {0}" -f $AssetPath)
                $AssetErrorCount++
                continue
            }

            for ($Index = 0; $Index -lt $ExpectedSignature.Length; $Index++) {
                if ($ActualSignature[$Index] -ne $ExpectedSignature[$Index]) {
                    Add-CheckError ("Frontend asset signature invalid: {0}" -f $AssetPath)
                    $AssetErrorCount++
                    break
                }
            }
        }
    }

    if ($AssetErrorCount -eq 0) {
        Write-Host "[PASS] Frontend static assets are valid."
    }
}

function Test-FrontendBuild {
    param([string]$TempRoot)

    if ($SkipFrontendBuild) {
        Add-CheckWarning "Frontend build skipped by parameter."
        return
    }
    if (-not (Test-Path -LiteralPath (Join-Path $FrontendRoot "package.json") -PathType Leaf)) {
        Add-CheckWarning "frontend/package.json not found, frontend build skipped."
        return
    }
    if (-not (Test-Path -LiteralPath (Join-Path $FrontendRoot "node_modules") -PathType Container)) {
        Add-CheckWarning "frontend/node_modules not found, run npm install before frontend build validation."
        return
    }

    # 中文注释：构建输出放到临时目录，不覆盖仓库内 frontend/dist。
    if ([string]::IsNullOrWhiteSpace($TempRoot)) {
        Add-CheckError "Frontend production build: temp root is empty."
        return
    }
    $FrontendBuildOutputPath = Join-Path $TempRoot "frontend-dist"
    $NpmCommand = Get-Command "npm.cmd" -ErrorAction SilentlyContinue
    if (-not $NpmCommand) {
        Add-CheckError "Frontend production build: npm.cmd not found."
        return
    }
    $NpmPath = $NpmCommand.Source
    $NpmBuildCommand = '& "' + $NpmPath + '" run build -- --outDir "' + $FrontendBuildOutputPath + '" --emptyOutDir'
    $PreviousLocation = Get-Location

    Write-Host "==> Frontend production build"
    try {
        Set-Location -LiteralPath $FrontendRoot
        # 中文注释：命令内容只来自 npm.cmd 路径和本地临时目录，避免 PowerShell 直接解析 --outDir。
        Invoke-Expression -Command $NpmBuildCommand
        if ($LASTEXITCODE -ne 0) {
            throw "npm build exited with code $LASTEXITCODE"
        }
        Set-Location -LiteralPath $PreviousLocation
        Write-Host "[PASS] Frontend production build"
    } catch {
        Set-Location -LiteralPath $PreviousLocation -ErrorAction SilentlyContinue
        Add-CheckError ("Frontend production build: {0}" -f $_.Exception.Message)
    }
}

function Test-ReleasePackage {
    if (-not (Test-Path -LiteralPath $VerifyPackageScript -PathType Leaf)) {
        Add-CheckError "verify_release_package.ps1 not found."
        return
    }

    Invoke-CheckedCommand -Description "GPO release package verification" -Command {
        if ($RequireVirtualDisplayDriver) {
            & $VerifyPackageScript -RequireVirtualDisplayDriver
        } else {
            & $VerifyPackageScript
        }
    }
}

function Test-ClosedRuntime {
    # 中文注释：发布前确认本机没有遗留调试服务，避免误把旧进程当成本次验收结果。
    Write-Host "==> Check local runtime is stopped"
    $Ports = @(5173, 8080, 8081, 8082, 9000)
    $ListeningPorts = @()
    foreach ($Port in $Ports) {
        $Connections = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
        if ($Connections.Count -gt 0) {
            $ListeningPorts += $Port
        }
    }
    if ($ListeningPorts.Count -gt 0) {
        Add-CheckWarning ("Project ports still listening: {0}" -f ($ListeningPorts -join ", "))
    } else {
        Write-Host "[PASS] Project ports are not listening."
    }

    $ProjectPath = [System.IO.Path]::GetFullPath($ProjectRoot)
    $ProjectProcesses = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            ($_.Name -in @("python.exe", "pythonw.exe", "node.exe", "Z-View.exe")) -and
            ([string]$_.CommandLine).IndexOf($ProjectPath, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
        })
    if ($ProjectProcesses.Count -gt 0) {
        Add-CheckWarning ("Project runtime processes still exist: {0}" -f (($ProjectProcesses | ForEach-Object { "$($_.Name)/$($_.ProcessId)" }) -join ", "))
    } else {
        Write-Host "[PASS] No project Python/Node/Z-View runtime process found."
    }
}

function Test-GitReleaseBaseline {
    # 中文注释：Git 基线检查只读取状态，不清理文件；严格模式用于发布前阻止缓存和未整理变更混入交付。
    Write-Host "==> Check Git release baseline"
    $GitCommand = Get-Command git -ErrorAction SilentlyContinue
    if (-not $GitCommand) {
        Add-CheckWarning "git command not found, Git release baseline check skipped."
        return
    }

    $RepoRoot = (& git -C $ProjectRoot rev-parse --show-toplevel 2>$null)
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($RepoRoot)) {
        Add-CheckWarning "Project root is not inside a Git repository, Git release baseline check skipped."
        return
    }
    $RepoRoot = $RepoRoot.Trim()

    $GeneratedPathspecs = @(
        ":(glob)**/node_modules/**",
        ":(glob)**/dist/**",
        ":(glob)**/__pycache__/**",
        ":(glob)**/*.pyc",
        ":(glob)**/*.log",
        ":(glob)**/.vite/**"
    )
    $TrackedGeneratedFiles = @(& git -C $RepoRoot ls-files -- $GeneratedPathspecs)
    if ($TrackedGeneratedFiles.Count -gt 0) {
        $Message = "Git tracks generated/cache files: count=$($TrackedGeneratedFiles.Count). Run cleanup_git_release_baseline.ps1 for a dry-run, then use -Apply after review."
        if ($RequireCleanGitBaseline) {
            Add-CheckError $Message
        } else {
            Add-CheckWarning $Message
        }
    } else {
        Write-Host "[PASS] No tracked generated/cache files found."
    }

    $StatusPathspecs = @(
        ".",
        ":(exclude)**/node_modules/**",
        ":(exclude)**/dist/**",
        ":(exclude)**/__pycache__/**",
        ":(exclude)**/*.pyc",
        ":(exclude)**/*.log"
    )
    $NonGeneratedChanges = @(& git -C $RepoRoot status --short -- $StatusPathspecs)
    if ($NonGeneratedChanges.Count -gt 0) {
        $Message = "Git has non-generated working tree changes: count=$($NonGeneratedChanges.Count). Review or commit before final release."
        if ($RequireCleanGitBaseline) {
            Add-CheckError $Message
        } else {
            Add-CheckWarning $Message
        }
    } else {
        Write-Host "[PASS] No non-generated working tree changes found."
    }
}

$TempRoot = Get-ReadinessTempRoot
New-Item -ItemType Directory -Force -Path $TempRoot | Out-Null
Write-Host ("==> Z-View release readiness check: {0}" -f $ProjectRoot)
Write-Host ("==> Temp root: {0}" -f $TempRoot)

Test-PythonVersion
Test-PythonSyntax -TempRoot $TempRoot
Test-PowerShellSyntax
Test-FrontendAssets
Test-FrontendBuild -TempRoot $TempRoot
Test-ReleasePackage
Test-ClosedRuntime
Test-GitReleaseBaseline

Write-Host ("==> Readiness result: errors={0}, warnings={1}" -f $Errors.Count, $Warnings.Count)
if ($Warnings.Count -gt 0) {
    foreach ($Warning in $Warnings) {
        Write-Host ("[WARN-SUMMARY] {0}" -f $Warning) -ForegroundColor Yellow
    }
}
if ($Errors.Count -gt 0) {
    throw ("Release readiness failed: {0} error(s)." -f $Errors.Count)
}

Write-Host "Release readiness checks passed." -ForegroundColor Green

