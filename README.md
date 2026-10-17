# WorkBuddy Bridge

**Turn the official Tencent CodeBuddy CLI into an OpenAI-compatible API endpoint — no API key, no reverse engineering.**

**English** | [中文](README.zh-CN.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)

> ⚠️ **Read [DISCLAIMER.md](DISCLAIMER.md) first.** This project is an *interoperability
> adapter*, not a crack. It uses only the officially distributed CLI and its documented
> non-interactive mode. You are responsible for complying with Tencent's terms.

---

## Why this exists

The CodeBuddy CLI authenticates with **just a login** — no API key required — and some
models are billed at **`x0.00` credits** (free). But the CLI speaks *its own* protocol,
so you cannot use it from ordinary OpenAI-compatible clients (Hermes Agent, Open WebUI,
Continue, LiteLLM, your own scripts…).

This bridge sits in between:

```
your client ──(OpenAI /v1/chat/completions)──▶ wb_bridge.py ──(official CLI, -p mode)──▶ model
                                                  │
                                                  └── parses the CLI's text/markup output
                                                      back into native tool_calls
```

## What it is (and isn't)

| | |
|---|---|
| ✅ **Is** a local protocol adapter (CLI text output → OpenAI `tool_calls`) | ❌ **Is not** a reverse-engineered client |
| ✅ **Is** built on the official, MIT-licensed `@tencent-ai/codebuddy-code` CLI | ❌ **Is not** a credential extractor or account pool |
| ✅ **Is** per-user: each user runs it against their own logged-in account | ❌ **Is not** a relay service, proxy, or resale product |
| ✅ **Is** credit-safe: only models priced `x0.00` are allowed | ❌ **Is not** a way to avoid paying for a paid plan |

**It contains no Tencent code and bundles no binaries.**

---

## Requirements

- macOS or Windows (Linux should work; untested)
- Python 3.8+
- Node.js + the official CLI installed and logged in:

```bash
npm install -g @tencent-ai/codebuddy-code --registry=https://registry.npmmirror.com
codebuddy              # interactive mode → log in (browser or QR)
```

## Quick start

```bash
git clone https://github.com/xbmaxx/workbuddy-bridge.git
cd workbuddy-bridge

# macOS / Linux
./install.sh                # installs a launchd (mac) or systemd-ish service on :8790
./verify.sh                 # 18 self-checks

# Windows (PowerShell)
.\install.ps1
```

Or run it in the foreground:

```bash
python3 wb_bridge.py        # → http://127.0.0.1:8790/v1
```

Health check:

```bash
curl -s http://127.0.0.1:8790/health
# {"ok": true, "free_models": ["deepseek-v4.1-flash", "hy3"], ...}
```

## Use it from any OpenAI client

