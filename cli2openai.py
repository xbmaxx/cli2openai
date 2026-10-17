#!/usr/bin/env python3
"""
cli2openai —— 把 codebuddy CLI 暴露成 OpenAI 兼容 API

解决的问题:
  codebuddy CLI 只说自己的协议，普通 OpenAI 客户端（Hermes / Open WebUI / Continue /
  LiteLLM / 自研脚本）无法直接驱动它。本桥接层负责双向协议转换。

设计要点:
  1. 模型范围守卫 —— 只暴露当前会话可直接调用的模型，范围外的请求返回 403
  2. 路由型条目（default-model）被排除 —— 它不是确定的模型，行为不可复现
  3. 清单可热刷新 —— CLI 目录变化时自动更新

用法:
  python3 cli2openai.py                 # 启动服务 (默认 :8790)
  python3 cli2openai.py --refresh       # 只刷新模型清单
  python3 cli2openai.py --port 8790
"""
import json, os, subprocess, sys, re, threading, time, shutil, tempfile
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler

CONFIG_DIR  = os.path.expanduser("~/.codebuddy")
CACHE_FILE  = os.path.expanduser("~/.cli2openai/models.json")
CALL_TIMEOUT = 180          # 单次调用超时(秒)
REFRESH_HOURS = 6           # 模型清单刷新间隔
IS_WIN = (os.name == 'nt')


def _heal_codebuddy_link():
    """自愈:npm 自动更新中断会导致 ~/.local/bin/codebuddy 符号链接丢失
    (更新流程"创建临时链接→删旧→改名"三步只做完前两步)。
    若包目录存在而链接缺失/断链 → 重建。

    背景案例:2026-10-17 00:24 CLI 自动更新 2.168.0→2.168.2 时链接丢失,
    Bridge 全部请求报"找不到 codebuddy",用户侧显示"模型服务器错误"。
    """
    if IS_WIN:
        return
    try:
        home = os.path.expanduser('~')
        link = os.path.join(home, '.local', 'bin', 'codebuddy')
        target_abs = os.path.join(home, '.local', 'lib', 'node_modules',
                                  '@tencent-ai', 'codebuddy-code', 'bin', 'codebuddy')
        if not os.path.exists(target_abs):
            return                              # 包本身不在,无从修起
        if os.path.lexists(link) and os.path.exists(link):
            return                              # 链接健康
        if os.path.lexists(link):
            os.remove(link)                     # 断链:先清掉
        os.symlink(os.path.relpath(target_abs, os.path.dirname(link)), link)
        print(f'[bridge] [自愈] 已重建 codebuddy 链接: {link}',
              file=sys.stderr, flush=True)
    except Exception as e:
        print(f'[bridge] [自愈] 重建链接失败: {e}', file=sys.stderr, flush=True)


def _cli_path():
    """返回当前可用的 CLI 路径(带自愈)。每次调用 CLI 前都应经过这里。"""
    global CLI
    if CLI and os.path.exists(CLI):
        return CLI
    _heal_codebuddy_link()
    CLI = find_codebuddy()
    return CLI


