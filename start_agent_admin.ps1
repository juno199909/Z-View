# CMDB Agent 启动脚本（管理员权限）
# 必须以管理员权限运行此脚本

Write-Host "正在停止现有Agent服务..." -ForegroundColor Yellow
Get-Process python -ErrorAction SilentlyContinue | Where-Object {
    (netstat -ano | Select-String ":9000" | Select-String $_.Id)
} | Stop-Process -Force

Start-Sleep -Seconds 2

Write-Host "正在启动Agent服务（管理员权限）..." -ForegroundColor Green
Set-Location "c:\Users\Administrator\Desktop\IT2026\IT2026"

# 在当前窗口运行，而不是新窗口
python cmdb_agent_unified_v2.py

# 如果脚本退出，暂停以查看错误
Read-Host "按Enter键退出"
