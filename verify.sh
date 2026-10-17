#!/bin/bash
# cli2openai 自检 —— 一条命令输出完整体检报告
# 用法: bash verify.sh
#
# 检查 8 项: 依赖 / 登录 / 服务 / 端口 / 白名单 / 实测调用 / Hermes 配置 / 日志

BRIDGE_PORT="${1:-8790}"
SERVE_PORT=7890
LA_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs"
# 本脚本所在目录(用于定位 cli2openai.py / selftest_parser.py)
BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
PASS=0; FAIL=0; WARN=0

ok()   { echo -e "  \033[0;32m✅ $1\033[0m"; PASS=$((PASS+1)); }
bad()  { echo -e "  \033[0;31m❌ $1\033[0m"; FAIL=$((FAIL+1)); }
warn() { echo -e "  \033[1;33m⚠️  $1\033[0m"; WARN=$((WARN+1)); }
hdr()  { echo; echo "── $1 ─────────────────────────────"; }

echo "════════ cli2openai 自检 ════════"
echo "时间: $(date '+%Y-%m-%d %H:%M:%S')"

# ── 1. 依赖 ─────────────────────────────────────
hdr "1/8 依赖"
PY=""
for c in /usr/bin/python3 /opt/homebrew/bin/python3 "$(command -v python3 2>/dev/null)"; do
    [ -x "$c" ] && PY="$c" && break
done
[ -n "$PY" ] && ok "python3: $($PY --version 2>&1)" || bad "python3 缺失"

# node 是 codebuddy 的运行时依赖(shebang: #!/usr/bin/env node)
NODE=""
for c in "$HOME/.local/bin/node" /opt/homebrew/bin/node /usr/local/bin/node "$(command -v node 2>/dev/null)"; do
    [ -x "$c" ] && NODE="$c" && break