def find_codebuddy():
    """跨平台探测 codebuddy CLI 可执行文件"""
    _heal_codebuddy_link()                      # 先自愈(防自动更新断链)
    # 显式环境变量优先
    env_cli = os.environ.get('CODEBUDDY_CLI')
    if env_cli and os.path.exists(env_cli):
        return env_cli
    candidates = []
    if IS_WIN:
        home = os.path.expanduser('~')
        appdata = os.environ.get('APPDATA', '')
        candidates = [
            os.path.join(home, '.local', 'bin', 'codebuddy.cmd'),
            os.path.join(home, '.local', 'bin', 'codebuddy.exe'),
            os.path.join(appdata, 'npm', 'codebuddy.cmd'),
            os.path.join(home, 'AppData', 'Roaming', 'npm', 'codebuddy.cmd'),
            os.path.join(home, '.codebuddy', 'bin', 'codebuddy.exe'),
        ]
    else:
        home = os.path.expanduser('~')
        candidates = [
            os.path.join(home, '.local', 'bin', 'codebuddy'),
            '/opt/homebrew/bin/codebuddy',
            '/usr/local/bin/codebuddy',
            '/usr/bin/codebuddy',
        ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    # 最后尝试 PATH
    found = shutil.which('codebuddy')
    return found or candidates[0]


CLI = find_codebuddy()

# 输出协议模式:
#   text       (默认) 文本标记协议 —— 模型输出 JSON/DSML/XML 等标记,由解析器还原
#   structured 结构化输出 —— 用 --json-schema + StructuredOutput 工具,结果落在
#              structured_output 字段,不存在标记形态漂移
MODE = (os.environ.get('WB_MODE') or 'text').strip().lower()

# ── 兜底白名单(启动且无法联网时使用)────────────────────────
# 顺序即优先级:工具调用更稳定的排前面
FALLBACK_MODELS = ["deepseek-v4.1-flash", "hy3"]
# 永远排除:default-model 是 Auto,可能路由到收费模型
NEVER_ALLOW = {"default-model"}
# 排序偏好(未列出的排最后)
PREFERRED_ORDER = ["deepseek-v4.1-flash", "hy3"]

_lock = threading.Lock()
_allowed_models = list(FALLBACK_MODELS)


# ── 模型清单:通过 ACP 从 CLI 自身读取 credits ─────────────────
def fetch_models(port=7890, password=None):
    """从本地 --serve 实例的 ACP 端点拉取可用模型及 credits。
    需要已有 serve 在跑;没有则回退到缓存/兜底。"""
    import urllib.request, urllib.error
    if not password:
        password = _read_password()
    if not password:
        return None
    base = f"http://127.0.0.1:{port}"
    hdr = {'Authorization': f'Bearer {password}', 'Content-Type': 'application/json',
           'X-CodeBuddy-Request': '1'}
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            base + '/api/v1/acp/connect', headers=hdr,
            data=b'{}', method='POST'), timeout=15)
        conn = json.loads(r.read())
        ah = dict(hdr); ah.update({'Accept': 'application/json, text/event-stream',
                                   'acp-connection-id': conn['connectionId'],
                                   'acp-session-token': conn['sessionToken']})
        for body in ({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": 1, "clientCapabilities": {}}},
                     {"jsonrpc": "2.0", "id": 2, "method": "session/new",
                      "params": {"cwd": "/tmp", "mcpServers": []}}):
            req = urllib.request.Request(base + '/api/v1/acp',
                                         headers=ah, data=json.dumps(body).encode(), method='POST')
            raw = urllib.request.urlopen(req, timeout=25).read().decode('utf-8', 'replace')
            for line in raw.split('\n'):
                if not line.startswith('data: '):
                    continue
                try:
                    d = json.loads(line[6:])
                except Exception:
                    continue
                models = (d.get('result') or {}).get('models', {}).get('availableModels')
                if models:
                    return models
    except Exception as e:
        print(f'[bridge] 拉取模型清单失败: {type(e).__name__}: {e}')
    return None


def _read_password():
    """从 ~/.codebuddy/settings.json 或环境变量读取 gateway 密码
    实际结构: {"gateway": {"auth": "password", "password": "..."}}"""
    p = os.environ.get('CODEBUDDY_GATEWAY_PASSWORD')
    if p:
        return p
    try:
        cfg = json.load(open(os.path.join(CONFIG_DIR, 'settings.json')))
    except Exception:
        return None
    # 先查嵌套的 gateway 段,再查顶层(兼容不同版本)
    for src in (cfg.get('gateway') or {}, cfg):
        if not isinstance(src, dict):
            continue
        for k in ('password', 'gatewayPassword', 'gateway_password'):
            if src.get(k):
                return src[k]
    return None


def select_included_models(models):
    """从模型清单里筛出当前会话可直接调用的模型（不触发额外计费：credits 为 0 / 空，且非路由型）"""
    free = []
    for m in models:
        mid = m.get('modelId') or m.get('id')
        if not mid or mid in NEVER_ALLOW:
            continue
        cred = ((m.get('_meta') or {}).get('credits') or '').strip()
        if cred in ('x0.00', 'x0', '0.00', '') or cred.startswith('x0.00'):
            free.append(mid)
    # 按偏好排序:工具调用稳定的排前面(verify 也取第一个)
    free.sort(key=lambda m: PREFERRED_ORDER.index(m) if m in PREFERRED_ORDER else 999)
    return free or list(FALLBACK_MODELS)


def refresh_models(verbose=True):
    """刷新可用模型清单并落盘。返回 (free_list, changed)"""
    global _allowed_models
    models = fetch_models()
    if not models:
        if verbose:
            print('[bridge] 未拉到模型清单，沿用缓存/兜底')
        return _allowed_models, False
    free = select_included_models(models)
    with _lock:
        changed = (free != _allowed_models)
        _allowed_models = free
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    json.dump({'allowed': free, 'all': models, 'updatedAt': int(time.time())},
              open(CACHE_FILE, 'w'), ensure_ascii=False, indent=1)
    if changed and verbose:
        print(f'[bridge] ⚠️ 可用模型清单变化 → {free}')
    return free, changed


def load_cache():
    global _allowed_models
    try:
        d = json.load(open(CACHE_FILE))
        allowed = d.get('allowed') or d.get('free')   # 兼容旧缓存字段
        if allowed:
            _allowed_models = [m for m in allowed if m not in NEVER_ALLOW]
            return True
    except Exception:
        pass
    return False


def extract_json_objects(text):
    """用平衡括号状态机提取文本中所有完整的 JSON 对象。

    比正则健壮得多（借鉴 claude-openai-proxy 的 _extract_json_object）：
    正确处理字符串内的引号转义、括号、换行；解析失败的起点会从下一个 '{' 重试。
    返回 [(obj, start, end), ...]
    """
    results = []
    i, n = 0, len(text)
    while i < n:
        start = text.find('{', i)
        if start < 0:
            break
        depth, in_str, esc = 0, False, False
        closed_at = -1
        for j in range(start, n):
            c = text[j]
            if esc:
                esc = False
            elif c == '\\':
                esc = True
            elif in_str:
                if c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    closed_at = j
                    break
        if closed_at < 0:
            break                      # 未闭合 → 可能被截断，放弃
        cand = text[start:closed_at + 1]
        try:
            obj = json.loads(cand)
            results.append((obj, start, closed_at + 1))
            i = closed_at + 1
        except Exception:
            i = start + 1              # 解析失败 → 从下一个 { 重试
    return results


def _build_tool_specs(tools):
    """从 OpenAI tools 定义构建推断所需的精简规格(供裸参数对象兜底)。

    返回 [{'name': n, 'props': {参数名...}, 'required': {必填名...}}, ...]
    """
    specs = []
    for t in tools or []:
        fn = (t or {}).get('function') or {}
        nm = fn.get('name')
        if not nm:
            continue
        params = fn.get('parameters') or {}
        props = set((params.get('properties') or {}).keys())
        req = set(params.get('required') or [])
        specs.append({'name': nm, 'props': props, 'required': req})
    return specs


def _infer_name_from_args(obj, tool_specs):
    """裸参数对象 → 推断工具名。

    场景:模型输出了 {"command": "ls", "timeout": 60} 而忘写 name 包装。
    判据:① 必填参数必须全被覆盖 ② 对象键至少一半被工具认识 ③ 唯一胜出才采纳(歧义则拒绝)
    """
    if not isinstance(obj, dict) or not tool_specs:
        return None
    keys = {k for k in obj.keys() if isinstance(k, str) and not k.startswith('_')}
    if not keys:
        return None
    scored = []
    for sp in tool_specs:
        props, req = sp['props'], sp['required']
        if not props:
            continue
        if req and not req <= keys:           # 必填参数没覆盖齐 → 不是它
            continue
        inter = keys & props
        if not inter:
            continue
        ratio = len(inter) / len(keys)        # 键被认识的比例(容忍 timeout 之类附带键)
        if ratio < 0.5:
            continue
        scored.append((round(ratio, 3), len(req), -len(props), sp['name']))
    if not scored:
        return None
    scored.sort(reverse=True)
    if len(scored) > 1 and scored[0][:2] == scored[1][:2]:
        return None                            # 并列歧义 → 宁可不猜
    return scored[0][3]


def _normalize_tool_call(obj, valid_names=None, forced=None, tool_specs=None):
    """把各种 JSON 形态归一成标准 tool_call dict。不合规返回 None。

    支持的形态：
      A. {"name": "X", "arguments": {...}}            ← 契约要求的标准形
      B. {"function": {"name": "X", "arguments": ...}} ← OpenAI 原生形
      C. 裸参数对象（先看 tool_choice 强制，再用参数名推断）← 宽容兜底
    """
    if not isinstance(obj, dict):
        return None
    fn = obj.get('function') if isinstance(obj.get('function'), dict) else {}
    name = obj.get('name') or obj.get('tool') or obj.get('tool_name') or fn.get('name')
    args = obj.get('arguments', obj.get('parameters', fn.get('arguments')))

    if not name and forced:
        name, args = forced, obj      # 形态 C（强制指定优先）
    if not name:
        name = _infer_name_from_args(obj, tool_specs)   # 形态 C（参数名推断）
        if name:
            args = obj

    if not name or not isinstance(name, str):
        return None
    name = name.strip()
    if valid_names and name not in valid_names:
        return None                   # ★ 白名单：编造的工具名直接拒

    if args is None:
        args = {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            args = {'_raw': args}     # 保住原文不丢数据
    if not isinstance(args, (dict, list)):
        args = {'value': args}

    return {'name': name, 'arguments': json.dumps(args, ensure_ascii=False)}


def _mk_call(name, args_json, idx):
    base = int(time.time() * 1000)
    return {'id': f'call_wb{base}_{idx}', 'type': 'function',
            'function': {'name': name, 'arguments': args_json}}


# ── 工具调用标记解析(兜底路径)─────────────────────────
# 模型有时不遵守 JSON 契约,仍输出各家私有标记。已观测到 4 种变体:
#   ① 标准 DSML:  <｜｜DSML｜｜ calls> … <｜｜DSML｜｜ invoke name="X"> … </｜｜DSML｜｜ calls>
#   ② 全简写:      <toolinvoke name="X"> … <parameter name="Y">V</parameter> … </toolinvoke>
#   ③ 混合(长上下文常见):  <｜｜DSML｜｜ invoke name="X"> 里套 <parameter> 和 </invoke>
#   ④ 未知哈希:    <tool_calls:abc123> …          ← 无法解析,只记录
_DSML_OPEN  = re.compile(r'<｜｜DSML｜｜\s*')
_DSML_CLOSE = re.compile(r'</｜｜DSML｜｜\s*')
_WEIRD_CALLS = re.compile(r'<tool_calls?:', re.I)

_CALLS_RE  = re.compile(r'<calls>(.*?)</calls>', re.S)
_INVOKE_RE = re.compile(r'<(?:toolinvoke|invoke)\s+name="([^"]+)"\s*>(.*?)</(?:toolinvoke|invoke)>', re.S)
_PARAM_RE  = re.compile(r'<parameter\s+name="([^"]+)"[^>]*>(.*?)</parameter>', re.S)

malformed_seen = []          # 解析失败的样本(供诊断)

# 流式过滤用:任意位置出现以下模式即判定"进入结构化输出",停发后续正文。
# （模型常先说一句"我先看看…"再输出 JSON/标记，所以必须全文扫描）
_MARKER_RE = re.compile(
    r'\{"(?:name|tool_calls)"\s*:'      # JSON 契约: {"name": / {"tool_calls":
    r'|<｜｜DSML'                        # 标准 DSML 标记
    r'|<toolinvoke\b'                   # 简写标记
    r'|<tool_calls?[:>]'                # 哈希变体 <tool_calls:xxx>
    r'|<tool\s+calls?\b'                # XML 风格: <tool calls> / <tool call>
    r'|<tool\s+name\s*=',               # XML 风格: <tool name="X">
    re.I)

# 标记的完整形态(用于"尾部疑似前缀"检测 —— 避免把 {"name 这种半个标记发出去)
_FULL_MARKERS = ('{"name":', '{"tool_calls":', '<｜｜DSML', '<toolinvoke',
                 '<tool_calls:', '<tool_call>', '<tool_calls>',
                 '<tool calls>', '<tool name=')


def _partial_marker_tail(s):
    """若 s 的尾部恰好是某个标记的前缀(可能跨块断开),返回该前缀长度,否则 0。"""
    best = 0
    for mk in _FULL_MARKERS:
        for k in range(len(mk) - 1, 0, -1):
            if s.endswith(mk[:k]):
                if k > best:
                    best = k
                break
    return best


def _clean_marker_fragments(text):
    """清理残留在正文尾部的标记碎片。

    典型:模型输出了被截断的裸参数开头(如 `{"code` 或 `{"command":` 半截),
    不在任何 JSON span 内 → 残留为正文 → 显示成"乱字符串"。
    判据:尾部 200 字符内有未闭合的 `{`,且该片段无换行、长度短 → 砍掉。
    """
    if not text:
        return text
    t = text.rstrip()
    if t.endswith('}'):
        return t                       # 已闭合,可能是正常 JSON,不碰
    idx = t.rfind('{')
    if idx < 0 or len(t) - idx > 200:
        return t
    frag = t[idx:]
    if '}' in frag:
        return t                       # 有闭合括号,不是碎片
    if '\n' in frag or '\r' in frag:
        return t                       # 多行 → 可能是正文段落开头,不碰
    if len(frag) > 60:
        return t                       # 太长,保守不碰
    return t[:idx].rstrip()


def _normalize_tags(text):
    """把所有 DSML 变体归一化为简单标签:<｜｜DSML｜｜X> → <X>,</｜｜DSML｜｜X> → </X>"""
    t = _DSML_OPEN.sub('<', text)
    t = _DSML_CLOSE.sub('</', t)
    return t


# ── XML 风格工具调用(实测第 8 种形态)────────────────────────
#   <tool calls>
#   <tool name="execute_code">
#   <code>import re ...</code>
#   </tool>
# 归一成 DSML 形态,复用已有解析器。
_TOOL_CALLS_XML = re.compile(r'<tool\s+calls?\s*>', re.I)
_TOOL_XML       = re.compile(r'<tool\s+name\s*=\s*["\']([^"\']+)["\']\s*>(.*?)</tool\s*>',
                             re.S | re.I)
_XML_PARAM      = re.compile(r'<([a-zA-Z_][\w-]*)\s*>(.*?)</\1\s*>', re.S)
_XML_INNER_SKIP = {'tool', 'parameter', 'invoke', 'calls', 'toolinvoke'}


def _normalize_xml_tools(text):
    """把 XML 风格调用块归一成 DSML 形态(只处理 <tool name=...> 包裹的块)。"""
    t = _TOOL_CALLS_XML.sub('<calls>', text)

    def conv(m):
        name, body = m.group(1), m.group(2)
        params = []
        for pm in _XML_PARAM.finditer(body):
            pname, pval = pm.group(1), pm.group(2)
            if pname.lower() in _XML_INNER_SKIP:
                continue
            params.append(f'<parameter name="{pname}">{pval}</parameter>')
        inner = ''.join(params) if params else body
        return f'<invoke name="{name}">{inner}</invoke>'

    return _TOOL_XML.sub(conv, t)


def _normalize_bare_tool_tags(text, tool_specs):
    """第 9 形态:直接用工具名当标签,没有 <tool name=...> 包裹。

        <execute_code>
        from hermes_tools import terminal
        ...
        </execute_code>

    工具名是本轮动态的,只能用 tool_specs 现算。
    单参数工具(required 只有一个)→ 整段内容就是该参数值;
    内部带 <param>...</param> 的 → 按标签解析。
    """
    if not text or not tool_specs:
        return text
    names = [sp['name'] for sp in tool_specs if sp.get('name')]
    if not names:
        return text
    alt = '|'.join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    pat = re.compile(rf'<({alt})\s*>(.*?)</\1\s*>', re.S | re.I)
    req_map = {sp['name']: list(sp.get('required') or []) for sp in tool_specs}

    def conv(m):
        name, body = m.group(1), m.group(2)
        params = []
        for pm in _XML_PARAM.finditer(body):
            pn, pv = pm.group(1), pm.group(2)
            if pn.lower() in _XML_INNER_SKIP:
                continue
            params.append(f'<parameter name="{pn}">{pv}</parameter>')
        if params:
            inner = ''.join(params)
        else:
            req = req_map.get(name) or []
            pname = req[0] if len(req) == 1 else 'input'
            inner = f'<parameter name="{pname}">{body}</parameter>'
        return f'<invoke name="{name}">{inner}</invoke>'

    return pat.sub(conv, text)


def _parse_markup_calls(text, valid_names, forced):
    """标记语言兜底解析(DSML / toolinvoke)。返回 (calls, remaining) 或 (None, text)"""
    norm = _normalize_tags(text)
    m = _CALLS_RE.search(norm)
    scope = m.group(1) if m else norm

    calls = []
    for i, inv in enumerate(_INVOKE_RE.finditer(scope)):
        name = inv.group(1).strip()
        if valid_names and name not in valid_names:
            continue
        args = {p.group(1).strip(): p.group(2).strip()
                for p in _PARAM_RE.finditer(inv.group(2))}
        calls.append(_mk_call(name, json.dumps(args, ensure_ascii=False), i))
    if not calls:
        if _WEIRD_CALLS.search(text) or '<invoke' in norm or 'DSML' in text:
            malformed_seen.append(text[:400])
            if len(malformed_seen) > 20:
                del malformed_seen[:-20]
        return None, text
    if m:
        remaining = _CALLS_RE.sub('', norm).strip()
    else:
        remaining = _INVOKE_RE.sub('', norm).strip()
        remaining = remaining.replace('<calls>', '').replace('</calls>', '').strip()
    return calls, remaining


def parse_tool_calls(text, valid_names=None, forced=None, tool_specs=None):
    """从模型输出里提取工具调用。

    主路径:裸 JSON(契约要求的格式,稳定)
    兜底:标记语言(DSML/toolinvoke,模型不守规矩时)

    返回 (tool_calls|None, remaining_text)
    """
    if not text:
        return None, text

    # ── 主路径:平衡括号提取 JSON ──
    calls, spans = [], []
    for obj, s, e in extract_json_objects(text):
        if isinstance(obj, dict) and isinstance(obj.get('tool_calls'), list):
            for item in obj['tool_calls']:
                c = _normalize_tool_call(item, valid_names, forced, tool_specs)
                if c:
                    calls.append(_mk_call(c['name'], c['arguments'], len(calls)))
            spans.append((s, e))
            continue
        c = _normalize_tool_call(obj, valid_names, forced, tool_specs)
        if c:
            calls.append(_mk_call(c['name'], c['arguments'], len(calls)))
            spans.append((s, e))

    if calls:
        # 去掉已消费的 JSON 片段,保留其余文本
        keep, last = [], 0
        for s, e in spans:
            keep.append(text[last:s])
            last = e
        keep.append(text[last:])
        return calls, _clean_marker_fragments(''.join(keep).strip())

    # ── 兜底:标记语言(先归一化 XML 风格 <tool name="X">,再走 DSML 解析)──
    low = text.lower()
    if '<tool name' in low or '<tool calls' in low or '<tool call>' in low:
        text = _normalize_xml_tools(text)
    # 第 9 形态:裸工具名标签(工具名本轮动态)
    text = _normalize_bare_tool_tags(text, tool_specs)
    calls2, remaining2 = _parse_markup_calls(text, valid_names, forced)
    return calls2, _clean_marker_fragments(remaining2)


def build_system_prompt(original_system, tools, tool_choice=None):
    """把 Hermes 的 system + 工具定义 拼成给模型的 system prompt。

    关键设计（借鉴 claude-openai-proxy）：
      不再要求模型输出各家私有的文本标记（DSML/toolinvoke…），
      而是契约化为**裸 JSON** —— JSON 是训练语料里最普遍的格式，
      模型遵循度远高于厂商私有标记，且是我们自己定的契约、不会随上游变。
    """
    parts = []
    if original_system:
        parts.append(original_system)
    if tools:
        lines = ['\n# 可用工具（Available Tools）\n']
        for t in tools:
            fn = (t or {}).get('function') or {}
            nm, desc = fn.get('name'), fn.get('description', '')
            params = fn.get('parameters') or {}
            props = params.get('properties') or {}
            req = params.get('required') or []
            lines.append(f'## {nm}')
            if desc:
                lines.append(desc)
            if props:
                lines.append('参数：')
                for pn, pv in props.items():
                    mark = '（必填）' if pn in req else ''
                    lines.append(f'  - {pn} ({pv.get("type","string")}){mark}: {pv.get("description","")}')
            lines.append('')
        parts.append('\n'.join(lines))

        # ── 取数纪律:只改"怎么取",不改任何限额/截断(避免损害结果质量) ──
        parts.append(
            '# 取数纪律（省时间，务必遵守）\n'
            '- **先探规模再取内容**：读文件前先 `wc -l` / `ls -la`，再决定取哪一段。\n'
            '- **大文件不整读**：用 `sed -n \'1,60p\'`、`grep -n 关键词`、`head -50`；'
            '不要 `cat` 整个文件，也不要整篇读 HTML/JSON。\n'
            '- **列表类加限制**：`ls | head -30`、`grep -c` 先数再取，不要全量输出。\n'
            '- **同一文件不重复读**：除非内容被改过，或上次读取失败/被截断。\n'
            '- **一条命令解决一个问题**：输出越短越好，需要更多内容时再按需追加。\n'
        )

        # ── 输出契约:按 tool_choice 调整语气强度 ──
        forced_name = None
        if isinstance(tool_choice, dict):
            forced_name = ((tool_choice.get('function') or {}).get('name')
                           or tool_choice.get('name'))
        strength = 'required' if tool_choice == 'required' or forced_name else 'auto'

        header = '# 工具调用输出格式（必须严格遵守）\n'
        if strength == 'required':
            header += ('**你必须调用工具,不允许只回复文字。**\n')
        if forced_name:
            header += f'**本轮你必须且只能调用 `{forced_name}`。**\n'

        # 反例:用本轮真实工具名(更有针对性)
        ex_names = [sp_name for sp_name in
                    [(t.get('function') or {}).get('name') for t in tools] if sp_name][:2]
        ex1 = ex_names[0] if ex_names else 'terminal'
        ex_err_key = 'command' if ex1.lower() in ('terminal', 'bash', 'shell') else '参数名'

        parts.append(
            header + '\n'
            '⚠️ **你没有内置工具。** 不要使用 Write / Bash / Read / Edit / Glob / Grep '
            '等任何内置工具名，也不要输出 `<｜｜DSML｜｜>`、`<toolinvoke>`、`function_calls` '
            '等任何标记格式——这些全部无效，会被丢弃，白白浪费一轮。\n'
            '**你唯一能用的工具就是上面列出的这些，唯一合法的调用格式是下面这个 JSON。**\n\n'
            '当需要调用工具时，**只输出一个 JSON 对象**，不要写任何解释文字，不要用 ``` 代码块包裹：\n\n'
            '{"name": "工具名", "arguments": {"参数名": "参数值"}}\n\n'
            '需要一次调用多个工具时，输出：\n\n'
            '{"tool_calls": [{"name": "工具一", "arguments": {...}}, '
            '{"name": "工具二", "arguments": {...}}]}\n\n'
            '规则：\n'
            '- 工具名必须来自上面列出的工具，不得编造。\n'
            '- arguments 必须是合法 JSON 对象；字符串值内部的换行/引号必须正确转义。\n'
            '- ⚠️ **高频错误，务必避免**：直接输出参数对象而漏掉 name 包装 —— 这种回复会被判为无效：\n'
            f'    ❌ {{"{ex_err_key}": "..."}}                     ← 缺 name，无效！\n'
            f'    ✅ {{"name": "{ex1}", "arguments": {{"{ex_err_key}": "..."}}}}   ← 正确\n'
            '- 不要调用工具时，直接用自然语言回答，**不要**输出 JSON。'
        )
    return '\n'.join(parts)


def _augment_path(env):
    """确保 PATH 里有 node —— codebuddy 的 shebang 是 #!/usr/bin/env node，
    PATH 缺 node 会导致 rc=127 (env: node: No such file or directory)。
    跨平台补充常见安装位置。"""
    import glob
    home = os.path.expanduser('~')
    extra = []
    if IS_WIN:
        appdata = os.environ.get('APPDATA', '')
        extra = [os.path.join(home, '.local', 'bin'),
                 os.path.join(appdata, 'npm'),
                 os.path.join(home, 'AppData', 'Roaming', 'npm'),
                 os.path.join(home, 'AppData', 'Local', 'Programs', 'nodejs')]
    else:
        extra = [os.path.join(home, '.local', 'bin'),
                 '/opt/homebrew/bin', '/usr/local/bin', '/usr/bin', '/bin']
        # nvm / fnm 等版本管理器的 node
        for pat in ('~/.nvm/versions/node/*/bin',
                    '~/.volta/bin',
                    '~/.fnm/aliases/default/bin',
                    '~/.asdf/shims'):
            extra = sorted(glob.glob(os.path.expanduser(pat))) + extra

    cur = env.get('PATH', '')
    parts = [p for p in cur.split(os.pathsep) if p]
    for e in extra:
        if e and os.path.isdir(e) and e not in parts:
            parts.append(e)
    env['PATH'] = os.pathsep.join(parts)
    return env


def build_structured_schema(tool_specs):
    """根据本轮工具集生成结构化输出 schema。

    enum 限定工具名 → 模型无法编造、也无法用别的标记格式表达调用。
    """
    names = [sp['name'] for sp in (tool_specs or []) if sp.get('name')]
    name_schema = {"type": "string", "enum": names} if names else {"type": "string"}
    return {
        "type": "object",
        "properties": {
            "reply": {"type": "string",
                      "description": "给用户的正文回复;本轮若要调用工具,可为空字符串"},
            "tool_calls": {
                "type": "array",
                "description": "本轮要调用的工具;不需要调用时为空数组",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": name_schema,
                        "arguments": {"type": "object", "additionalProperties": True},
                    },
                    "required": ["name", "arguments"],
                },
            },
        },
        "required": ["reply", "tool_calls"],
    }