Point your client at `http://127.0.0.1:8790/v1`, use any non-empty API key, and pick a
model from `/v1/models`.

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8790/v1", api_key="not-needed")
print(client.chat.completions.create(
    model="deepseek-v4.1-flash",
    messages=[{"role": "user", "content": "hello"}],
).choices[0].message.content)
```

### Hermes Agent (custom provider)

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

`setup_hermes.py` does this for you (idempotent, with backup + rollback):

```bash
python3 setup_hermes.py           # inject provider into ~/.hermes/config.yaml
python3 setup_hermes.py --dry-run # preview
```

---

## Credit protection (the whole point)

Three independent guards, so you never accidentally spend credits:

1. **NEVER_ALLOW list** — `default-model` (the "Auto" router) is permanently banned,
   because it can silently route to paid models while reporting empty credits.
2. **Dynamic allow-list** — models are read from the CLI's own model catalogue and
   accepted only if `_meta.credits == "x0.00"`.
3. **Request-time guard** — any model outside the allow-list gets `HTTP 403` before the
   CLI is ever invoked.

`wb_watch.py` snapshots the catalogue so you notice when a free model stops being free:

```bash
python3 wb_watch.py --once     # diff against last snapshot
```

---

## How the tool-call bridge works

The CLI does not emit native `tool_calls`, so the bridge defines its own contract and
parses it back. Real models are messy — **seven distinct output shapes** have been
observed in the wild:

| # | Shape | Handling |
|---|---|---|
| 1 | `{"name": "x", "arguments": {...}}` | primary path |
| 2 | `{"function": {"name": ..., "arguments": ...}}` | OpenAI-native form |
| 3 | `<｜｜DSML｜｜ calls> <｜｜DSML｜｜ invoke name="x">` | normalised, then matched |
| 4 | DSML mixed shorthands | `_normalize_tags()` first |
| 5 | *prose first*, then JSON | **full-text scan** (not just the prefix) |
| 6 | **bare argument object** — `{"command": "ls"}` (missing `name`) | infer tool from required-arg coverage + ≥50% key match; refuse if ambiguous |
| 7 | marker split across stream chunks (`{"name`) | hold back the tail until resolved |

Two important lessons baked into the code:

- **`--tools ""` does not stop the model from *thinking* it has built-in tools.** It will
  happily emit `<｜｜DSML｜｜ invoke name="Write">` and waste a whole round. The system prompt
  therefore states explicitly: *you have no built-in tools; only this JSON contract is valid.*
- **Streaming removes the "post-hoc cleanup" safety net.** Non-streaming can strip JSON
  fragments from the final text; streaming cannot un-send bytes. So filtering happens in
  two tiers — hard markers (never re-sent) vs. soft signals (held, then re-sent if no tool
  call was parsed) — plus a watchdog that releases held content after 4 KB.

---

## Performance (measured, not marketing)

Same machine, same task, `deepseek-v4.1-flash`:

| | DeepSeek official API | This bridge |
|---|---|---|
| Small tool call — TTFT | 0.80 s | 3.20 s |
| 400-char document — TTFT | 3.49 s | 5.78 s |
| Generation speed | 385 chars/s | 320 chars/s |
| **Per-round fixed cost** | — | **+2.4 s** |

**Where the 2.4 s goes:** process spawn + CLI init + one extra network hop
(`localhost → codebuddy.ai → model`). It is architectural and cannot be removed without
upstream changes. Things that were tried and **made it worse**:

| Attempt | Result |
|---|---|
| Persistent `--input-format stream-json` process | 1.5 s/round, but **unusable**: context accumulates, `/clear` falls back to on-disk "memory" files, and tool-disabling stops working |
| `--serve` + `api/v1/llm/completions` | Endpoint exists, but upstream returns `400 request illegal` (needs GUI session context) |
| Truncating long tool outputs | **3× slower** — the model sees "…10,000 chars omitted…" and burns thinking tokens trying to recover them |
| `--effort minimal` / `low` | **2–3× slower** than the default (default emits no thinking tokens at all) |
| Switching to `hy3` | 2× slower than `deepseek-v4.1-flash` |

**The dominant variable is the model's own thinking budget, which is stochastic:**
six identical runs took 6.5 / 9.4 / 14.5 / 15.6 / 16.9 / 18.6 s (thinking: 1.7 K–7.2 K chars).
If you need predictable latency, use a paid API directly.

---

## Endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | status + free-model allow-list |
| GET | `/v1/models` | OpenAI model list (allow-list only) |
| POST | `/v1/chat/completions` | `stream` supported (SSE), tools supported |

Streaming emits `reasoning_content` deltas for the model's thinking (80 ms throttled).
`to_prompt()` never reads that field, so thinking is displayed but **never fed back** —
no context bloat, no reasoning-collapse loops.

---

## Known pitfalls

| Symptom | Cause | Fix |
|---|---|---|
| `找不到 codebuddy` / all requests fail | npm auto-update died between "create temp symlink" and "swap" | bridge self-heals the symlink on every call (`_heal_codebuddy_link`) |
| Dialogue shows raw JSON/DSML | model emitted a marker shape the filter didn't know | see the 7-shape table; add a rule |
| Replies stop mid-task | marker parsed as nothing → `tool_calls=0` | bare-arg inference (shape #6) |
| Long document, no output for minutes | soft-signal hold with no watchdog | watchdog releases after 4 KB |
| `empty content` errors | CLI exited 0 with no output | one hinted retry, then a real error instead of silence |

---

## 中文说明

完整中文文档见 **[README.zh-CN.md](README.zh-CN.md)**（独立成篇，与本文等长）。

**完整免责声明见 [DISCLAIMER.md](DISCLAIMER.md)** —— 使用前请务必阅读。

## Credits

Design decisions informed by studying several open-source protocol bridges
(muse-bridge, NanoProxy, acpbox, sample-acp-bridge) — the retry, keep-alive,
hold-back-the-tail, and "soft signal re-send" patterns are borrowed ideas, reimplemented
here for a CLI that emits text instead of native tool calls.

## License

[MIT](LICENSE) © 2026 xbmaxx
