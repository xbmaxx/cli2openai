# WorkBuddy Bridge

**把腾讯官方 CodeBuddy CLI 变成 OpenAI 兼容接口 —— 不需要 API key，不做逆向。**

[English](README.md) | **中文**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)

> ⚠️ **使用前请先读 [免责声明](DISCLAIMER.md)。** 本项目是**互操作性适配器**，不是破解工具：
> 只使用官方分发的 CLI 及其文档化的非交互模式。你需要自行遵守腾讯的服务条款。

---

## 为什么需要它

CodeBuddy CLI **只用登录**就能认证，不需要 API key；部分模型按 **`x0.00` 积分**计费（免费）。
但 CLI 说的是自己的协议，普通 OpenAI 客户端（Hermes Agent、Open WebUI、Continue、LiteLLM、
你自己的脚本）用不了。

这个桥接层居中协调：

```
你的客户端 ──(OpenAI /v1/chat/completions)──▶ wb_bridge.py ──(官方 CLI，-p 模式)──▶ 模型
                                                  │
                                                  └── 把 CLI 的文本/标记输出
                                                      解析回原生 tool_calls
```

## 它是什么 / 不是什么

| | |
|---|---|
| ✅ **是**本地协议适配器（CLI 文本 → OpenAI `tool_calls`） | ❌ **不是**逆向客户端 |
| ✅ **是**基于官方 MIT 许可的 `@tencent-ai/codebuddy-code` | ❌ **不是**凭据提取器或账号池 |
| ✅ **是**每人各自跑：每个用户对自己已登录的账号运行 | ❌ **不是**中转服务、代理或转售产品 |
| ✅ **是**积分安全：只放行 `x0.00` 计费的模型 | ❌ **不是**绕过付费套餐的手段 |

**仓库内不含任何腾讯代码，也不打包任何二进制。**

---

## 环境要求

- macOS 或 Windows（Linux 理论上可用，未充分测试）
- Python 3.8+
- Node.js + 已安装并登录的官方 CLI：

```bash
npm install -g @tencent-ai/codebuddy-code --registry=https://registry.npmmirror.com
codebuddy              # 交互模式 → 登录（浏览器或扫码）
```

## 快速开始

```bash
git clone https://github.com/xbmaxx/workbuddy-bridge.git
cd workbuddy-bridge

# macOS / Linux
./install.sh                # 装载 launchd 常驻服务（:8790）
./verify.sh                 # 18 项自检

# Windows（PowerShell）
.\install.ps1
```

也可以前台直接跑：

```bash
python3 wb_bridge.py        # → http://127.0.0.1:8790/v1
```

健康检查：

```bash
curl -s http://127.0.0.1:8790/health
# {"ok": true, "free_models": ["deepseek-v4.1-flash", "hy3"], ...}
```

## 从任意 OpenAI 客户端使用

把客户端的 `base_url` 指向 `http://127.0.0.1:8790/v1`，API key 随便填一个非空值，
模型从 `/v1/models` 里选。

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8790/v1", api_key="not-needed")
print(client.chat.completions.create(
    model="deepseek-v4.1-flash",
    messages=[{"role": "user", "content": "你好"}],
).choices[0].message.content)
```

### 接入 Hermes Agent（自定义 provider）

```yaml
custom_providers:
  - name: workbuddy-free
    base_url: http://127.0.0.1:8790/v1
    api_key: local
    models:
      deepseek-v4.1-flash:
        context_window: 1000000
      hy3:
        context_window: 200000