def call_codebuddy_structured(model, prompt, system=None, tool_specs=None,
                              on_think=None, timeout=None):
    """结构化输出模式:--json-schema + 只放行 StructuredOutput 工具。

    模型不再"发明"标记格式,而是通过 CLI 的 StructuredOutput 工具交付;
    结果落在 result 事件的 structured_output 字段 → 不存在标记形态漂移。

    返回 (reply_text, tool_calls, raw_structured)
    """
    schema = build_structured_schema(tool_specs)
    args = ['-p', '--tools', 'StructuredOutput', '--model', model,
            '--output-format', 'stream-json', '--include-partial-messages',
            '--no-session-persistence',
            '--json-schema', json.dumps(schema, ensure_ascii=False)]
    if system:
        args += ['--system-prompt', system]
    args.append(prompt)

    cli = _cli_path()
    cmd = [cli] + args
    env = dict(os.environ)
    env['CODEBUDDY_CONFIG_DIR'] = CONFIG_DIR
    env = _augment_path(env)
    cwd = tempfile.gettempdir()
    tmo = timeout or CALL_TIMEOUT

    structured, think_total, ev_types = None, 0, {}
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding='utf-8', errors='replace',
                            env=env, cwd=cwd, bufsize=1)
    try:
        deadline = time.time() + tmo
        for line in proc.stdout:
            if time.time() > deadline:
                proc.kill()
                raise RuntimeError(f'codebuddy 结构化模式超时 (>{tmo}s)')
            line = (line or '').strip()
            if not line.startswith('{'):
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            t = d.get('type')
            ev_types[t] = ev_types.get(t, 0) + 1
            if t == 'stream_event':
                ev = d.get('event') or {}
                if ev.get('type') == 'content_block_delta':
                    dl = ev.get('delta') or {}
                    if dl.get('type') == 'thinking_delta':
                        th = dl.get('thinking') or ''
                        if th:
                            think_total += len(th)
                            if on_think:
                                on_think(th)
            elif t == 'result':
                so = d.get('structured_output')
                if isinstance(so, dict):
                    structured = so
    finally:
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        err = ''
        try:
            err = (proc.stderr.read() or '').strip()[:300]
        except Exception:
            pass
        if structured is None:
            print(f'[bridge] [结构化] 未拿到 structured_output '
                  f'(rc={proc.returncode} thinking={think_total} events={ev_types}) '
                  f'{err[:160]}', file=sys.stderr, flush=True)

    if not isinstance(structured, dict):
        return '', [], None

    reply = structured.get('reply') or ''
    if not isinstance(reply, str):
        reply = str(reply)

    calls = []
    raw_calls = structured.get('tool_calls') or []
    if isinstance(raw_calls, list):
        for rc in raw_calls:
            if not isinstance(rc, dict):
                continue
            nm = rc.get('name')
            if not isinstance(nm, str) or not nm.strip():
                continue
            ar = rc.get('arguments')
            if not isinstance(ar, (dict, list)):
                ar = {}
            calls.append({'name': nm.strip(),
                          'arguments': json.dumps(ar, ensure_ascii=False)})

    return reply, calls, structured


