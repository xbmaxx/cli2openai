#!/usr/bin/env python3
"""解析器自检 —— 独立文件,避免 shell 转义地狱。

用法: python3 selftest_parser.py    (全过退出码 0,否则 1)
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli2openai as wb

VALID = {'terminal', 'read_file', 'search_files'}

CASES = [
    # (名称, 输入, 期望调用数, 期望首个工具名)
    ('裸JSON单调用', '{"name": "terminal", "arguments": {"command": "ls /tmp"}}', 1, 'terminal'),
    ('裸JSON批量', '{"tool_calls": [{"name":"terminal","arguments":{"command":"ls"}},'
                  '{"name":"read_file","arguments":{"path":"/etc/hosts"}}]}', 2, 'terminal'),
    ('带说明文字', '我先看看。\n{"name": "terminal", "arguments": {"command": "pwd"}}', 1, 'terminal'),
    ('代码块包裹', '```json\n{"name": "terminal", "arguments": {"command": "pwd"}}\n```', 1, 'terminal'),
    ('OpenAI原生形', '{"function": {"name": "terminal", "arguments": "{\\"command\\": \\"ls\\"}"}}', 1, 'terminal'),
    ('嵌套花括号参数', '{"name": "terminal", "arguments": {"command": "echo {\\"a\\": 1}"}}', 1, 'terminal'),
    ('DSML标记兜底', '<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name="terminal">'
                    '<｜｜DSML｜｜ parameter name="command">ls</｜｜DSML｜｜ parameter>'
                    '</｜｜DSML｜｜ invoke></｜｜DSML｜｜ calls>', 1, 'terminal'),
    ('混合标记兜底', '说明 <｜｜DSML｜｜ invoke name="terminal">'
                    '<parameter name="command">ls</parameter></invoke>', 1, 'terminal'),
    ('纯文本不误判', '你好,这是普通回复,没有任何工具调用。', 0, None),
    ('白名单拒编造', '{"name": "evil_tool", "arguments": {}}', 0, None),
    ('空输入', '', 0, None),
    ('截断JSON不崩', '{"name": "terminal", "arguments": {"comm', 0, None),
]


def main():
    passed = failed = 0
    for name, text, want_n, want_first in CASES:
        try:
            calls, _ = wb.parse_tool_calls(text, VALID, None)
            n = len(calls or [])
            first = calls[0]['function']['name'] if calls else None
            ok = (n == want_n) and (first == want_first)
        except Exception as e:
            ok = False
            print(f'  ❌ {name:16} 抛异常: {type(e).__name__}: {e}')
            failed += 1
            continue
        if ok:
            passed += 1
            print(f'  ✅ {name:16} {n} 个调用' + (f' → {first}' if first else ''))
        else:
            failed += 1
            print(f'  ❌ {name:16} 期望 {want_n}个/{want_first},实际 {n}个/{first}')

    print(f'\n  解析器自检: 通过 {passed}  失败 {failed}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())

# ─────────────────────────────────────────────────────────────
# v1.2.2 新增:裸参数对象推断(模型忘写 name 包装的兜底)
# ─────────────────────────────────────────────────────────────
def test_bare_args_inference():
    specs = wb._build_tool_specs([
        {'function': {'name': 'terminal', 'parameters': {'type': 'object',
            'properties': {'command': {'type': 'string'}, 'timeout': {'type': 'number'}},
            'required': ['command']}}},
        {'function': {'name': 'read_file', 'parameters': {'type': 'object',
            'properties': {'path': {'type': 'string'}}, 'required': ['path']}}},
        {'function': {'name': 'write_file', 'parameters': {'type': 'object',
            'properties': {'path': {'type': 'string'}, 'content': {'type': 'string'}},
            'required': ['path', 'content']}}},
    ])
    valid = {'terminal', 'read_file', 'write_file'}

    cases = [
        # (描述, 输入文本, 期望工具名 or None)
        ('裸参数 command(真实 case)', '{"command":"ls -la","timeout":60}', 'terminal'),
        ('裸参数 path', '{"path":"/tmp/x.txt"}', 'read_file'),
        ('裸参数 path+content', '{"path":"/tmp/x","content":"hi"}', 'write_file'),
        ('未知键无法推断', '{"foo":"bar"}', None),
        ('文本+裸参数混合', '先说一句。\n\n{"command":"pwd"}', 'terminal'),
        ('标准形不受影响', '{"name":"terminal","arguments":{"command":"ls"}}', 'terminal'),
        ('XML 形态 tool name=X', '<tool calls>\n<tool name="terminal">\n<command>ls -la</command>\n</tool>', 'terminal'),
        ('XML 形态多参数', '<tool calls>\n<tool name="write_file">\n<path>/tmp/a</path>\n<content>hi</content>\n</tool>', 'write_file'),
        ('裸工具名标签', '<terminal>\nls -la\n</terminal>', 'terminal'),
        ('多工具包装', '{"tool_calls":[{"name":"terminal","arguments":{"command":"ls"}}]}', 'terminal'),
    ]
    passed = 0
    for label, text, want in cases:
        calls, _ = wb.parse_tool_calls(text, valid, None, specs)
        got = calls[0]['function']['name'] if calls else None
        ok = (got == want)
        passed += ok
        print(f'  {"✅" if ok else "❌"} {label:26} → {got} (期望 {want})')
    return passed, len(cases)


if __name__ == '__main__' and '--bare' in sys.argv:
    print('裸参数推断自检:')
    p, t = test_bare_args_inference()
    print(f'  → {p}/{t} 通过')
