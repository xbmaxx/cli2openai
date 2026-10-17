#!/bin/bash
# cli2openai 卸载脚本 (macOS)
# 移除 launchd 服务,保留数据文件
set -e
UID_NUM="$(id -u)"
LA_DIR="$HOME/Library/LaunchAgents"

echo "════════ cli2openai 卸载 ════════"

for svc in cli2openai cli2openai-serve; do
    if launchctl bootout "gui/$UID_NUM/com.local.$svc" 2>/dev/null; then
        echo "✅ 已停止 $svc"
    else
        echo "ℹ️  $svc 未在运行"
    fi
    rm -f "$LA_DIR/com.local.$svc.plist"
done

pkill -f "cli2openai.py" 2>/dev/null && echo "✅ 已清理残留进程" || true

echo
echo "服务已移除。数据文件保留在:"
echo "  ~/.cli2openai/     (模型缓存 + 监控快照)"
echo "  ~/Library/Logs/wb-*.log  (日志)"
echo
echo "如需彻底清除,手动删除上述目录。"
echo "Hermes 配置里的 custom_providers 条目需自行移除。"