def call_codebuddy_stream(model, prompt, system=None, on_text=None, timeout=None,
                          on_think=None):
    """流式调用 CLI(真流式:边生成边回调)。

    用 --output-format stream-json --include-partial-messages 逐行读 ndjson,
    把 text_delta 实时交给 on_text 回调(用于转发给客户端,避免长任务被判超时)。
    thinking_delta 忽略(推理链不需要给用户)。

    返回 (full_text, result_text)
      full_text   —— 累积的正文(text_delta 之和)
      result_text —— <result> 事件里的最终结果(权威,优先用它解析工具调用)
    """
    args = ['-p', '--tools', '', '--model', model,
            '--output-format', 'stream-json', '--include-partial-messages',
            '--no-session-persistence']
    if system:
        args += ['--system-prompt', system]
    args.append(prompt)

    cli = _cli_path()
    if IS_WIN and cli.lower().endswith(('.cmd', '.bat')):
        cmd = ['cmd', '/c', cli] + args
    else:
        cmd = [cli] + args

    env = dict(os.environ); env['CODEBUDDY_CONFIG_DIR'] = CONFIG_DIR
    env = _augment_path(env)
    cwd = tempfile.gettempdir()
    tmo = timeout or CALL_TIMEOUT

    full, result = '', None
    ev_log = []                # 诊断:事件类型轨迹
    raw_tail = []              # 诊断:原始行尾巴
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding='utf-8', errors='replace',
                            env=env, cwd=cwd, bufsize=1)
    try:
        deadline = time.time() + tmo
        for line in proc.stdout:
            if time.time() > deadline:
                proc.kill()
                raise RuntimeError(f'codebuddy 流式超时 (>{tmo}s)')
            line = (line or '').strip()
            if not line or not line.startswith('{'):
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            t = d.get('type')
            # ── 诊断收集(空响应排查用)──
            if t == 'stream_event':
                ev = d.get('event') or {}
                ev_log.append('se:' + str(ev.get('type')))
            else:
                ev_log.append(t or '?')
            raw_tail.append(line[:400])
            if len(raw_tail) > 10:
                raw_tail.pop(0)
            if t == 'stream_event':
                ev = d.get('event') or {}
                if ev.get('type') == 'content_block_delta':
                    delta = ev.get('delta') or {}
                    if delta.get('type') == 'text_delta':
                        txt = delta.get('text') or ''
                        if txt:
                            full += txt
                            if on_text:
                                on_text(txt)
                    elif delta.get('type') == 'thinking_delta':
                        # 思考流:转发给调用方(作为 reasoning_content 显示),
                        # 不并入 full —— full 只放正文,避免污染解析
                        th = delta.get('thinking') or ''
                        if th and on_think:
                            on_think(th)
            elif t == 'result':
                r = d.get('result')
                if isinstance(r, str):
                    result = r
    finally:
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        err = ''
        try:
            err = (proc.stderr.read() or '').strip()[:400]
        except Exception:
            pass
        if proc.returncode not in (0, None) and not full and not result:
            if 'node: No such file' in err or 'node: not found' in err:
                raise RuntimeError('codebuddy 找不到 node —— 请确认 node 已安装且在 PATH 中')
            raise RuntimeError(err or f'exit {proc.returncode}')
        if proc.returncode in (0, None) and not full and not result:
            # ★ 空响应诊断:CLI 正常退出却没产出 —— 记录事件轨迹与原始行
            print(f'[bridge] [诊断] CLI 空响应! 事件轨迹: {ev_log}',
                  file=sys.stderr, flush=True)
            print(f'[bridge] [诊断] prompt={len(prompt)}字符 system={len(system or "")}字符 '
                  f'model={model}', file=sys.stderr, flush=True)
            for r in raw_tail[-6:]:
                print(f'[bridge] [诊断] RAW {r}', file=sys.stderr, flush=True)

    return full, (result if result is not None else full)


