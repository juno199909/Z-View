# Z-View 本地 CI 流水线（R10）
# 用法: powershell -File ci.ps1
# 阶段: 凭据扫描 → 后端编译 → pytest → API 冒烟 → 前端构建 → Agent 构建检查
$ErrorActionPreference = "Continue"
$code = "D:\IT2026\IT2026\IT2026\IT2026"
$fail = 0

function Step($name, $ok) {
    if ($ok) { Write-Host "[PASS] $name" -ForegroundColor Green }
    else { Write-Host "[FAIL] $name" -ForegroundColor Red; $script:fail++ }
}

Write-Host "=== Z-View CI ===" -ForegroundColor Cyan

# 1. 凭据扫描（工作区源码不得含真实凭据）
$hits = Get-ChildItem $code -Recurse -File -Include *.py,*.md,*.js,*.vue -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch 'node_modules|\.git|dist|__pycache__|GPO|config\.json' } |
    Select-String -Pattern 'qinghe@6308|Qinghe@6308|dTCxH6B3HuJuM' -List
Step "凭据扫描（工作区）" (-not $hits)

# 1b. git 跟踪检查：凭据文件不得入库
$tracked = git -C $code ls-files 2>$null | Select-String -Pattern 'IT2026/\.env$|IT2026/config\.json$|GPO.*config\.json'
Step 'git 跟踪检查（凭据文件不入库）' (-not $tracked)

# 2. 后端 py_compile
$files = Get-ChildItem $code -Filter *.py | Select-Object -ExpandProperty FullName
python -m py_compile @files 2>$null
Step "后端 py_compile ($($files.Count) 文件)" ($LASTEXITCODE -eq 0)

# 3. pytest（需环境变量 ZVIEW_TEST_PASSWORD / ZVIEW_AGENT_TOKEN）
Push-Location $code
$env:ZVIEW_TEST_PASSWORD = $env:ZVIEW_TEST_PASSWORD
if (-not $env:ZVIEW_AGENT_TOKEN -and (Test-Path "$code\.env")) {
    Get-Content "$code\.env" | ForEach-Object { if ($_ -match '^(ZVIEW_AGENT_TOKEN)=(.+)$') { Set-Item -Path "env:$($Matches[1])" -Value $Matches[2] } }
}
python -m pytest tests/ -q 2>$null | Select-Object -Last 1
Step "pytest tests/" ($LASTEXITCODE -eq 0)
Pop-Location

# 4. 前端构建
Push-Location "$code\frontend"
npm.cmd run build 2>$null | Out-Null
Step "前端构建" ($LASTEXITCODE -eq 0)
Pop-Location

# 5. Agent 构建（pyinstaller 配置校验级：仅编译 spec 依赖核心文件）
python -m py_compile "$code\cmdb_agent_core.py" "$code\cmdb_agent_unified_v2.py" "$code\remote_desktop_engine_v2.py" "$code\security_manager.py" 2>$null
Step "Agent 核心编译" ($LASTEXITCODE -eq 0)

Write-Host "=== CI 完成: $fail 项失败 ===" -ForegroundColor $(if ($fail -eq 0) { "Green" } else { "Red" })
exit $fail