```

`setup_hermes.py` 可以自动完成（幂等、带备份与回滚）：

```bash
python3 setup_hermes.py           # 注入 provider 到 ~/.hermes/config.yaml
python3 setup_hermes.py --dry-run # 先预览
```

---

## 积分保护（本项目的核心）

三重独立防线，避免误扣积分：

1. **`NEVER_ALLOW` 黑名单** —— `default-model`（"Auto" 路由）永久禁用：
   它可能静默路由到付费模型，而积分字段显示为空。
2. **动态白名单** —— 从 CLI 自己的模型目录读取，只有 `_meta.credits == "x0.00"` 才放行。
3. **请求时守卫** —— 白名单外的模型在调用 CLI **之前**就返回 `HTTP 403`。

`wb_watch.py` 会记录模型目录快照，免费模型变收费时能及时发现：

```bash
python3 wb_watch.py --once     # 与上次快照比对
```

---

## 工具调用怎么桥接的

CLI 不会输出原生 `tool_calls`，所以桥接层自定义了一套契约再解析回来。
真实的模型输出很脏 —— 目前已在实践中观察到 **七种不同形态**：

| # | 形态 | 处理方式 |
|---|---|---|
| 1 | `{"name": "x", "arguments": {...}}` | 主路径 |
| 2 | `{"function": {"name": ..., "arguments": ...}}` | OpenAI 原生形 |
| 3 | `<｜｜DSML｜｜ calls> <｜｜DSML｜｜ invoke name="x">` | 先归一化再匹配 |
| 4 | DSML 混合简写 | 先过 `_normalize_tags()` |
| 5 | **先说一句，再输出 JSON** | **全文扫描**（不能只看开头） |
| 6 | **裸参数对象** —— `{"command": "ls"}`（缺 `name`） | 按"必填参数覆盖率 + 键名匹配率 ≥50%"反推工具；歧义则拒绝 |
| 7 | 标记被流式切断（`{"name`） | 尾部扣留，等下一块再判定 |

两条写进代码的教训：

- **`--tools ""` 并不能阻止模型"以为"自己有内置工具。** 它会输出
  `<｜｜DSML｜｜ invoke name="Write">`，白白浪费一整轮。所以 system prompt 里明确写：
  *你没有内置工具；唯一合法的格式是下面这个 JSON 契约。*
- **流式拆掉了"事后清理"这层安全网。** 非流式可以把 JSON 残片从最终文本里剥掉；
  流式发出去的字节收不回来。所以过滤分两级 —— 硬标记（不再补发）与软信号（先扣留，
  最终没解析出工具调用就补发），再加一个 4 KB 看门狗释放被扣内容。

---

## 性能（实测数据，不是宣传）

同机、同任务、`deepseek-v4.1-flash`：

| | DeepSeek 官方 API | 本桥接 |
|---|---|---|
| 小任务（工具调用）首字节 | 0.80 s | 3.20 s |
| 400 字文档 首字节 | 3.49 s | 5.78 s |
| 生成速度 | 385 字符/秒 | 320 字符/秒 |
| **每轮固定开销** | — | **+2.4 秒** |

**那 2.4 秒花在哪：** 进程启动 + CLI 初始化 + 多一跳网络
（`本机 → codebuddy.ai → 模型`）。这是架构成本，不改上游就消不掉。

**试过并且反而更慢的做法（别重复踩）：**

| 尝试 | 结果 |
|---|---|
| 常驻 `--input-format stream-json` 进程 | 1.5 秒/轮，但**不可用**：上下文累积、`/clear` 会回落到磁盘"记忆"文件、工具禁用失效 |
| `--serve` 的 `api/v1/llm/completions` | 端点存在，但上游返回 `400 request illegal`（缺 GUI 会话上下文） |
| 截断过长的工具输出 | **慢 3 倍** —— 模型看到"已省略 10,000 字符"就花思考去补回来 |
| `--effort minimal` / `low` | 比默认**慢 2–3 倍**（默认完全不产生思考 token） |
| 换 `hy3` | 比 `deepseek-v4.1-flash` 慢一倍 |

**最大的变量是模型自己的思考预算，而这是随机的：** 同一请求跑六次，
耗时 6.5 / 9.4 / 14.5 / 15.6 / 16.9 / 18.6 秒（思考 1.7 K–7.2 K 字符）。
**需要稳定延迟就用付费 API 直连。**

---

## 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 状态 + 免费模型白名单 |
| GET | `/v1/models` | OpenAI 模型列表（仅白名单） |
| POST | `/v1/chat/completions` | 支持 `stream`（SSE）与工具调用 |

流式会把模型的思考以 `reasoning_content` 增量转发（80 ms 节流）。
`to_prompt()` **不读**该字段，所以思考只用于显示，**永不回灌** ——
不会撑大上下文，也不会触发思考塌缩循环。

---

## 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| 报 `找不到 codebuddy` / 全部请求失败 | npm 自动更新死在"创建临时链接"和"替换正式链接"之间 | 桥接层每次调用前自愈符号链接（`_heal_codebuddy_link`） |
| 对话里出现裸 JSON/DSML | 模型输出了过滤器还不认识的标记形态 | 对照七形态表，补一条规则 |
| 任务中途停住 | 标记未被解析 → `tool_calls=0` | 裸参数推断（形态 #6） |
| 写长文档时几分钟无输出 | 软信号扣留但没有看门狗 | 4 KB 后自动释放 |
| 报 `empty content` | CLI 正常退出但零输出 | 带提示重试一次，仍空则报明确错误而不是沉默 |

---

## 致谢

设计决策受益于若干开源协议桥接项目的研读（muse-bridge、NanoProxy、acpbox、
sample-acp-bridge）—— 重试、保活、"扣留尾部"、"软信号补发"这些模式是借鉴来的思路，
本项目针对"只输出文本、没有原生 tool_calls"的 CLI 重新实现。

## 许可证

[MIT](LICENSE) © 2026 xbmaxx