# ── 调用 CLI ──────────────────────────────────────────────
def call_codebuddy(model, prompt, system=None):
    """调 codebuddy -p,返回纯文本。失败抛 RuntimeError。跨平台。"""
    args = ['-p', '--tools', '', '--model', model]
    if system:
        args += ['--system-prompt', system]
    args.append(prompt)

    # Windows 上 codebuddy 是 .cmd 包装,必须经 cmd /c 调用
    cli = _cli_path()
    if IS_WIN and cli.lower().endswith(('.cmd', '.bat')):
        cmd = ['cmd', '/c', cli] + args
    else:
        cmd = [cli] + args

    env = dict(os.environ); env['CODEBUDDY_CONFIG_DIR'] = CONFIG_DIR
    env = _augment_path(env)          # ★ 补 PATH,避免 node 找不到
    cwd = tempfile.gettempdir()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding='utf-8', errors='replace',
                           timeout=CALL_TIMEOUT, env=env, cwd=cwd)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f'codebuddy 超时 (>{CALL_TIMEOUT}s)')
    except FileNotFoundError:
        raise RuntimeError(f'找不到 codebuddy: {CLI}(可设 CODEBUDDY_CLI 环境变量指定)')
    out = (r.stdout or '').strip()
    if r.returncode != 0 and not out:
        err = (r.stderr or '').strip()[:400]
        if 'node: No such file' in err or 'node: not found' in err:
            raise RuntimeError('codebuddy 找不到 node —— 请确认 node 已安装且在 PATH 中')
        raise RuntimeError(err or f'exit {r.returncode}')
    if not out:
        raise RuntimeError('codebuddy 返回空结果')
    return out


