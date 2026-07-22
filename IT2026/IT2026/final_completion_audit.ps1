[CmdletBinding()]
param(
    [string]$EvidenceRoot = "",
    [string]$OutputJson = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = ""
$AuditItems = New-Object System.Collections.Generic.List[object]

function Add-AuditItem {
    param(
        [string]$Area,
        [string]$Name,
        [string]$Status,
        [string]$Evidence,
        [string]$Action
    )

    $AuditItems.Add([pscustomobject]@{
        Area = $Area
        Name = $Name
        Status = $Status
        Evidence = $Evidence
        Action = $Action
    })
}

function Test-LeafFile {
    param(
        [string]$Area,
        [string]$Name,
        [string]$Path,
        [int64]$MinBytes
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        Add-AuditItem $Area $Name "FAIL" ("缺少文件: {0}" -f $Path) "恢复或重新生成该文件。"
        return
    }

    $Item = Get-Item -LiteralPath $Path
    if ($Item.Length -lt $MinBytes) {
        Add-AuditItem $Area $Name "FAIL" ("文件过小: {0}, size={1}" -f $Path, $Item.Length) "检查是否为空文件或占位文件。"
        return
    }

    Add-AuditItem $Area $Name "PASS" ("{0}, size={1}" -f $Path, $Item.Length) "无。"
}

function Get-RepoRoot {
    $GitCommand = Get-Command git -ErrorAction SilentlyContinue
    if (-not $GitCommand) {
        return ""
    }

    $Result = (& git -C $ProjectRoot rev-parse --show-toplevel 2>$null)
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($Result)) {
        return ""
    }
    return $Result.Trim()
}

function Test-PythonReleaseVersion {
    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $PythonCommand) {
        Add-AuditItem "生产环境" "Python 版本" "FAIL" "未找到 python 命令。" "安装 Python 3.10、3.11 或 3.12 后复验。"
        return
    }

    $Version = (& python -B -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')").Trim()
    $Parts = $Version.Split(".")
    $Supported = ([int]$Parts[0] -eq 3 -and [int]$Parts[1] -ge 10 -and [int]$Parts[1] -le 12)
    if ($Supported) {
        Add-AuditItem "生产环境" "Python 版本" "PASS" ("当前 Python {0} 在 3.10-3.12 范围内。" -f $Version) "无。"
    } else {
        Add-AuditItem "生产环境" "Python 版本" "BLOCKED" ("当前 Python {0} 不在 3.10-3.12 发布验证范围内。" -f $Version) "切换到 Python 3.10-3.12 后运行 release_readiness_check.ps1 -RequireSupportedPython。"
    }
}

function Test-VirtualDisplayPayload {
    $DriverRoot = Join-Path $ProjectRoot "GPO部署包\Drivers\VirtualDisplay"
    $RequiredExtensions = @(".inf", ".cat", ".sys")
    $MissingExtensions = New-Object System.Collections.Generic.List[string]

    if (-not (Test-Path -LiteralPath $DriverRoot -PathType Container)) {
        Add-AuditItem "生产环境" "虚拟显示驱动载荷" "BLOCKED" ("缺少目录: {0}" -f $DriverRoot) "放入正式签名 IDD/虚拟显示驱动载荷。"
        return
    }

    foreach ($Extension in $RequiredExtensions) {
        $Matches = @(Get-ChildItem -LiteralPath $DriverRoot -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Extension -ieq $Extension -and $_.Length -gt 0 })
        if ($Matches.Count -eq 0) {
            $MissingExtensions.Add($Extension)
        }
    }

    $Manifest = Join-Path $DriverRoot "driver_manifest.json"
    if (-not (Test-Path -LiteralPath $Manifest -PathType Leaf)) {
        $MissingExtensions.Add("driver_manifest.json")
    }

    if ($MissingExtensions.Count -gt 0) {
        Add-AuditItem "生产环境" "虚拟显示驱动载荷" "BLOCKED" ("缺少正式载荷: {0}" -f ($MissingExtensions -join ", ")) "补齐 INF、CAT、SYS 和 driver_manifest.json 后运行 verify_release_package.ps1 -RequireVirtualDisplayDriver。"
    } else {
        Add-AuditItem "生产环境" "虚拟显示驱动载荷" "PASS" ("已发现完整载荷目录: {0}" -f $DriverRoot) "仍需在目标终端确认驱动签名和安装结果。"
    }
}

function Test-GitBaseline {
    if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
        Add-AuditItem "发布基线" "Git 仓库" "WARN" "当前目录不在 Git 仓库内，跳过 Git 基线。" "在 Git 工作副本中复验。"
        return
    }

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
        Add-AuditItem "发布基线" "已跟踪生成物" "BLOCKED" ("Git 仍跟踪生成/缓存文件 {0} 个。" -f $TrackedGeneratedFiles.Count) "先运行 cleanup_git_release_baseline.ps1 预览，确认后用 -Apply 移出索引。"
    } else {
        Add-AuditItem "发布基线" "已跟踪生成物" "PASS" "未发现已跟踪生成/缓存文件。" "无。"
    }

    $StatusPathspecs = @(
        ".",
        ":(exclude)**/node_modules/**",
        ":(exclude)**/dist/**",
        ":(exclude)**/__pycache__/**",
        ":(exclude)**/*.pyc",
        ":(exclude)**/*.log",
        ":(exclude)**/.vite/**"
    )
    $NonGeneratedChanges = @(& git -C $RepoRoot status --short -- $StatusPathspecs)
    if ($NonGeneratedChanges.Count -gt 0) {
        Add-AuditItem "发布基线" "非生成类变更" "BLOCKED" ("仍有非生成类工作区变更 {0} 项。" -f $NonGeneratedChanges.Count) "逐项审查后提交、暂存或恢复，不建议脚本自动处理。"
    } else {
        Add-AuditItem "发布基线" "非生成类变更" "PASS" "未发现非生成类工作区变更。" "无。"
    }
}