done
if [ -z "$NODE" ]; then
    for d in "$HOME"/.nvm/versions/node/*/bin/node; do
        [ -x "$d" ] && NODE="$d" && break
    done
fi
[ -n "$NODE" ] && ok "node: $($NODE --version 2>&1)" || bad "node 缺失 —— codebuddy 无法运行(装 node 或配 PATH)"

CB=""
for c in "$HOME/.local/bin/codebuddy" /opt/homebrew/bin/codebuddy /usr/local/bin/codebuddy; do
    [ -x "$c" ] && CB="$c" && break
done
[ -n "$CB" ] && ok "codebuddy: $CB" || bad "codebuddy 缺失 (npm i -g @tencent-ai/codebuddy-code)"

# ── 2. 登录 ─────────────────────────────────────
hdr "2/8 codebuddy 登录状态"
if [ -n "$CB" ]; then
    if "$CB" -p "ok" >/dev/null 2>&1; then
        ok "已登录"
    else
        bad "未登录 —— 运行 codebuddy 后输入 /login"
    fi
fi

# ── 3. 常驻服务 ─────────────────────────────────
hdr "3/8 常驻服务 (launchd)"
for svc in cli2openai-serve cli2openai; do
    pid=$(launchctl list 2>/dev/null | awk -v s="com.local.$svc" '$3==s {print $1}')
    if [ -n "$pid" ] && [ "$pid" != "-" ]; then
        ok "$svc 运行中 (PID $pid)"
    elif launchctl list 2>/dev/null | grep -q "com.local.$svc"; then
        warn "$svc 已装载但未运行"
    else
        bad "$svc 未装载 (运行 install.sh)"
    fi
done

# ── 4. 端口 ─────────────────────────────────────
hdr "4/8 端口响应"
if curl -sS -m 5 "http://127.0.0.1:$BRIDGE_PORT/health" >/dev/null 2>&1; then
    ok "Bridge :$BRIDGE_PORT 响应正常"
else
    bad "Bridge :$BRIDGE_PORT 无响应"
fi
if curl -sS -m 5 -o /dev/null "http://127.0.0.1:$SERVE_PORT/api/v1/auth/status" 2>/dev/null; then
    ok "Serve :$SERVE_PORT 响应正常"
else
    warn "Serve :$SERVE_PORT 无响应 (Bridge 将回退兜底白名单)"
fi

# ── 5. 白名单 ───────────────────────────────────
hdr "5/8 免费模型白名单"
WL=$(curl -sS -m 5 "http://127.0.0.1:$BRIDGE_PORT/health" 2>/dev/null | "$PY" -c "
import json,sys
try:
    d=json.load(sys.stdin); print(' '.join(d.get('free_models',[])))
except: print('')
" 2>/dev/null)
if [ -n "$WL" ]; then
    ok "白名单: $WL"
    echo "$WL" | grep -q "default-model" && bad "危险! default-model(Auto) 不应在白名单" || ok "default-model 已正确排除"
else
    bad "无法读取白名单"
fi

# ── 6. 实测调用 ─────────────────────────────────
hdr "6/8 实测调用 (免费模型 + 工具闭环)"
if [ -n "$WL" ]; then
    M=$(echo "$WL" | awk '{print $1}')
    R=$(curl -sS -m 90 -X POST "http://127.0.0.1:$BRIDGE_PORT/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "{\"model\":\"$M\",\"messages\":[{\"role\":\"user\",\"content\":\"回复两个字:正常\"}]}" 2>/dev/null \
        | "$PY" -c "import json,sys;print(json.load(sys.stdin)['choices'][0]['message']['content'])" 2>/dev/null)
    [ -n "$R" ] && ok "模型 $M 返回: $R" || bad "调用失败"

    # 工具闭环测试(关键:验证 DSML→tool_calls 转换 + tool 角色回灌)
    TC=$(curl -sS -m 90 -X POST "http://127.0.0.1:$BRIDGE_PORT/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "{\"model\":\"$M\",\"messages\":[{\"role\":\"user\",\"content\":\"用 terminal 执行 ls /tmp\"}],\"tools\":[{\"type\":\"function\",\"function\":{\"name\":\"terminal\",\"description\":\"执行命令\",\"parameters\":{\"type\":\"object\",\"properties\":{\"command\":{\"type\":\"string\"}},\"required\":[\"command\"]}}}]}" 2>/dev/null \
        | "$PY" -c "import json,sys;d=json.load(sys.stdin);m=d['choices'][0]['message'];print('YES' if m.get('tool_calls') else 'NO')" 2>/dev/null)
    [ "$TC" = "YES" ] && ok "工具调用闭环正常 (DSML → tool_calls)" || bad "工具调用失败(模型未返回 tool_calls)"

    # 解析器自检(独立文件,避免 shell 转义问题)
    PT=$("$PY" "$BRIDGE_DIR/selftest_parser.py" 2>&1 | tail -1)
    echo "$PT" | grep -q "失败 0" && ok "解析器自检全通过 (JSON主路径/嵌套参数/标记兜底/白名单)" \
        || bad "解析器自检失败 → $PT"
else
    bad "无白名单,跳过"
fi
# 拦截测试
C=$(curl -sS -m 15 -o /dev/null -w "%{http_code}" -X POST "http://127.0.0.1:$BRIDGE_PORT/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -d '{"model":"gpt-5.5","messages":[{"role":"user","content":"x"}]}' 2>/dev/null)
[ "$C" = "403" ] && ok "收费模型拦截生效 (gpt-5.5 → 403)" || bad "拦截失效 (gpt-5.5 → $C,应为 403)"

# ── 7. Hermes 配置 ──────────────────────────────
hdr "7/8 Hermes 配置"
CFG="$HOME/.hermes/config.yaml"
if [ -f "$CFG" ]; then
    if grep -q "cli2openai" "$CFG" 2>/dev/null; then
        ok "cli2openai provider 已配置"
    else
        warn "未找到 cli2openai (运行 setup_hermes.py)"
    fi
else
    warn "未找到 $CFG"
fi

# ── 8. 日志 ─────────────────────────────────────
hdr "8/8 日志位置"
for f in "$LOG_DIR/cli2openai.log" "$LOG_DIR/cli2openai.err.log" "$LOG_DIR/cli2openai-serve.log"; do
    if [ -f "$f" ]; then
        sz=$(wc -c < "$f" | tr -d ' ')
        ok "$(basename $f) (${sz} bytes)"
    fi
done
[ -s "$LOG_DIR/cli2openai.err.log" ] && echo "      最近错误:" && tail -3 "$LOG_DIR/cli2openai.err.log" | sed 's/^/        /'

# ── 汇总 ────────────────────────────────────────
echo
echo "════════ 结果 ════════"
echo -e "  \033[0;32m通过 $PASS\033[0m  \033[1;33m警告 $WARN\033[0m  \033[0;31m失败 $FAIL\033[0m"
echo
if [ $FAIL -eq 0 ]; then
    echo "  🎉 一切正常"
    exit 0
else
    echo "  请按上面 ❌ 项修复;修改代码后运行: bash install.sh 重启服务"
    exit 1
fi