def to_prompt(messages):
    """OpenAI messages → 单个 prompt;system 单独返回。

    完整支持多轮 + 工具调用闭环:
      system               → 单独提取(不进 prompt)
      user                 → 用户输入
      assistant(带 tool_calls) → 记录"我调用了什么工具"
      tool                 → ★ 工具执行结果(关键,之前被丢弃导致闭环断裂)
      assistant(带 content)    → 历史回复
    """
    system, parts = None, []
    call_names = {}          # tool_call_id → 工具名

    for m in messages:
        role = m.get('role')
        content = m.get('content')
        if isinstance(content, list):   # 多模态 → 只取文本
            content = ' '.join(c.get('text', '') for c in content if isinstance(c, dict))
        content = (content or '').strip()

        if role == 'system':
            if content:
                system = (system + '\n' + content) if system else content

        elif role == 'user':
            if content:
                parts.append(content)

        elif role == 'assistant':
            # 可能带 tool_calls(这一轮模型决定调工具)
            for tc in (m.get('tool_calls') or []):
                fn = (tc or {}).get('function') or {}
                tid = tc.get('id') or ''
                name, args = fn.get('name', ''), fn.get('arguments', '')
                if tid:
                    call_names[tid] = name
                parts.append(f'[我调用了工具 {name}，参数 {args}]')
            if content:
                parts.append(f'[我之前的回复] {content}')

        elif role == 'tool':
            # ★ 工具执行结果 —— 必须回灌给模型,否则工具闭环断裂
            tid = m.get('tool_call_id') or ''
            name = m.get('name') or call_names.get(tid) or 'tool'
            if content:
                parts.append(f'[工具 {name} 的执行结果]\n{content}')

    return system, '\n\n'.join(parts)


# ── 请求执行与流式输出辅助 ────────────────────────────────

def run_with_retry(model, prompt, system, valid_names, forced, expect_tools,
                   tool_specs=None, max_retry=1):
    """调 CLI;若期望工具调用却没解析出来,追加更正提示重试一次。

    借鉴 NanoProxy 的 invalid_tool_block 重试:解析失败不要静默吞掉,
    而是把失败反馈给模型让它重来。
    返回 (text, tool_calls, remaining)
    """
    text, tool_calls, remaining = '', None, ''
    for attempt in range(max_retry + 1):
        if attempt == 0:
            cur = prompt
        else:
            cur = (prompt + '\n\n[系统提示] 你上一条回复没有包含合法的工具调用。'
                   '请重新回复:只输出一个 JSON 对象 '
                   '{"name": "工具名", "arguments": {...}}，'
                   '不要有任何其他文字、不要用代码块包裹。'
                   '特别注意:不要直接输出参数对象(如 {"command": "..."})，'
                   '必须用 name 字段指明工具名。')
        text = call_codebuddy(model, cur, system)
        tool_calls, remaining = parse_tool_calls(text, valid_names, forced, tool_specs)
        if tool_calls or not expect_tools:
            return text, tool_calls, remaining
    return text, tool_calls, remaining


def start_heartbeat(handler, interval=10):
    """起后台线程定期发 SSE 注释帧,防客户端/反代在长思考期间超时断流。

    借鉴 muse-bridge 的 ': ping' 与 NanoProxy 的 keepalive(15s)。
    返回 threading.Event,调用方 set() 即停止。
    """
    stop = threading.Event()
    if getattr(handler, '_wlock', None) is None:
        handler._wlock = threading.Lock()
    lock = handler._wlock

    def beat():
        while not stop.wait(interval):
            try:
                with lock:
                    handler.wfile.write(b': keep-alive\n\n')
                    handler.wfile.flush()
            except Exception:
                break
    threading.Thread(target=beat, daemon=True).start()
    return stop


