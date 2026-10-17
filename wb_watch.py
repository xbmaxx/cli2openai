#!/usr/bin/env python3
"""
WorkBuddy 免费模型监控 —— 检测免费状态变化并告警

背景:
  模型的 credits 倍率由腾讯控制,随时可能从 x0.00 变成收费。
  本脚本定时快照,发现变化就告警,避免"不知情被扣积分"。

用法:
  python3 wb_watch.py              # 检查一次,有变化则输出告警(退出码 1)
  python3 wb_watch.py --json       # JSON 输出,便于接飞书/告警
  python3 wb_watch.py --baseline   # 强制重建基线快照

安装为定时任务(macOS launchd 示例见 README)。
"""
import json, os, sys, time, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wb_bridge import fetch_models, compute_free, NEVER_ALLOW, CACHE_FILE

SNAPSHOT = os.path.expanduser('~/.workbuddy-bridge/snapshot.json')


def take_snapshot():
    """拉当前模型清单,返回 {modelId: credits}"""
    models = fetch_models()
    if not models:
        return None
    snap = {}
    for m in models:
        mid = m.get('modelId') or m.get('id')
        if not mid:
            continue
        snap[mid] = ((m.get('_meta') or {}).get('credits') or '').strip()
    return snap


def load_snapshot():
    try:
        return json.load(open(SNAPSHOT))
    except Exception:
        return None


def classify(credits):
    """x0.00 / 空 → free;其它 → paid"""
    c = (credits or '').strip()
    return 'free' if (c in ('x0.00', 'x0', '0.00', '') or c.startswith('x0.00')) else 'paid'


def diff(old, new):
    """对比两次快照,返回变化列表"""
    changes = []
    for mid, cred in new.items():
        if mid in NEVER_ALLOW:
            continue
        was, now = classify((old or {}).get(mid, '')), classify(cred)
        if mid not in (old or {}):
            changes.append({'model': mid, 'type': 'new', 'credits': cred, 'level': 'info'})
        elif was != now:
            changes.append({
                'model': mid, 'type': f'{was}→{now}', 'credits': cred,
                'level': 'warn' if now == 'paid' else 'info',
                'old_credits': (old or {}).get(mid, '')})
    for mid in (old or {}):
        if mid not in new and mid not in NEVER_ALLOW:
            changes.append({'model': mid, 'type': 'removed', 'credits': '-', 'level': 'info'})
    return changes


def main():
    args = sys.argv[1:]
    force = '--baseline' in args

    new = take_snapshot()
    if not new:
        msg = '⚠️ 无法拉取模型清单(serve 未运行或未登录)'
        print(json.dumps({'ok': False, 'error': msg}, ensure_ascii=False) if '--json' in args else msg)
        return 2

    old = load_snapshot()
    changes = [] if (force or not old) else diff(old.get('models', {}), new)

    # 更新快照
    os.makedirs(os.path.dirname(SNAPSHOT), exist_ok=True)
    json.dump({'models': new, 'updatedAt': int(time.time()),
               'updatedAtHuman': datetime.datetime.now().isoformat(timespec='seconds')},
              open(SNAPSHOT, 'w'), ensure_ascii=False, indent=1)

    free_now = [m for m, c in new.items() if classify(c) == 'free' and m not in NEVER_ALLOW]
    warn = [c for c in changes if c['level'] == 'warn']

    if '--json' in args:
        print(json.dumps({'ok': True, 'free': sorted(free_now), 'changes': changes,
                          'warn': bool(warn)}, ensure_ascii=False, indent=1))
    else:
        ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
        if not old and not force:
            print(f'[{ts}] 建立基线快照 · 免费模型 {len(free_now)} 个: {", ".join(sorted(free_now))}')
        elif not changes:
            print(f'[{ts}] ✅ 无变化 · 免费模型 {len(free_now)} 个: {", ".join(sorted(free_now))}')
        else:
            print(f'[{ts}] ⚠️ 检测到 {len(changes)} 处变化:')
            for c in changes:
                flag = '🔴' if c['level'] == 'warn' else 'ℹ️'
                print(f"   {flag} {c['model']:28} {c['type']:14} credits={c['credits']}")
            if warn:
                print('\n   ⚠️ 有模型从免费变为收费 —— 请检查 Hermes 白名单是否需调整')

    return 1 if warn else 0


if __name__ == '__main__':
    sys.exit(main())
