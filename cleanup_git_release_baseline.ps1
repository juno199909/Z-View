[CmdletBinding()]
param(
    [switch]$Apply,
    [int]$PreviewCount = 30,
    [int]$BatchSize = 100
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

function Write-Info {
    param([string]$Message)

    Write-Host ("[INFO] {0}" -f $Message)
}

function Write-Warn {
    param([string]$Message)

    Write-Host ("[WARN] {0}" -f $Message) -ForegroundColor Yellow
}

function Write-Pass {
    param([string]$Message)

    Write-Host ("[PASS] {0}" -f $Message) -ForegroundColor Green
}

function Invoke-GitChecked {
    param([string[]]$Arguments)

    & git @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw ("git {0} failed with exit code {1}" -f ($Arguments -join " "), $LASTEXITCODE)
    }
}

function Get-GitRepoRoot {
    $GitCommand = Get-Command git -ErrorAction SilentlyContinue
    if (-not $GitCommand) {
        throw "git command not found."
    }

    $RepoRoot = (& git -C $ScriptRoot rev-parse --show-toplevel 2>$null)
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($RepoRoot)) {
        throw ("当前目录不在 Git 仓库中: {0}" -f $ScriptRoot)
    }
    return $RepoRoot.Trim()
}

function Get-GeneratedRules {
    # 中文注释：这些目录和文件都可以由安装、构建或运行重新生成，不应作为源码长期跟踪。
    return @(
        @{ Name = "node_modules"; Pathspec = ":(glob)**/node_modules/**"; Reason = "前端依赖可通过 npm ci 或 npm install 恢复" },
        @{ Name = "dist"; Pathspec = ":(glob)**/dist/**"; Reason = "前端构建产物应由构建命令生成" },
        @{ Name = "__pycache__"; Pathspec = ":(glob)**/__pycache__/**"; Reason = "Python 字节码缓存会污染差异并占用 C 盘" },
        @{ Name = "pyc"; Pathspec = ":(glob)**/*.pyc"; Reason = "Python 字节码缓存不属于源码" },
        @{ Name = "log"; Pathspec = ":(glob)**/*.log"; Reason = "运行日志不属于发布源码" },
        @{ Name = ".vite"; Pathspec = ":(glob)**/.vite/**"; Reason = "Vite 依赖预构建缓存可重新生成" }
    )
}

function Get-TrackedGeneratedFiles {
    param(
        [string]$RepoRoot,
        [array]$Rules
    )

    $AllFiles = New-Object "System.Collections.Generic.HashSet[string]"
    $CategoryRows = New-Object System.Collections.Generic.List[object]

    foreach ($Rule in $Rules) {
        $Files = @(& git -C $RepoRoot ls-files -- $Rule.Pathspec)
        if ($LASTEXITCODE -ne 0) {
            throw ("git ls-files failed for pathspec: {0}" -f $Rule.Pathspec)
        }

        foreach ($Path in $Files) {
            [void]$AllFiles.Add($Path)
        }

        $CategoryRows.Add([pscustomobject]@{
            Category = $Rule.Name
            Count = $Files.Count
            Reason = $Rule.Reason
        })
    }

    return [pscustomobject]@{
        Files = @($AllFiles.GetEnumerator() | Sort-Object)
        Categories = $CategoryRows
    }
}

function Get-NonGeneratedStatus {
    param([string]$RepoRoot)

    # 中文注释：非生成类变更只提示人工复核，脚本不会自动处理，避免误伤用户正在开发的代码。
    $StatusPathspecs = @(
        ".",
        ":(exclude)**/node_modules/**",
        ":(exclude)**/dist/**",
        ":(exclude)**/__pycache__/**",
        ":(exclude)**/*.pyc",
        ":(exclude)**/*.log",
        ":(exclude)**/.vite/**"
    )
    $Status = @(& git -C $RepoRoot status --short -- $StatusPathspecs)
    if ($LASTEXITCODE -ne 0) {
        throw "git status failed while checking non-generated changes."
    }
    return $Status
}

function Remove-GeneratedFilesFromIndex {
    param(
        [string]$RepoRoot,
        [string[]]$Files,
        [int]$BatchSize
    )

    if ($BatchSize -lt 1) {
        throw "BatchSize must be greater than 0."
    }

    $Removed = 0
    for ($Offset = 0; $Offset -lt $Files.Count; $Offset += $BatchSize) {
        $EndIndex = [Math]::Min($Offset + $BatchSize - 1, $Files.Count - 1)
        $Batch = $Files[$Offset..$EndIndex]

        # 中文注释：--cached 只移出 Git 索引，-f 允许处理本地已修改或已删除的生成物，-q 避免输出上万行。
        $Arguments = @("-C", $RepoRoot, "rm", "--cached", "-r", "-f", "-q", "--") + $Batch
        Invoke-GitChecked -Arguments $Arguments
        $Removed += $Batch.Count
        Write-Info ("已从 Git 索引移出生成物: {0}/{1}" -f $Removed, $Files.Count)
    }
}

Write-Host ("==> Z-View Git 发布基线清理: {0}" -f $ScriptRoot)
$RepoRoot = Get-GitRepoRoot
Write-Info ("Git 仓库根目录: {0}" -f $RepoRoot)

$Rules = Get-GeneratedRules
$GeneratedResult = Get-TrackedGeneratedFiles -RepoRoot $RepoRoot -Rules $Rules
$GeneratedFiles = [string[]]$GeneratedResult.Files
$NonGeneratedStatus = @(Get-NonGeneratedStatus -RepoRoot $RepoRoot)

Write-Host "==> 已跟踪生成物分类"
$GeneratedResult.Categories | Format-Table -AutoSize

Write-Info ("已跟踪生成/缓存文件总数: {0}" -f $GeneratedFiles.Count)
Write-Info ("非生成类工作区变更数量: {0}" -f $NonGeneratedStatus.Count)

if ($GeneratedFiles.Count -gt 0) {
    Write-Host ("==> 预览前 {0} 个将从 Git 索引移出的路径" -f $PreviewCount)
    $GeneratedFiles | Select-Object -First $PreviewCount | ForEach-Object { Write-Host ("  {0}" -f $_) }
}

if ($NonGeneratedStatus.Count -gt 0) {
    Write-Warn "检测到非生成类工作区变更，本脚本不会处理，请人工确认后提交或恢复。"
    $NonGeneratedStatus | Select-Object -First $PreviewCount | ForEach-Object { Write-Host ("  {0}" -f $_) }
}

if (-not $Apply) {
    Write-Warn "当前是预览模式，未修改 Git 索引。确认无误后可追加 -Apply 执行。"
    exit 0
}

if ($GeneratedFiles.Count -eq 0) {
    Write-Pass "没有需要从 Git 索引移出的生成物。"
    exit 0
}

Remove-GeneratedFilesFromIndex -RepoRoot $RepoRoot -Files $GeneratedFiles -BatchSize $BatchSize
Write-Pass "生成物已从 Git 索引移出，磁盘文件已保留。下一步请运行 release_readiness_check.ps1 -SkipFrontendBuild -RequireCleanGitBaseline 复核。"
