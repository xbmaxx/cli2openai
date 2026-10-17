#!/bin/bash
# cli2openai 一键安装脚本 (macOS)
# 用法: bash install.sh
#
# 做四件事:
#   1. 检查依赖 (python3 / codebuddy CLI / 登录状态)
#   2. 用当前路径渲染 launchd plist
#   3. 装载为常驻服务 (开机自启 + 崩溃自动拉起)
#   4. 验证服务可用
set -e

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✅ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠️  $1${NC}"; }
fail() { echo -e "${RED}❌ $1${NC}"; exit 1; }

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOME_DIR="$HOME"
LOG_DIR="$HOME_DIR/Library/Logs"
LA_DIR="$HOME_DIR/Library/LaunchAgents"
UID_NUM="$(id -u)"

# 模板目录自适应:支持两种布局
#   独立部署: <dir>/launchd/*.template      (脚本与 plist 同层)
#   skill 包:  <skill>/scripts/install.sh + <skill>/templates/*.template
if [ -d "$BRIDGE_DIR/launchd" ]; then
    TPL_DIR="$BRIDGE_DIR/launchd"
elif [ -d "$BRIDGE_DIR/../templates" ]; then
    TPL_DIR="$(cd "$BRIDGE_DIR/../templates" && pwd)"
else
    echo "❌ 找不到 plist 模板目录 (尝试过: $BRIDGE_DIR/launchd, $BRIDGE_DIR/../templates)"; exit 1
fi

echo "════════ cli2openai 安装 ════════"
echo "安装目录: $BRIDGE_DIR"
echo

# ── 1. 依赖检查 ─────────────────────────────────
echo "① 检查依赖"

PY=""
for c in /usr/bin/python3 /opt/homebrew/bin/python3 "$(command -v python3 2>/dev/null)"; do
    [ -x "$c" ] && PY="$c" && break
done
[ -n "$PY" ] || fail "未找到 python3"
ok "python3: $PY ($($PY --version 2>&1))"

CB=""
for c in "$HOME_DIR/.local/bin/codebuddy" /opt/homebrew/bin/codebuddy /usr/local/bin/codebuddy; do
    [ -x "$c" ] && CB="$c" && break
done
if [ -z "$CB" ]; then
    warn "未找到 codebuddy CLI"
    echo "    请先安装: npm i -g @tencent-ai/codebuddy-code"
    echo "    国内网络慢可加: --registry=https://registry.npmmirror.com"
    fail "缺少 codebuddy CLI"
fi
ok "codebuddy: $CB"

# 登录状态检查
if ! "$CB" -p "ok" >/dev/null 2>&1; then
    warn "codebuddy 可能未登录"
    echo "    请运行: codebuddy"
    echo "    然后在 TUI 里输入: /login   (选 International Site 或 Chinese Site)"
    fail "请先完成 CLI 登录"
fi
ok "登录状态正常"

# 文件检查
for f in cli2openai.py model_watch.py; do
    [ -f "$BRIDGE_DIR/$f" ] || fail "缺少文件: $f"
done
ok "程序文件完整"

echo

# ── 2. 渲染 plist ───────────────────────────────
echo "② 生成 launchd 配置"
mkdir -p "$LOG_DIR" "$LA_DIR" "$HOME_DIR/.cli2openai"

render() {  # $1=模板文件  $2=输出路径
    sed -e "s|__HOME__|$HOME_DIR|g" \
        -e "s|__BRIDGE_DIR__|$BRIDGE_DIR|g" \
        -e "s|__LOG_DIR__|$LOG_DIR|g" \
        "$1" > "$2"
}

render "$TPL_DIR/com.local.cli2openai.plist.template" "$LA_DIR/com.local.cli2openai.plist"
render "$TPL_DIR/com.local.cli2openai-serve.plist.template"  "$LA_DIR/com.local.cli2openai-serve.plist"
ok "plist 已生成到 $LA_DIR"
echo

# ── 3. 装载服务 ─────────────────────────────────
echo "③ 装载常驻服务"
# 先卸掉旧的(忽略错误)
launchctl bootout "gui/$UID_NUM/com.local.cli2openai" 2>/dev/null || true
launchctl bootout "gui/$UID_NUM/com.local.cli2openai-serve"  2>/dev/null || true
# 清掉可能占用端口的手动进程
pkill -f "cli2openai.py" 2>/dev/null || true
sleep 1

launchctl bootstrap "gui/$UID_NUM" "$LA_DIR/com.local.cli2openai-serve.plist"  || fail "cli2openai-serve 装载失败"
ok "cli2openai-serve 已装载"
sleep 3
launchctl bootstrap "gui/$UID_NUM" "$LA_DIR/com.local.cli2openai.plist" || fail "cli2openai 装载失败"
ok "cli2openai 已装载"
echo

# ── 4. 验证 ─────────────────────────────────────
echo "④ 验证服务"
for i in $(seq 1 10); do
    if curl -sS -m 3 "http://127.0.0.1:8790/health" >/dev/null 2>&1; then break; fi
    sleep 2
done

if curl -sS -m 5 "http://127.0.0.1:8790/health" 2>/dev/null | grep -q '"ok"'; then
    ok "Bridge 运行中 http://127.0.0.1:8790/v1"
    curl -sS -m 5 "http://127.0.0.1:8790/health" | "$PY" -m json.tool 2>/dev/null | head -8 || true
else
    warn "Bridge 未响应,检查日志:"
    echo "    $LOG_DIR/cli2openai.err.log"
fi

echo
echo "════════ 安装完成 ════════"
echo
echo "下一步 — 把 provider 写进 Hermes 配置 (~/.hermes/config.yaml):"
echo
echo "  custom_providers:"
echo "    - name: cli2openai"
echo "      base_url: http://127.0.0.1:8790/v1"
echo "      api_key: wb-local-bridge"
echo "      model: deepseek-v4.1-flash"
echo "      api_mode: chat_completions"
echo "      models:"
echo "        - deepseek-v4.1-flash"
echo "        - hy3"
echo
echo "管理命令:"
echo "  launchctl list | grep wb-            # 查看状态"
echo "  launchctl kickstart -k gui/$UID_NUM/com.local.cli2openai   # 重启"
echo "  bash uninstall.sh                    # 卸载"
