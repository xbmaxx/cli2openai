<#
.SYNOPSIS
    cli2openai Windows 卸载脚本
.DESCRIPTION
    移除计划任务，保留数据文件
#>
$ErrorActionPreference = 'Continue'

Write-Host "======== cli2openai 卸载 ========" -ForegroundColor Cyan

foreach ($name in @('cli2openai','cli2openai-serve')) {
    $t = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($t) {
        Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
        Write-Host "  [OK] 已移除 $name" -ForegroundColor Green
    } else {
        Write-Host "  [--] $name 不存在" -ForegroundColor DarkGray
    }
}

# 清理残留 python 进程
Get-CimInstance Win32_Process -Filter "Name like '%python%'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*cli2openai.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host "  [OK] 已结束残留进程 $($_.ProcessId)" }

Write-Host ""
Write-Host "服务已移除。数据文件保留在:"
Write-Host "  $env:USERPROFILE\.cli2openai\   (模型缓存 + 监控快照)"
Write-Host ""
Write-Host "如需彻底清除，手动删除上述目录。"
Write-Host "Hermes 配置里的 custom_providers 条目需自行移除。"
