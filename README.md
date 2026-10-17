# cli2openai

**Expose the CodeBuddy CLI as an OpenAI-compatible API — so your existing OpenAI client can drive it.**

**English** | [中文](README.zh-CN.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![tests](https://github.com/xbmaxx/cli2openai/actions/workflows/tests.yml/badge.svg)](https://github.com/xbmaxx/cli2openai/actions/workflows/tests.yml)

> ⚠️ **Read [DISCLAIMER.md](DISCLAIMER.md) first.** This project is an *interoperability
> adapter*: it only drives the officially distributed CLI through its documented
> non-interactive mode. **It is not affiliated with, endorsed by, or sponsored by the
> upstream vendor.** You are responsible for complying with the applicable terms.

---

## The problem

The CodeBuddy CLI is a capable coding agent with its own tools, permissions and session
handling. But it only speaks **its own protocol**.

Every mainstream client — Hermes Agent, Open WebUI, Continue, LiteLLM, LangChain,
your own scripts — expects the OpenAI shape: `POST /v1/chat/completions`, SSE streaming,
`tool_calls` in the response. The CLI speaks none of that, so the CLI simply cannot be
plugged into those clients.

That is the entire problem this project solves.

## The solution

A local bridge that sits between them:

```
your client ──(OpenAI /v1/chat/completions)──▶ cli2openai.py ──(official CLI, -p mode)──▶ CodeBuddy
     ▲                                              │
     └───────── SSE + native tool_calls ◀───────────┘
                (parsed back from the CLI's text output)
```

Three responsibilities:

1. **Accept** OpenAI-format requests (messages, tools, `tool_choice`, streaming).
2. **Drive** the officially distributed CLI in its documented non-interactive mode.
3. **Translate back** — the CLI emits text and markup, not `tool_calls`; the bridge parses
   that into native `tool_calls` so multi-turn tool use closes the loop properly.

Everything you already have keeps working: your client, your prompts, your tool loop,
your session history.

## What it is / isn't

| | |
|---|---|
| ✅ **Is** a local protocol adapter (OpenAI ⇄ CLI) | ❌ **Is not** a reverse-engineered client |
| ✅ **Is** built on the officially distributed, MIT-licensed `@tencent-ai/codebuddy-code` | ❌ **Is not** a credential extractor or account pool |
| ✅ **Is** per-user: each user drives their own installed CLI and own session | ❌ **Is not** a relay, proxy, or hosted service |
| ✅ **Is** transparent: no bundled binaries, no patched upstream | ❌ **Is not** affiliated with or endorsed by the vendor |

**This repository contains no vendor code and bundles no binaries.**

---

## Requirements

- macOS or Windows (Linux expected to work; not thoroughly tested)
- Python 3.8+
- Node.js + the official CLI installed and signed in:

```bash
npm install -g @tencent-ai/codebuddy-code
codebuddy              # interactive mode → sign in
```

## Quick start

```bash
git clone https://github.com/xbmaxx/cli2openai.git
cd cli2openai

# macOS / Linux
./install.sh                # installs a launchd service on :8790
./verify.sh                 # 18 local checks

# Windows (PowerShell)
.\install.ps1
```

Or run it in the foreground:

```bash
python3 cli2openai.py        # → http://127.0.0.1:8790/v1
```

```bash
curl -s http://127.0.0.1:8790/health
# {"ok": true, "cli": "/path/to/codebuddy", "models": ["..."]}
```

## Use it from any OpenAI client

Point your client at `http://127.0.0.1:8790/v1` with any non-empty API key, and pick a
model from `/v1/models`.

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8790/v1", api_key="not-needed")
print(client.chat.completions.create(
    model="deepseek-v4.1-flash",
    messages=[{"role": "user", "content": "hello"}],
).choices[0].message.content)
```

Streaming and tool calls work the same way they do against a real OpenAI endpoint:

```python
stream = client.chat.completions.create(
    model="deepseek-v4.1-flash",
    messages=[{"role": "user", "content": "list the files in /tmp"}],
    tools=[{
        "type": "function",
        "function": {
            "name": "terminal",
            "description": "run a shell command",
            "parameters": {"type": "object",
                           "properties": {"command": {"type": "string"}},
                           "required": ["command"]},
        },
    }],
    stream=True,
)
```

### Hermes Agent (custom provider)

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

`setup_hermes.py` performs the edit for you (idempotent, with backup and rollback):

```bash
python3 setup_hermes.py           # inject provider into ~/.hermes/config.yaml
python3 setup_hermes.py --dry-run # preview only
```

## Model discovery

`/v1/models` is **synchronised from the CLI's own catalogue** — the bridge asks the CLI
which models the current session can call, and exposes exactly that list.

Two deliberate exclusions:

- **Router entries** (`default-model`) are filtered out. It is not a concrete model — it is
  an automatic router, so its behaviour is not reproducible and it has no fixed context
  window. A bridge should expose deterministic targets only.
- **Unknown names** are rejected with `404`, so a typo cannot silently fall through to
  something unexpected.

The catalogue is cached on disk; run `model_watch.py` to refresh and diff it after a CLI
upgrade.

---

## How tool calls are bridged

This is the non-trivial part. The CLI does not emit native `tool_calls`, so the bridge
defines a JSON contract in the system prompt and parses it back. Real models are messy —
**seven distinct output shapes** have been observed:

| # | Shape | Handling |
|---|---|---|
| 1 | `{"name": "x", "arguments": {...}}` | primary contract |
| 2 | `{"function": {"name": ..., "arguments": ...}}` | OpenAI-native form |
| 3 | `<｜｜DSML｜｜ calls> <｜｜DSML｜｜ invoke name="x">` | normalised, then matched |
| 4 | DSML with mixed shorthands | `_normalize_tags()` first |
| 5 | **prose first, then JSON** | **full-text scan** (a prefix check misses it) |
| 6 | **bare argument object** — `{"command": "ls"}` (no `name`) | infer the tool from required-argument coverage + ≥50 % key match; refuse when ambiguous |
| 7 | marker split across stream chunks (`{"name`) | hold the tail back until it resolves |

Two lessons baked into the code:

- **`--tools ""` does not stop the model from *believing* it has built-in tools.** It will
  emit `<｜｜DSML｜｜ invoke name="Write">` and burn an entire round for nothing. The system
  prompt therefore states explicitly: *you have no built-in tools; this JSON contract is
  the only valid call format.*
- **Streaming removes the "clean up afterwards" safety net.** A non-streaming path can strip
  JSON fragments from the final text; a streaming path cannot un-send bytes. So filtering is
  two-tier — hard markers (never re-sent) vs. soft signals (held, then re-sent if no tool
  call was parsed) — plus a 4 KB watchdog that releases held content.

---

## Performance (measured)

Same machine, same task:

| | Direct API call | Through this bridge |
|---|---|---|
| Small tool call — first token | 0.80 s | 3.20 s |
| 400-char document — first token | 3.49 s | 5.78 s |
| Generation speed | 385 chars/s | 320 chars/s |
| **Per-round fixed cost** | — | **+2.4 s** |

**Where the 2.4 s goes:** process spawn + CLI initialisation + one extra network hop
(`localhost → vendor endpoint → model`). It is architectural; it cannot be removed without
upstream changes.

**Approaches that were measured and rejected** (so you don't repeat them):

| Attempt | Result |
|---|---|
| Persistent `--input-format stream-json` process | 1.5 s/round, but **unusable**: context accumulates, `/clear` falls back to on-disk "memory" files, and tool-disabling silently stops working |
| `--serve` + `api/v1/llm/completions` | Endpoint exists, but the upstream rejects it without a GUI session context (`400 request illegal`) |
| Truncating long tool outputs | **3× slower** — the model sees "…10,000 chars omitted…" and spends thinking budget trying to recover them |
| `--effort minimal` / `low` | **2–3× slower** than the default (the default emits no thinking tokens at all) |
| Switching to `hy3` | 2× slower than `deepseek-v4.1-flash` |

**The dominant variable is the model's own thinking budget, which is stochastic:** six
identical runs took 6.5 / 9.4 / 14.5 / 15.6 / 16.9 / 18.6 s (thinking 1.7 K–7.2 K chars).
If you need predictable latency, call a model API directly.

---

## Endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | health + resolved CLI path + model list |
| GET | `/v1/models` | OpenAI model list (synced from the CLI catalogue) |
| POST | `/v1/chat/completions` | `stream` (SSE) and tools supported |

Streaming emits `reasoning_content` deltas for the model's thinking (80 ms throttled).
`to_prompt()` never reads that field, so thinking is **displayed only and never fed back** —
no context bloat, no reasoning-collapse loops.

---

## Known pitfalls

| Symptom | Cause | Fix |
|---|---|---|
| `codebuddy not found` / every request fails | npm auto-update died between "create temp symlink" and "swap" | the bridge self-heals the symlink before each call (`_heal_codebuddy_link`) |
| Raw JSON/DSML appears in the transcript | the model used a marker shape the filter didn't know | see the 7-shape table; add a rule |
| A turn stalls mid-task | a marker parsed into nothing → `tool_calls=0` | bare-argument inference (shape #6) |
| Minutes of silence while writing a long document | soft-signal hold with no watchdog | released automatically after 4 KB |
| `empty content` | CLI exited 0 with no output | one hinted retry, then a concrete error instead of silence |

---

## Acknowledgements

Design decisions were informed by reading several open-source protocol bridges
(muse-bridge, NanoProxy, acpbox, sample-acp-bridge). The retry, keep-alive,
hold-back-the-tail and "soft signal re-send" patterns are borrowed ideas, reimplemented
here for a CLI that emits text instead of native tool calls.

## License

[MIT](LICENSE) © 2026 xbmaxx
