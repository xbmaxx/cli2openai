<#
.SYNOPSIS
    WorkBuddy Bridge Windows 安装脚本
.DESCRIPTION
    用 Windows 计划任务实现常驻（开机/登录自启 + 崩溃自动重启）
    做四件事:
      1. 检查依赖 (python / codebuddy CLI / 登录状态)
      2. 注册两个计划任务 (wb-serve, wb-bridge)
      3. 启动并验证
      4. 打印后续配置步骤
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install.ps1
#>

$ErrorActionPreference = 'Stop'
$BridgeDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$HomeDir   = $env:USERPROFILE
$LogDir    = Join-Path $HomeDir 'workbuddy-bridge\logs'

function Ok   ($m) { Write-Host "  [OK] $m"   -ForegroundColor Green }
function Warn ($m) { Write-Host "  [!!] $m"   -ForegroundColor Yellow }
function Fail ($m) { Write-Host "  [XX] $m"   -ForegroundColor Red; exit 1 }

Write-Host "======== WorkBuddy Bridge 安装 (Windows) ========" -ForegroundColor Cyan
Write-Host "安装目录: $BridgeDir"
Write-Host ""

# ── 1. 依赖检查 ─────────────────────────────────
Write-Host "[1/4] 检查依赖"

$Python = $null
foreach ($c in @('py', 'python', 'python3')) {
    $cmd = Get-Command $c -ErrorAction SilentlyContinue
    if ($cmd) { $Python = $cmd.Source; break }
}
if (-not $Python) { Fail "未找到 Python，请先安装: https://www.python.org/downloads/  (勾选 Add to PATH)" }
Ok "Python: $Python"

$Node = Get-Command node -ErrorAction SilentlyContinue
if (-not $Node) { Warn "未找到 node（codebuddy 需要它）" }

# 探测 codebuddy
$Cb = $null
$cbCandidates = @(
    (Join-Path $HomeDir '.local\bin\codebuddy.cmd'),
    (Join-Path $env:APPDATA 'npm\codebuddy.cmd'),
    (Join-Path $HomeDir '.codebuddy\bin\codebuddy.exe')
)
foreach ($c in $cbCandidates) { if (Test-Path $c) { $Cb = $c; break } }
if (-not $Cb) {
    $cmd = Get-Command codebuddy -ErrorAction SilentlyContinue
    if ($cmd) { $Cb = $cmd.Source }
}
if (-not $Cb) {
    Warn "未找到 codebuddy CLI"
    Write-Host "      请先安装: npm i -g @tencent-ai/codebuddy-code"
    Write-Host "      国内网络慢可加: --registry=https://registry.npmmirror.com"
    Fail "缺少 codebuddy CLI"
}
Ok "codebuddy: $Cb"

# 登录状态
try {
    $null = & $Cb -p "ok" 2>&1
    if ($LASTEXITCODE -ne 0) { throw "exit $LASTEXITCODE" }
    Ok "登录状态正常"
} catch {
    Warn "codebuddy 可能未登录"
    Write-Host "      请运行: codebuddy"
    Write-Host "      然后在 TUI 里输入: /login (选 International Site 或 Chinese Site)"
    Fail "请先完成 CLI 登录"
}

foreach ($f in @('wb_bridge.py','wb_watch.py')) {
    if (-not (Test-Path (Join-Path $BridgeDir $f))) { Fail "缺少文件: $f" }
}
Ok "程序文件完整"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
Write-Host ""

# ── 2. 注册计划任务 ─────────────────────────────
Write-Host "[2/4] 注册计划任务"

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

function Register-WbTask {
    param($Name, $Exe, $ArgLine, $WorkDir)
    Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue
    $action  = New-ScheduledTaskAction -Execute $Exe -Argument $ArgLine -WorkingDirectory $WorkDir
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger `
        -Settings $Settings -Description "WorkBuddy Bridge - $Name" -Force | Out-Null
    Start-ScheduledTask -TaskName $Name
    Ok "$Name 已注册并启动"
}

# wb-serve（codebuddy HTTP 服务，Bridge 靠它拉模型清单）
Register-WbTask -Name 'wb-serve' -Exe $Cb `
    -ArgLine '--serve --port 7890 --session-id wb-serve-daemon' -WorkDir $env:TEMP

Start-Sleep -Seconds 4

# wb-bridge（主服务）
Register-WbTask -Name 'wb-bridge' -Exe $Python `
    -ArgLine "`"$BridgeDir\wb_bridge.py`" --port 8790" -WorkDir $BridgeDir

Write-Host ""

# ── 3. 验证 ─────────────────────────────────────
Write-Host "[3/4] 验证服务"
$healthy = $false
for ($i = 0; $i -lt 10; $i++) {
    try {
        $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8790/health' -TimeoutSec 4 -UseBasicParsing
        if ($r.StatusCode -eq 200) { $healthy = $true; break }
    } catch { }
    Start-Sleep -Seconds 2
}

if ($healthy) {
    Ok "Bridge 运行中 http://127.0.0.1:8790/v1"
    Write-Host "      $($r.Content)"
} else {
    Warn "Bridge 未响应，请检查日志: $LogDir"
}

Write-Host ""
# ── 4. Hermes 配置 ──────────────────────────────
Write-Host "[4/4] 下一步 — 写入 Hermes 配置"
Write-Host ""
Write-Host "  方案 A（推荐）：运行自动注入脚本"
Write-Host "     $Python `"$BridgeDir\setup_hermes.py`""
Write-Host ""
Write-Host "  方案 B：手动在 %USERPROFILE%\.hermes\config.yaml 的 custom_providers: 段追加"
Write-Host ""
Write-Host "     - name: workbuddy-free"
Write-Host "       base_url: http://127.0.0.1:8790/v1"
Write-Host "       api_key: wb-local-bridge"
Write-Host "       model: deepseek-v4.1-flash"
Write-Host "       api_mode: chat_completions"
Write-Host "       models:"
Write-Host "         - deepseek-v4.1-flash"
Write-Host "         - hy3"
Write-Host ""
Write-Host "  管理命令:"
Write-Host "     Get-ScheduledTask wb-*              # 查看状态"
Write-Host "     Restart-ScheduledTask wb-bridge     # 重启"
Write-Host "     powershell -File uninstall.ps1      # 卸载"
Write-Host ""
Write-Host "======== 安装完成 ========" -ForegroundColor Green