def _frame(cid, ts, model, delta, finish=None):
    obj = {'id': cid, 'object': 'chat.completion.chunk', 'created': ts,
           'model': model,
           'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
    return ('data: ' + json.dumps(obj, ensure_ascii=False) + '\n\n').encode('utf-8')


def emit_sse_stream(handler, cid, ts, model, text, tool_calls, remaining, usage):
    """发完整 SSE 流。工具调用与纯文本两条分支,均遵循 OpenAI index/delta 约定。"""
    if getattr(handler, '_wlock', None) is None:
        handler._wlock = threading.Lock()
    lock = handler._wlock

    def w(b):
        with lock:
            handler.wfile.write(b)
            handler.wfile.flush()

    try:
        if tool_calls:
            # 首帧:role + 每个 call 的 index/id/type/name(arguments 先留空)
            w(_frame(cid, ts, model, {'role': 'assistant', 'content': None,
                'tool_calls': [{'index': i, 'id': c['id'], 'type': 'function',
                                'function': {'name': c['function']['name'], 'arguments': ''}}
                               for i, c in enumerate(tool_calls)]}))
            # 增量帧:只带 index + arguments 片段
            for i, c in enumerate(tool_calls):
                args = c['function']['arguments']
                for k in range(0, max(len(args), 1), 32):
                    w(_frame(cid, ts, model, {'tool_calls': [{'index': i,
                        'function': {'arguments': args[k:k + 32]}}]}))
            w(_frame(cid, ts, model, {}, 'tool_calls'))
        else:
            w(_frame(cid, ts, model, {'role': 'assistant', 'content': ''}))
            for i in range(0, len(text), 24):
                w(_frame(cid, ts, model, {'content': text[i:i + 24]}))
            w(_frame(cid, ts, model, {}, 'stop'))
        w(b'data: [DONE]\n\n')
        handler.close_connection = True
    except (BrokenPipeError, ConnectionResetError):
        pass


def emit_sse_error(handler, cid, ts, model, message):
    """流式下出错:发一条带 error 的帧 + [DONE],不裸断连接(客户端能读到原因)。"""
    try:
        obj = {'id': cid, 'object': 'chat.completion.chunk', 'created': ts,
               'model': model, 'choices': [],
               'error': {'message': message, 'type': 'upstream_error'}}
        handler.wfile.write(('data: ' + json.dumps(obj, ensure_ascii=False) + '\n\n').encode())
        handler.wfile.write(b'data: [DONE]\n\n')
        handler.wfile.flush()
        handler.close_connection = True
    except Exception:
        pass


# ── HTTP 服务 ─────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):      # 静音默认日志
        pass

    def log_req(self, msg):
        """请求级日志,带时间戳 + 强制 flush(否则 launchd 日志永远是空的)"""
        try:
            print(f'[{time.strftime("%H:%M:%S")}] {msg}', flush=True)
        except Exception:
            pass

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/v1/models':
            now = int(time.time())
            self._send(200, {'object': 'list', 'data': [
                {'id': m, 'object': 'model', 'created': now, 'owned_by': 'cli2openai'}
                for m in _allowed_models]})
        elif path in ('/health', '/v1/health'):
            self._send(200, {'ok': True, 'models': _allowed_models, 'cli': CLI,
                             'mode': MODE})
        else:
            self._send(404, {'error': {'message': f'not found: {path}'}})

    def _stream_run_structured(self, model, prompt, system, valid_names, forced,
                               cid, ts, tool_specs=None):
        """结构化输出模式的流式实现。

        取舍:正文不再逐字到达(结构化结果最后一次性给出),但工具调用是原生结构化的
        —— 不存在标记形态漂移。思考仍逐块转发(reasoning_content),等待期间不黑屏。
        """
        if getattr(self, '_wlock', None) is None:
            self._wlock = threading.Lock()
        lock = self._wlock

        def w(b):
            with lock:
                self.wfile.write(b)
                self.wfile.flush()

        def chunked_send(text, size=2048):
            for i in range(0, len(text), size):
                w(_frame(cid, ts, model, {'content': text[i:i + size]}))

        think_parts, last_flush = [], [0.0]

        def flush_think(force=False):
            if not think_parts:
                return
            now = time.time()
            if not force and now - last_flush[0] < 0.08:
                return
            txt = ''.join(think_parts)
            think_parts.clear()
            last_flush[0] = now
            if txt:
                w(_frame(cid, ts, model, {'reasoning_content': txt}))

        def on_think(txt):
            think_parts.append(txt)
            flush_think()

        w(_frame(cid, ts, model, {'role': 'assistant', 'content': ''}))

        reply, raw_calls, structured = call_codebuddy_structured(
            model, prompt, system, tool_specs=tool_specs, on_think=on_think)

        if structured is None:
            print('[bridge] [结构化] 空结果 → 带提示重试一次', file=sys.stderr, flush=True)
            retry_prompt = (prompt + '\n\n[系统提示] 上一轮没有产出结构化结果。'
                            '请调用 StructuredOutput,按 schema 填写 reply 与 tool_calls。')
            reply, raw_calls, structured = call_codebuddy_structured(
                model, retry_prompt, system, tool_specs=tool_specs, on_think=on_think)
            if structured is None:
                raise RuntimeError('codebuddy 结构化模式连续两次未返回 structured_output'
                                   '(诊断见 bridge err 日志)')

        flush_think(force=True)
        calls = [_mk_call(c['name'], c['arguments'], i)
                 for i, c in enumerate(raw_calls)]

        if reply:
            chunked_send(reply)

        if calls:
            w(_frame(cid, ts, model, {'tool_calls': [
                {'index': i, 'id': c['id'], 'type': 'function',
                 'function': {'name': c['function']['name'], 'arguments': ''}}
                for i, c in enumerate(calls)]}))
            for i, c in enumerate(calls):
                a = c['function']['arguments']
                for k in range(0, max(len(a), 1), 32):
                    w(_frame(cid, ts, model, {'tool_calls': [{'index': i,
                        'function': {'arguments': a[k:k + 32]}}]}))
            w(_frame(cid, ts, model, {}, 'tool_calls'))
        else:
            w(_frame(cid, ts, model, {}, 'stop'))

        w(b'data: [DONE]\n\n')
        self.close_connection = True
        return reply, calls, ''

    def _stream_run(self, model, prompt, system, valid_names, forced, cid, ts,
                    tool_specs=None):
        """真流式执行:CLI 边生成边转发,最后处理工具调用。

        过滤分级:
          · 硬标记(JSON 契约 / DSML)→ 停发,不补发(几乎不误判)
          · 软信号(裸参数对象)→ 停发;带【看门狗】—— 扣留超过 _SOFT_HOLD_MAX
            字符仍无定论就恢复发送(文档里含 JSON 示例时会误判,不能把整篇扣死);
            若最终没解析出工具调用则补发(内容不丢)
        """
        _SOFT_HOLD_MAX = 4000
        # 结构化模式:走原生 --json-schema,不做标记解析
        if MODE == 'structured':
            return self._stream_run_structured(model, prompt, system, valid_names,
                                               forced, cid, ts, tool_specs)
        buf, json_mode, soft_mode, sent_len = [], [False], [False], [0]
        cut_pos, watchdog_done = [0], [False]
        if getattr(self, '_wlock', None) is None:
            self._wlock = threading.Lock()
        lock = self._wlock

        # 裸参数对象检测用的键名集合(来自本轮工具定义)
        arg_keys = set()
        for sp in (tool_specs or []):
            arg_keys |= sp['props']
        _bare_re = (re.compile(r'[\n\r{,]\s*\{\s*"([A-Za-z_][\w]*)"\s*:') if arg_keys else None)

        # 第 9 形态:裸工具名标签(<execute_code> 等)—— 工具名本轮动态,只能现算
        _name_re = None
        _names = [n for n in (valid_names or []) if n]
        if _names:
            _alt = '|'.join(re.escape(n) for n in sorted(_names, key=len, reverse=True))
            _name_re = re.compile(rf'<({_alt})\s*>', re.I)

        def w(b):
            with lock:
                self.wfile.write(b)
                self.wfile.flush()

        def chunked_send(text, size=2048):
            """大文本分块发送 —— 单帧几十 KB 容易让对方流解析出错。"""
            for i in range(0, len(text), size):
                w(_frame(cid, ts, model, {'content': text[i:i + size]}))

        # ── 思考流:作为 reasoning_content 转发(Hermes 单独渲染;to_prompt 不读它,不入历史) ──
        think_parts, last_think_flush = [], [0.0]
        think_total = [0]                      # 累计思考字数(空响应诊断用)

        def flush_think(force=False):
            if not think_parts:
                return
            now = time.time()
            if not force and now - last_think_flush[0] < 0.08:
                return
            txt = ''.join(think_parts)
            think_parts.clear()
            last_think_flush[0] = now
            if txt:
                w(_frame(cid, ts, model, {'reasoning_content': txt}))

        def on_think(txt):
            think_total[0] += len(txt)
            think_parts.append(txt)
            flush_think()

        def on_text(txt):
            buf.append(txt)
            acc = ''.join(buf)
            if json_mode[0]:
                # ★ 软信号看门狗:扣留太久还没定论 → 恢复发送(避免整篇被扣死)
                if soft_mode[0] and not watchdog_done[0]:
                    if len(acc) - cut_pos[0] > _SOFT_HOLD_MAX:
                        chunked_send(acc[sent_len[0]:])
                        sent_len[0] = len(acc)
                        json_mode[0] = False
                        soft_mode[0] = False
                        watchdog_done[0] = True     # 本轮的软信号不再触发
                return
            # ★ 硬标记:全文扫描(不只看开头)—— 模型常先说一句再输出 JSON
            m = _MARKER_RE.search(acc)
            if m:
                cut = m.start()
                if cut > sent_len[0]:           # 标记之前的正常文本仍然发出
                    chunked_send(acc[sent_len[0]:cut])
                    sent_len[0] = cut
                json_mode[0] = True
                return
            # ★ 第 9 形态:裸工具名标签 —— 工具名本轮动态,命中即停发
            if _name_re is not None:
                mn = _name_re.search(acc)
                if mn:
                    cut = mn.start()
                    if cut > sent_len[0]:
                        chunked_send(acc[sent_len[0]:cut])
                        sent_len[0] = cut
                    json_mode[0] = True
                    return
            # ★ 软信号:裸参数对象(模型忘写 name 包装时)—— 停发 + 看门狗保护
            if _bare_re is not None and not watchdog_done[0]:
                mb = _bare_re.search(acc)
                if mb and mb.group(1) in arg_keys:
                    cut = mb.start()
                    if cut > sent_len[0]:
                        chunked_send(acc[sent_len[0]:cut])
                        sent_len[0] = cut
                    cut_pos[0] = cut
                    json_mode[0] = True
                    soft_mode[0] = True
                    return
            # ★ 尾部若疑似硬标记前缀(如 {"name 跨块断开),先扣着不发,等下一块确认
            hold = _partial_marker_tail(acc)
            safe_end = max(len(acc) - hold, sent_len[0])
            new = acc[sent_len[0]:safe_end]
            if new:
                chunked_send(new)
                sent_len[0] = safe_end

        w(_frame(cid, ts, model, {'role': 'assistant', 'content': ''}))

        full, result = call_codebuddy_stream(model, prompt, system, on_text,
                                             on_think=on_think)
        # ★ 空响应兜底:带提示重试一次(应对模型静默/上游偶发)
        if not full and not result:
            print('[bridge] 空响应 → 带提示重试一次', file=sys.stderr, flush=True)
            retry_prompt = (prompt + '\n\n[系统提示] 你上一条没有输出任何内容。'
                            '请给出你的回复（工具调用或文本均可）。')
            full, result = call_codebuddy_stream(model, retry_prompt, system, on_text,
                                                 on_think=on_think)
            if not full and not result:
                print(f'[bridge] 空响应重试仍空 → 报错 '
                      f'(prompt={len(prompt):,} system={len(system or ""):,} '
                      f'thinking={think_total[0]:,})', file=sys.stderr, flush=True)
                raise RuntimeError(
                    f'模型本轮只产生思考、未产生正文'
                    f'（思考 {think_total[0]:,} 字符；'
                    f'prompt {len(prompt):,} + system {len(system or ""):,} 字符）。'
                    f'上下文过长时会出现这种情况 —— 建议压缩会话历史、'
                    f'或拆成更小的任务后重试。')
        flush_think(force=True)          # 把最后一段思考发完
        calls, remaining = parse_tool_calls(result, valid_names, forced, tool_specs)

        if calls:
            w(_frame(cid, ts, model, {'tool_calls': [
                {'index': i, 'id': c['id'], 'type': 'function',
                 'function': {'name': c['function']['name'], 'arguments': ''}}
                for i, c in enumerate(calls)]}))
            for i, c in enumerate(calls):
                a = c['function']['arguments']
                for k in range(0, max(len(a), 1), 32):
                    w(_frame(cid, ts, model, {'tool_calls': [{'index': i,
                        'function': {'arguments': a[k:k + 32]}}]}))
            w(_frame(cid, ts, model, {}, 'tool_calls'))
        else:
            # 没有工具调用:补发未发送部分(分块)。
            # 硬标记违规 → 不补发;软信号误判 → 补发(内容不丢)。
            if not json_mode[0] or soft_mode[0]:
                rest = ''.join(buf)[sent_len[0]:]
                if rest:
                    chunked_send(rest)
            w(_frame(cid, ts, model, {}, 'stop'))

        w(b'data: [DONE]\n\n')
        self.close_connection = True
        return result, calls, remaining

    def do_POST(self):
        if self.path.split('?')[0] != '/v1/chat/completions':
            self._send(404, {'error': {'message': 'only /v1/chat/completions'}})
            return
        try:
            n = int(self.headers.get('Content-Length') or 0)
            req = json.loads(self.rfile.read(n) or b'{}')
        except Exception as e:
            self._send(400, {'error': {'message': f'bad json: {e}'}})
            return

        model = (req.get('model') or _allowed_models[0]).strip()
        t_start = time.time()
        self.log_req(f'→ {model} | msgs={len(req.get("messages") or [])} '
                     f'tools={len(req.get("tools") or [])} stream={bool(req.get("stream"))}')
        # ★ 白名单硬守卫
        if model not in _allowed_models:
            self._send(403, {'error': {
                'message': f'model "{model}" 不在可用模型范围内。当前可用: {_allowed_models}',
                'type': 'model_not_allowed', 'code': 'model_not_allowed'}})
            return

        system, prompt = to_prompt(req.get('messages') or [])
        tools = req.get('tools') or []
        tool_choice = req.get('tool_choice')

        # 工具名白名单 + 强制指定(防模型编造工具名)
        valid_names, forced, tool_specs = None, None, None
        if tools:
            valid_names = {(t.get('function') or {}).get('name') for t in tools}
            valid_names.discard(None); valid_names.discard('')
            tool_specs = _build_tool_specs(tools)
            if isinstance(tool_choice, dict):
                forced = ((tool_choice.get('function') or {}).get('name')
                          or tool_choice.get('name'))
            system = build_system_prompt(system, tools, tool_choice)

        if not prompt:
            self._send(400, {'error': {'message': 'messages 为空'}})
            return

        is_stream = bool(req.get('stream'))
        expect_tools = (tool_choice == 'required') or bool(forced)
        cid = f'chatcmpl-wb-{int(time.time()*1000)}'
        ts = int(time.time())

        # ── 流式:先发响应头 + 保活注释,再去跑 CLI ──
        # （关键:否则 CLI 跑 40s 期间客户端连 HTTP 头都收不到,会被判超时)
        stop_hb = None
        if is_stream:
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.send_header('X-Accel-Buffering', 'no')      # 防中间层缓冲
            self.end_headers()
            try:
                self.wfile.write(b': connected\n\n'); self.wfile.flush()
            except Exception:
                return
            stop_hb = start_heartbeat(self)

        # ── 流式:真流式 CLI,边生成边转发(不等跑完)──
        if is_stream:
            try:
                text, tool_calls, remaining = self._stream_run(
                    model, prompt, system, valid_names, forced, cid, ts, tool_specs)
            except Exception as e:
                if stop_hb:
                    stop_hb.set()
                emit_sse_error(self, cid, ts, model, str(e))
                return
            if stop_hb:
                stop_hb.set()
            self.log_req(f'← {model} | {time.time()-t_start:.1f}s | 流式 | '
                         f'tool_calls={len(tool_calls) if tool_calls else 0} len={len(text)}')
            return

        # ── 非流式:阻塞调用 + 失败重试 ──
        try:
            text, tool_calls, remaining = run_with_retry(
                model, prompt, system, valid_names, forced, expect_tools, tool_specs)
        except Exception as e:
            self._send(502, {'error': {'message': str(e), 'type': 'upstream_error'}})
            return

        self.log_req(f'← {model} | {time.time()-t_start:.1f}s | '
                     f'tool_calls={len(tool_calls) if tool_calls else 0} '
                     f'len={len(text)}')

        # 粗略 token 估算(4 字符 ≈ 1 token)
        usage = {'prompt_tokens': len(prompt)//4, 'completion_tokens': len(text)//4,
                 'total_tokens': (len(prompt)+len(text))//4}

        # ── 非流式 ──
        if tool_calls:
            message = {'role': 'assistant', 'content': remaining or None,
                       'tool_calls': tool_calls}
            finish = 'tool_calls'
        else:
            message = {'role': 'assistant', 'content': text}
            finish = 'stop'
        self._send(200, {
            'id': cid, 'object': 'chat.completion', 'created': ts,
            'model': model,
            'choices': [{'index': 0, 'finish_reason': finish, 'message': message}],
            'usage': usage})


def main():
    args = sys.argv[1:]
    if '--refresh' in args:
        free, changed = refresh_models()
        print(json.dumps({'free': free, 'changed': changed}, ensure_ascii=False, indent=1))
        return
    load_cache()
    refresh_models()

    port = 8790
    if '--port' in args:
        port = int(args[args.index('--port') + 1])

    # 后台定时刷新
    def loop():
        while True:
            time.sleep(REFRESH_HOURS * 3600)
            refresh_models()
    threading.Thread(target=loop, daemon=True).start()

    print(f'[bridge] cli2openai 启动 → http://127.0.0.1:{port}/v1', flush=True)
    print(f'[bridge] 可用模型范围: {_allowed_models}', flush=True)
    print(f'[bridge] CLI: {CLI}', flush=True)
    print(f'[bridge] 并发: ThreadingHTTPServer(多请求并行)', flush=True)
    # ThreadingHTTPServer:避免单线程串行导致 Hermes 并发请求排队超时
    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()


if __name__ == '__main__':
    main()
