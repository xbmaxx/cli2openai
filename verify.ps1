<#
.SYNOPSIS  cli2openai 自检 (Windows)
#>
$ErrorActionPreference='Continue'; $P=0;$F=0;$W=0
function Ok($m){Write-Host "  [OK] $m" -ForegroundColor Green; $script:P++}
function Bad($m){Write-Host "  [XX] $m" -ForegroundColor Red; $script:F++}
function Warn2($m){Write-Host "  [!!] $m" -ForegroundColor Yellow; $script:W++}
$Port=if($args[0]){$args[0]}else{8790}
Write-Host "==== cli2openai 自检 (Windows) ====" -ForegroundColor Cyan

# 1 依赖
$py=Get-Command py,python,python3 -ErrorAction SilentlyContinue | Select-Object -First 1
if($py){Ok "python: $($py.Source)"}else{Bad "python 缺失"}
$cb=Get-Command codebuddy -ErrorAction SilentlyContinue
if($cb){Ok "codebuddy: $($cb.Source)"}else{Bad "codebuddy 缺失"}

# 2 登录
if($cb){ try{ $null=& $cb.Source -p "ok" 2>&1; if($LASTEXITCODE -eq 0){Ok "已登录"}else{Bad "未登录 - 运行 codebuddy 后 /login"} }catch{Bad "登录检查失败"} }

# 3 计划任务
foreach($n in @('cli2openai-serve','cli2openai')){
  $t=Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue
  if($t -and $t.State -eq 'Running'){Ok "$n 运行中"}elseif($t){Warn2 "$n 已注册未运行"}else{Bad "$n 未注册"}
}

# 4 端口
try{ $r=Invoke-WebRequest "http://127.0.0.1:$Port/health" -TimeoutSec 5 -UseBasicParsing; Ok "Bridge :$Port 响应正常"; $h=($r.Content|ConvertFrom-Json) }catch{Bad "Bridge :$Port 无响应"; $h=$null}

# 5 白名单
if($h){ Ok ("白名单: "+($h.free_models -join ' ')); if($h.free_models -contains 'default-model'){Bad "default-model 不应在白名单"}else{Ok "default-model 已排除"} }

# 6 实测
if($h -and $h.free_models){
  $m=$h.free_models[0]
  try{
    $body=@{model=$m;messages=@(@{role='user';content='回复两个字:正常'})}|ConvertTo-Json -Depth 5
    $r2=Invoke-WebRequest "http://127.0.0.1:$Port/v1/chat/completions" -Method POST -Body $body -ContentType 'application/json' -TimeoutSec 90 -UseBasicParsing
    $t2=($r2.Content|ConvertFrom-Json).choices[0].message.content
    if($t2){Ok "模型 $m 返回: $t2"}else{Bad "调用返回空"}
  }catch{Bad "调用失败: $_"}
  try{
    $r3=Invoke-WebRequest "http://127.0.0.1:$Port/v1/chat/completions" -Method POST -Body (@{model='gpt-5.5';messages=@(@{role='user';content='x'})}|ConvertTo-Json -Depth 5) -ContentType 'application/json' -TimeoutSec 15 -UseBasicParsing
    Bad "拦截失效 (gpt-5.5 未被拒绝)"
  }catch{ if($_.Exception.Response.StatusCode.value__ -eq 403){Ok "收费模型拦截生效"}else{Warn2 "拦截检查异常"} }
}

# 7 Hermes 配置
$cfg="$env:USERPROFILE\.hermes\config.yaml"
if(Test-Path $cfg){ if(Select-String -Path $cfg -Pattern 'cli2openai' -Quiet){Ok "cli2openai 已配置"}else{Warn2 "未配置 (运行 setup_hermes.py)"} }else{Warn2 "未找到 $cfg"}

Write-Host ""
Write-Host "==== 结果: 通过 $P  警告 $W  失败 $F ====" -ForegroundColor Cyan
if($F -eq 0){Write-Host "  一切正常" -ForegroundColor Green; exit 0}else{exit 1}
