#!/usr/bin/env python3
"""
cli2openai 模型目录监控 —— 检测可用模型清单变化并告警

背景:
  CLI 的模型目录由上游控制，条目随时可能增减或改变计费属性。
  本脚本定时快照，发现变化就告警，便于及时调整客户端配置。

用法:
  python3 model_watch.py              # 检查一次,有变化则输出告警(退出码 1)
  python3 model_watch.py --json       # JSON 输出,便于接飞书/告警
  python3 model_watch.py --baseline   # 强制重建基线快照

安装为定时任务(macOS launchd 示例见 README)。
"""
import json, os, sys, time, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cli2openai import fetch_models, select_included_models, NEVER_ALLOW, CACHE_FILE

SNAPSHOT = os.path.expanduser('~/.cli2openai/snapshot.json')


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
    """credits 为 0 / 空 → included（不额外计费）;其它 → billable"""
    c = (credits or '').strip()
    return 'included' if (c in ('x0.00', 'x0', '0.00', '') or c.startswith('x0.00')) else 'billable'


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
                'level': 'warn' if now == 'billable' else 'info',
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

    included_now = [m for m, c in new.items() if classify(c) == 'included' and m not in NEVER_ALLOW]
    warn = [c for c in changes if c['level'] == 'warn']

    if '--json' in args:
        print(json.dumps({'ok': True, 'included': sorted(included_now), 'changes': changes,
                          'warn': bool(warn)}, ensure_ascii=False, indent=1))
    else:
        ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
        if not old and not force:
            print(f'[{ts}] 建立基线快照 · 可用模型 {len(included_now)} 个: {", ".join(sorted(included_now))}')
        elif not changes:
            print(f'[{ts}] ✅ 无变化 · 可用模型 {len(included_now)} 个: {", ".join(sorted(included_now))}')
        else:
            print(f'[{ts}] ⚠️ 检测到 {len(changes)} 处变化:')
            for c in changes:
                flag = '🔴' if c['level'] == 'warn' else 'ℹ️'
                print(f"   {flag} {c['model']:28} {c['type']:14} credits={c['credits']}")
            if warn:
                print('\n   ⚠️ 模型条目属性发生变化 —— 请检查客户端配置是否需调整')

    return 1 if warn else 0


if __name__ == '__main__':
    sys.exit(main())
