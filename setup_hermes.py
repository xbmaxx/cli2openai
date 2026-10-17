#!/usr/bin/env python3
"""
Hermes 配置自动注入 —— 把 cli2openai provider 写进 ~/.hermes/config.yaml

特点:
  - 跨平台 (macOS / Windows / Linux)
  - 文本插入,不用 yaml.dump(避免丢注释/格式)
  - 写前自动备份 + 写后 YAML 校验(校验失败自动回滚)
  - 幂等:已存在则跳过

用法:
  python3 setup_hermes.py              # 注入
  python3 setup_hermes.py --dry-run    # 只看会做什么
  python3 setup_hermes.py --port 8790  # 指定 Bridge 端口
"""
import os, sys, shutil, time, argparse

PROVIDER_NAME = 'cli2openai'


def find_config():
    """定位 Hermes config.yaml"""
    env = os.environ.get('HERMES_CONFIG')
    if env and os.path.exists(env):
        return env
    for p in [
        os.path.expanduser('~/.hermes/config.yaml'),
        os.path.expanduser('~/AppData/Roaming/hermes/config.yaml'),   # Windows
        os.path.expanduser('~/.config/hermes/config.yaml'),
    ]:
        if os.path.exists(p):
            return p
    return None


def build_block(port):
    return (
        f"  - name: {PROVIDER_NAME}\n"
        f"    base_url: http://127.0.0.1:{port}/v1\n"
        f"    api_key: wb-local-bridge\n"
        f"    model: deepseek-v4.1-flash\n"
        f"    api_mode: chat_completions\n"
        f"    models:\n"
        f"      - deepseek-v4.1-flash\n"
        f"      - hy3\n"
    )


def insert_into_existing_section(src, block):
    """在 custom_providers: 段末尾插入。返回新文本,失败返回 None"""
    lines = src.split('\n')
    start = None
    for i, l in enumerate(lines):
        if l.rstrip() == 'custom_providers:' or l.startswith('custom_providers:'):
            start = i
            break
    if start is None:
        return None
    # 找段结束:下一个顶层键
    end = len(lines)
    for j in range(start + 1, len(lines)):
        l = lines[j]
        if l and not l[0].isspace() and not l.startswith('#'):
            end = j
            break
    # 去掉段尾的空行,保证紧贴
    while end > start + 1 and lines[end - 1].strip() == '':
        end -= 1
    new_lines = lines[:end] + block.rstrip('\n').split('\n') + lines[end:]
    return '\n'.join(new_lines)


def append_new_section(src, block):
    """没有 custom_providers 段时,在文件末尾新建"""
    sep = '' if src.endswith('\n') else '\n'
    return src + sep + '\ncustom_providers:\n' + block


def validate(text):
    """校验注入结果。
    有 PyYAML 时做完整结构校验；没有则降级为文本级检查（够用，因为是纯文本插入）。
    返回 (ok: bool, info: dict|str)
    """
    try:
        import yaml
    except ImportError:
        # 降级：文本级检查 —— 确认条目在 custom_providers 段内
        in_section = False
        for line in text.split('\n'):
            if line.startswith('custom_providers:'):
                in_section = True
                continue
            if in_section and line and not line[0].isspace() and not line.startswith('#'):
                break                      # 出了段
            if in_section and line.strip().startswith('- name: ' + PROVIDER_NAME):
                return True, {'name': PROVIDER_NAME + ' (文本级校验，未装 PyYAML)'}
        return False, '未在 custom_providers 段内找到 cli2openai 条目'

    try:
        d = yaml.safe_load(text)
        for p in (d or {}).get('custom_providers', []) or []:
            if isinstance(p, dict) and p.get('name') == PROVIDER_NAME:
                return True, p
        return False, '注入后找不到 cli2openai 条目'
    except Exception as e:
        return False, f'YAML 解析失败: {e}'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8790, help='Bridge 端口 (默认 8790)')
    ap.add_argument('--dry-run', action='store_true', help='只预览,不写入')
    ap.add_argument('--config', help='指定 config.yaml 路径')
    args = ap.parse_args()

    print('════════ Hermes 配置注入 ════════')
    cfg = args.config or find_config()
    if not cfg:
        print('❌ 未找到 Hermes config.yaml')
        print('   预期位置: ~/.hermes/config.yaml')
        print('   可用 --config 指定路径')
        return 1
    print(f'配置文件: {cfg}')

    src = open(cfg, encoding='utf-8').read()

    # 幂等检查
    if PROVIDER_NAME in src:
        print(f'✅ {PROVIDER_NAME} 已存在,无需重复注入')
        ok, info = validate(src)
        if info and isinstance(info, dict):
            print(f'   当前配置: {info.get("base_url")} → {info.get("models")}')
        return 0

    block = build_block(args.port)

    # 插入
    new_src = insert_into_existing_section(src, block)
    where = 'custom_providers 段'
    if new_src is None:
        new_src = append_new_section(src, block)
        where = '文件末尾(新建 custom_providers 段)'
    print(f'插入位置: {where}')

    # 校验
    ok, info = validate(new_src)
    if not ok:
        print(f'❌ 校验失败:{info}')
        return 1
    print('✅ 校验通过')
    if isinstance(info, dict) and 'PyYAML' in str(info.get('name', '')):
        print('   (未装 PyYAML,使用文本级校验 —— 不影响功能)')

    if args.dry_run:
        print('\n[dry-run] 将要写入的内容:')
        print('─' * 50)
        print(block.rstrip())
        print('─' * 50)
        print('\n(未实际写入。去掉 --dry-run 执行)')
        return 0

    # 备份 + 写入
    bak = f'{cfg}.bak.{time.strftime("%Y%m%d-%H%M%S")}'
    shutil.copy2(cfg, bak)
    print(f'已备份: {os.path.basename(bak)}')

    with open(cfg, 'w', encoding='utf-8') as f:
        f.write(new_src)

    # 复核
    ok2, info2 = validate(open(cfg, encoding='utf-8').read())
    if ok2:
        print('✅ 写入成功并复核通过')
        print(f'   provider: {info2.get("name")}')
        print(f'   base_url: {info2.get("base_url")}')
        print(f'   models  : {info2.get("models")}')
        print()
        print('下一步: 在 Hermes 对话里选择模型 →')
        print(f'   {PROVIDER_NAME}: deepseek-v4.1-flash')
        return 0
    else:
        # 回滚
        shutil.copy2(bak, cfg)
        print('❌ 写入后复核失败,已自动回滚')
        return 1


if __name__ == '__main__':
    sys.exit(main())