function Test-FieldEvidence {
    if ([string]::IsNullOrWhiteSpace($EvidenceRoot)) {
        $EvidenceRoot = Join-Path $ProjectRoot "验收证据"
    }

    $RequiredEvidence = @(
        @{ Name = "AD/GPO 客户端部署"; File = "ad_gpo_client_install.txt"; Action = "在测试 OU 通过 GPO 安装客户端，并保存 gpresult、安装日志或截图说明。" },
        @{ Name = "Windows Service 与用户会话助手"; File = "windows_service_user_session.txt"; Action = "保存服务状态、user-session-agent 心跳和相关日志。" },
        @{ Name = "Agent 心跳与任务轮询"; File = "agent_heartbeat_task_polling.txt"; Action = "保存平台在线状态、心跳记录和任务轮询日志。" },
        @{ Name = "软件安装卸载任务"; File = "software_install_uninstall_task.txt"; Action = "保存真实安装、卸载、失败回传和结果查询证据。" },
        @{ Name = "远控连续抓帧输入剪贴板文件"; File = "remote_desktop_continuity_input_clipboard_file.txt"; Action = "保存连续帧活性、鼠标键盘、剪贴板和文件传输验收证据。" }
    )

    foreach ($Evidence in $RequiredEvidence) {
        $EvidencePath = Join-Path $EvidenceRoot $Evidence.File
        if (Test-Path -LiteralPath $EvidencePath -PathType Leaf) {
            $Item = Get-Item -LiteralPath $EvidencePath
            if ($Item.Length -gt 0) {
                Add-AuditItem "现场验收" $Evidence.Name "PASS" ("{0}, size={1}" -f $EvidencePath, $Item.Length) "确认内容来自真实测试环境。"
                continue
            }
        }
        Add-AuditItem "现场验收" $Evidence.Name "BLOCKED" ("缺少证据文件: {0}" -f $EvidencePath) $Evidence.Action
    }
}

Write-Host ("==> Z-View 最终完成度审计: {0}" -f $ProjectRoot)
$RepoRoot = Get-RepoRoot

# 中文注释：这些检查证明仓库内核心交付物不是空文件或缺失文件。
Test-LeafFile "仓库内代码" "Agent 主入口" (Join-Path $ProjectRoot "cmdb_agent_unified_v2.py") 1024
Test-LeafFile "仓库内代码" "桌面上下文模块" (Join-Path $ProjectRoot "desktop_context.py") 1024
Test-LeafFile "仓库内代码" "发布验收脚本" (Join-Path $ProjectRoot "release_readiness_check.ps1") 1024
Test-LeafFile "仓库内代码" "Git 索引清理脚本" (Join-Path $ProjectRoot "cleanup_git_release_baseline.ps1") 1024
Test-LeafFile "发布包" "GPO Agent EXE" (Join-Path $ProjectRoot "GPO部署包\Z-View.exe") 1024
Test-LeafFile "发布包" "GPO 部署脚本" (Join-Path $ProjectRoot "GPO部署包\deploy.bat") 1024
Test-LeafFile "发布包" "GPO 卸载脚本" (Join-Path $ProjectRoot "GPO部署包\uninstall.bat") 1024

Test-PythonReleaseVersion
Test-VirtualDisplayPayload
Test-GitBaseline
Test-FieldEvidence

$AuditItems | Format-Table Area, Name, Status, Evidence -AutoSize

$BlockedCount = @($AuditItems | Where-Object { $_.Status -eq "BLOCKED" }).Count
$FailCount = @($AuditItems | Where-Object { $_.Status -eq "FAIL" }).Count
$WarnCount = @($AuditItems | Where-Object { $_.Status -eq "WARN" }).Count
$PassCount = @($AuditItems | Where-Object { $_.Status -eq "PASS" }).Count

if (-not [string]::IsNullOrWhiteSpace($OutputJson)) {
    # 中文注释：JSON 便于后续自动归档到发布记录或 CI 日志。
    $OutputObject = [pscustomobject]@{
        project_root = $ProjectRoot
        repo_root = $RepoRoot
        pass = $PassCount
        warn = $WarnCount
        blocked = $BlockedCount
        fail = $FailCount
        items = $AuditItems
    }
    $OutputDirectory = Split-Path -Parent $OutputJson
    if (-not [string]::IsNullOrWhiteSpace($OutputDirectory)) {
        New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
    }
    $OutputObject | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $OutputJson -Encoding UTF8
}

Write-Host ("==> 审计汇总: PASS={0}, WARN={1}, BLOCKED={2}, FAIL={3}" -f $PassCount, $WarnCount, $BlockedCount, $FailCount)
if ($FailCount -gt 0 -or $BlockedCount -gt 0) {
    Write-Host "最终 100% 尚未被当前证据证明；请按 BLOCKED/FAIL 项补齐后复验。" -ForegroundColor Yellow
    exit 2
}

Write-Host "最终 100% 已被当前审计证据证明。" -ForegroundColor Green
