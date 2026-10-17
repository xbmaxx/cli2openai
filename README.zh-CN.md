# cli2openai

**把 CodeBuddy CLI 暴露成 OpenAI 兼容接口 —— 让你现有的 OpenAI 客户端直接驱动它。**

[English](README.md) | **中文**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![tests](https://github.com/xbmaxx/cli2openai/actions/workflows/tests.yml/badge.svg)](https://github.com/xbmaxx/cli2openai/actions/workflows/tests.yml)

> ⚠️ **使用前请先读 [免责声明](DISCLAIMER.md)。** 本项目是**互操作性适配器**：
> 只通过官方分发的 CLI 及其文档化的非交互模式驱动它。**本项目与上游厂商无任何关联、
> 未获其授权或背书。** 你需要自行遵守相关条款。

---

## 它解决什么问题

CodeBuddy CLI 是一个完整的编码 agent，有自己的工具、权限和会话管理。但它**只说自己那套协议**。

而主流客户端 —— Hermes Agent、Open WebUI、Continue、LiteLLM、LangChain、你自己的脚本 ——
期待的是 OpenAI 的形状：`POST /v1/chat/completions`、SSE 流式、响应里带 `tool_calls`。
这四样 CLI 一样都不提供，所以它**根本插不进这些客户端**。

这个项目解决的就是这一个问题。

## 怎么解决

一个本地桥接层夹在两者之间：

```
你的客户端 ──(OpenAI /v1/chat/completions)──▶ cli2openai.py ──(官方 CLI，-p 模式)──▶ CodeBuddy
     ▲                                              │
     └────────── SSE + 原生 tool_calls ◀────────────┘
                （从 CLI 的文本输出解析回来）
```

三件事：

1. **接收** OpenAI 格式请求（messages、tools、`tool_choice`、流式）。
2. **驱动** 官方分发的 CLI，走它文档化的非交互模式。
3. **翻译回来** —— CLI 吐的是文本和标记，不是 `tool_calls`；桥接层把那份输出解析成原生
   `tool_calls`，多轮工具调用才能闭环。

你原有的东西全部照旧：客户端、提示词、工具循环、会话历史。

## 它是什么 / 不是什么

| | |
|---|---|
| ✅ **是**本地协议适配器（OpenAI ⇄ CLI） | ❌ **不是**逆向客户端 |
| ✅ **是**基于官方分发、MIT 许可的 `@tencent-ai/codebuddy-code` | ❌ **不是**凭据提取器或账号池 |
| ✅ **是**每人各自跑：每个用户驱动自己安装的 CLI、自己的会话 | ❌ **不是**中转、代理或托管服务 |
| ✅ **是**透明的：不打包二进制、不打补丁改上游 | ❌ **不是**厂商官方项目，也无任何背书关系 |

**仓库内不含任何厂商代码，也不打包任何二进制。**

---

## 环境要求

- macOS 或 Windows（Linux 预计可用，未充分测试）
- Python 3.8+
- Node.js + 已安装并登录的官方 CLI：

```bash
npm install -g @tencent-ai/codebuddy-code
codebuddy              # 交互模式 → 登录
```

## 快速开始

```bash
git clone https://github.com/xbmaxx/cli2openai.git
cd cli2openai

# macOS / Linux
./install.sh                # 装载 launchd 常驻服务（:8790）
./verify.sh                 # 18 项本地自检

# Windows（PowerShell）
.\install.ps1
```

也可以前台直接跑：

```bash
python3 cli2openai.py        # → http://127.0.0.1:8790/v1
```

```bash
curl -s http://127.0.0.1:8790/health
# {"ok": true, "cli": "/path/to/codebuddy", "models": ["..."]}
```

## 从任意 OpenAI 客户端使用

把客户端的 `base_url` 指向 `http://127.0.0.1:8790/v1`，API key 填任意非空值，
模型从 `/v1/models` 里选。

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8790/v1", api_key="not-needed")
print(client.chat.completions.create(
    model="deepseek-v4.1-flash",
    messages=[{"role": "user", "content": "你好"}],
).choices[0].message.content)
```

流式与工具调用，和对接真实 OpenAI 端点时完全一致：

```python
stream = client.chat.completions.create(
    model="deepseek-v4.1-flash",
    messages=[{"role": "user", "content": "列出 /tmp 下的文件"}],
    tools=[{
        "type": "function",
        "function": {
            "name": "terminal",
            "description": "执行 shell 命令",
            "parameters": {"type": "object",
                           "properties": {"command": {"type": "string"}},
                           "required": ["command"]},
        },
    }],
    stream=True,
)
```

### 接入 Hermes Agent（自定义 provider）

```yaml
custom_providers:
  - name: codebuddy-cli
    base_url: http://127.0.0.1:8790/v1
    api_key: local
    models:
      deepseek-v4.1-flash:
        context_window: 1000000
      hy3:
        context_window: 200000
```

`setup_hermes.py` 会自动完成这次编辑（幂等，带备份与回滚）：

```bash
python3 setup_hermes.py           # 注入 provider 到 ~/.hermes/config.yaml
python3 setup_hermes.py --dry-run # 只预览，不落盘
```

## 模型发现

`/v1/models` 的内容**从 CLI 自己的模型目录同步** —— 桥接层向 CLI 询问"当前会话可以调用哪些模型"，
然后原样暴露这份清单。

两处刻意的排除：

- **路由型条目**（`default-model`）被过滤掉。它不是一个确定的模型，而是一个自动路由，
  行为不可复现、上下文窗口也不固定。桥接层应当只暴露**确定性**目标。
- **未知名称**直接 `404`，避免拼写错误静默落到意料之外的模型上。

目录会缓存到本地；CLI 升级后用 `model_watch.py` 刷新并比对差异。

---

## 工具调用是怎么桥接的

这是本项目真正有技术含量的部分。CLI 不输出原生 `tool_calls`，所以桥接层在 system prompt
里定义一套 JSON 契约，再把它解析回来。真实的模型输出很脏 —— 目前已在实践中观察到
**七种不同形态**：

| # | 形态 | 处理方式 |
|---|---|---|
| 1 | `{"name": "x", "arguments": {...}}` | 契约主路径 |
| 2 | `{"function": {"name": ..., "arguments": ...}}` | OpenAI 原生形 |
| 3 | `<｜｜DSML｜｜ calls> <｜｜DSML｜｜ invoke name="x">` | 先归一化再匹配 |
| 4 | DSML 混合简写 | 先过 `_normalize_tags()` |
| 5 | **先说一句，再输出 JSON** | **全文扫描**（只看开头会漏） |
| 6 | **裸参数对象** —— `{"command": "ls"}`（缺 `name`） | 按"必填参数覆盖率 + 键名匹配率 ≥50%"反推工具；有歧义就拒绝 |
| 7 | 标记被流式切断（`{"name`） | 尾部扣留，等下一块再判定 |

两条写进代码的教训：

- **`--tools ""` 并不能阻止模型"以为"自己有内置工具。** 它会输出
  `<｜｜DSML｜｜ invoke name="Write">`，白白消耗一整轮。所以 system prompt 里明确写：
  *你没有内置工具；唯一合法的调用格式是下面这个 JSON 契约。*
- **流式拆掉了"事后清理"这层安全网。** 非流式路径可以把 JSON 残片从最终文本里剥掉；
  流式发出去的字节收不回来。所以过滤分两级 —— 硬标记（不再补发）与软信号（先扣留，
  最终没解析出工具调用就补发），再加一个 4 KB 看门狗释放被扣内容。

---

## 性能（实测数据）

同机、同任务：

| | 直连 API | 经本桥接 |
|---|---|---|
| 小任务（工具调用）首字节 | 0.80 秒 | 3.20 秒 |
| 400 字文档 首字节 | 3.49 秒 | 5.78 秒 |
| 生成速度 | 385 字符/秒 | 320 字符/秒 |
| **每轮固定开销** | — | **+2.4 秒** |

**那 2.4 秒花在哪：** 进程启动 + CLI 初始化 + 多一跳网络
（`本机 → 厂商端点 → 模型`）。这是架构成本，不改上游消不掉。

**试过并被否决的做法**（省得你再踩一遍）：

| 尝试 | 结果 |
|---|---|
| 常驻 `--input-format stream-json` 进程 | 1.5 秒/轮，但**不可用**：上下文累积、`/clear` 会回落到磁盘"记忆"文件、工具禁用悄悄失效 |
| `--serve` + `api/v1/llm/completions` | 端点存在，但没有 GUI 会话上下文时上游直接拒绝（`400 request illegal`） |
| 截断过长的工具输出 | **慢 3 倍** —— 模型看到"已省略 10,000 字符"就花思考去补回来 |
| `--effort minimal` / `low` | 比默认**慢 2–3 倍**（默认完全不产生思考 token） |
| 换 `hy3` | 比 `deepseek-v4.1-flash` 慢一倍 |

**最大的变量是模型自己的思考预算，而它是随机的：** 同一请求跑六次，耗时
6.5 / 9.4 / 14.5 / 15.6 / 16.9 / 18.6 秒（思考 1.7 K–7.2 K 字符）。
**需要稳定延迟就直接调模型 API。**

---

## 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康状态 + 解析到的 CLI 路径 + 模型清单 |
| GET | `/v1/models` | OpenAI 模型列表（从 CLI 目录同步） |
| POST | `/v1/chat/completions` | 支持 `stream`（SSE）与工具调用 |

流式会把模型的思考以 `reasoning_content` 增量转发（80 ms 节流）。
`to_prompt()` **不读**该字段，所以思考只用于显示、**永不回灌** ——
不会撑大上下文，也不会触发思考塌缩循环。

---

## 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| 报 `codebuddy not found` / 全部请求失败 | npm 自动更新死在"创建临时链接"和"替换正式链接"之间 | 桥接层每次调用前自愈符号链接（`_heal_codebuddy_link`） |
| 对话里出现裸 JSON/DSML | 模型用了过滤器还不认识的标记形态 | 对照七形态表，补一条规则 |
| 任务中途停住 | 标记未被解析 → `tool_calls=0` | 裸参数推断（形态 #6） |
| 写长文档时几分钟无输出 | 软信号扣留但没有看门狗 | 4 KB 后自动释放 |
| 报 `empty content` | CLI 正常退出但零输出 | 带提示重试一次，仍空则报明确错误而不是沉默 |
| **某一轮永久卡住、零输出** | `WB_MODE=structured`（`--json-schema`）：模型若只用纯文本回答、没有调用 `StructuredOutput` 工具，CLI 认为结构化结果未交付，不产出 `result` 也不退出 | 进程级看门狗 90 秒强杀；**保持默认 `WB_MODE=text`** |

### 关于 `WB_MODE=structured`（实验性，默认关闭）

设 `WB_MODE=structured` 会切到 CLI 原生的 `--json-schema` 通道：模型必须通过内置的
`StructuredOutput` 工具交付结果，因此工具调用标记的形态漂移会彻底消失。

**但它有一个硬失败模式。** 当模型用纯文本回答、没有调用 `StructuredOutput` 时，CLI 认为
结构化结果未交付——它不产出 `result` 事件，也不退出。进程就此空转，既不报错也不超时。

因此桥接层给每次 CLI 调用加了进程级看门狗（`CALL_TIMEOUT`，默认 90 秒）：到点强杀子进程
并抛出明确错误。即便如此，**仍建议保持 text 模式为默认**——它有正常的退出路径，且经过
的实际验证更多。

---

## 致谢

设计决策受益于对若干开源协议桥接项目的研读（muse-bridge、NanoProxy、acpbox、
sample-acp-bridge）。重试、保活、"扣留尾部"、"软信号补发"这些模式是借鉴来的思路，
本项目针对"只输出文本、没有原生 tool_calls"的 CLI 重新实现。

## 许可证

[MIT](LICENSE) © 2026 xbmaxx
