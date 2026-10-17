# Disclaimer / 免责声明

> **Read this before using or forking this project.**
> If anything here is unacceptable to you, do not use this software.

---

## 1. No affiliation / 无关联

This project is **not** affiliated with, endorsed by, sponsored by, or approved by
**Tencent Holdings Limited**, **Tencent Cloud**, **CodeBuddy**, or **WorkBuddy**
(collectively "Tencent").

"CodeBuddy", "WorkBuddy", "Tencent", and related marks are trademarks of Tencent.
They are used here **only** in a descriptive sense (to name the upstream tool this
project interoperates with), under nominative fair use. No trademark licence is granted.

本项目与腾讯公司及其 CodeBuddy / WorkBuddy 产品**没有任何关联**，未获其授权、赞助或认可。
相关名称仅作**描述性使用**（指明所对接的上游工具）。

---

## 2. What this project IS / 本项目是什么

A **protocol adapter**. It exposes a local OpenAI-compatible HTTP endpoint
(`/v1/chat/completions`) and fulfils each request by invoking the **officially
distributed** `codebuddy` CLI in its **documented non-interactive mode**:

```bash
codebuddy -p --tools "" --output-format stream-json "your prompt"
```

The CLI is installed by the user from the public npm registry
(`@tencent-ai/codebuddy-code`), which is distributed under an **MIT-style licence**.
This repository contains **no Tencent code** and **no bundled binaries** — it only
contains our own Python adapter.

本项目是一个**协议适配器**：把官方 CLI 的文本输出转换成 OpenAI 格式，
让任意支持 OpenAI 协议的客户端都能使用该 CLI 已登录的账号。仓库内**不含任何腾讯代码或二进制**。

---

## 3. What this project does NOT do / 本项目不做什么

We deliberately avoid every technique that would cross from "interoperability" into
"circumvention":

| Technique | Status |
|---|---|
| Reverse-engineering the client, deobfuscating bundles | ❌ Not used |
| Extracting, decrypting, or storing credential files / tokens | ❌ Not used |
| Spoofing device fingerprints, IDE headers, or machine IDs | ❌ Not used |
| Forging plugin signatures or bypassing authentication | ❌ Not used |
| Sharing, pooling, or reselling accounts | ❌ Not supported by design |
| Modifying or redistributing Tencent binaries | ❌ Not done |

Every user runs the bridge **against their own locally installed CLI and their own
logged-in account**. There is no central relay, no shared credential pool, and no
traffic proxying service operated by this project.

本项目**不做**逆向、不提取或存储凭据、不伪造指纹/设备 ID、不绕过认证、不共享账号、不修改或再分发官方二进制。
每个使用者都只针对**自己本机安装的 CLI 和自己的账号**运行。

---

## 4. Your responsibility / 使用者责任

By using this software you acknowledge and agree that:

1. **You are solely responsible** for how you use it, and for complying with
   Tencent's Terms of Service, Acceptable Use Policy, and any quota or billing rules
   that apply to your account.
2. **Free-tier model availability is a commercial decision made by Tencent**, not by
   this project. Free models (e.g. models billed at `x0.00` credits) can be changed,
   limited, or withdrawn **at any time without notice**. This project does not, and
   cannot, guarantee that any model stays free.
3. **This is not a way to avoid paying for a paid plan.** If your use case requires a
   paid tier, buy the paid tier. This project exists for interoperability and for
   experimenting with CLI-backed local agents — not for circumventing billing.
4. You will not use this software to build a paid relay, resale service, or
   credential-sharing service.
5. You will stop using it if Tencent asks you to.

---

## 5. No warranty / 无担保

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF
CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE
OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

This includes — without limitation — **account suspension, quota loss, data loss,
service interruption, or any action taken by Tencent against your account**.
You accept that risk entirely.

---

## 6. Takedown / 下架承诺

If you are a rights holder and believe this repository infringes your rights or
violates your terms, **open an issue or contact the maintainer directly**. We will
respond promptly and, where a legitimate concern is raised, **remove or modify the
offending material without argument**.

If Tencent (or any authorised party) requests removal, this repository will be taken
down or made private immediately.

---

## 7. Jurisdiction / 适用说明

This project is published for **educational and interoperability research purposes**.
Laws and platform terms differ by jurisdiction; nothing here constitutes legal advice.
If you are unsure whether your use is permitted, **do not use it**.

---

*Last updated: 2026-10-17*
