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

Its sole purpose is to bridge a protocol gap: the CLI speaks its own protocol, OpenAI-style
clients expect a different one. Nothing more.

The CLI is installed by the user from the public npm registry
(`@tencent-ai/codebuddy-code`), which is distributed under an **MIT-style licence**.
This repository contains **no Tencent code** and **no bundled binaries** — it only
contains our own Python adapter.

本项目是一个**协议适配器**：把官方 CLI 的输出转换成 OpenAI 格式，让支持 OpenAI 协议的客户端
能够驱动该 CLI。它只解决协议不通的问题。

仓库内**不含任何腾讯代码或二进制**，只包含本项目自己的 Python 代码。

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
| Granting entitlements the user's account does not already have | ❌ Not possible |

Every user runs the bridge **against their own locally installed CLI and their own
logged-in account**. There is no central relay, no shared credential pool, and no traffic
proxying service operated by this project.

本项目**不做**逆向、不提取或存储凭据、不伪造指纹/设备 ID、不绕过认证、不共享账号、不修改或
再分发官方二进制、也不会产生用户账号本身不具备的权限。每个使用者都只针对**自己本机安装的
CLI 和自己的账号**运行。

---

## 4. Your responsibility / 使用者责任

By using this software you acknowledge and agree that:

1. **You are solely responsible** for how you use it, and for complying with the vendor's
   terms of service, acceptable-use policy, and any usage rules that apply to your account.
2. **Model availability and account entitlements are decided by the vendor**, not by this
   project. The set of models your session can call may change, be limited, or be withdrawn
   at any time without notice. This project does not, and cannot, guarantee the availability
   of any model.
3. **This project is not a billing workaround.** It exists purely for protocol
   interoperability — so an OpenAI-compatible client can drive a CLI that speaks a
   different protocol. If your use case requires an entitlement, obtain it through the
   vendor's normal channels.
4. You will not use this software to build a paid relay, resale service, or
   credential-sharing service.
5. You will stop using it if the vendor asks you to.

---

## 5. No warranty / 无担保

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF
CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE
OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

This includes — without limitation — **account suspension, entitlement loss, data loss,
service interruption, or any action taken by the vendor against your account**.
You accept that risk entirely.

---

## 6. Takedown / 下架承诺

If you are a rights holder and believe this repository infringes your rights or
violates your terms, **open an issue or contact the maintainer directly**. We will
respond promptly and, where a legitimate concern is raised, **remove or modify the
offending material without argument**.

If the vendor (or any authorised party) requests removal, this repository will be taken
down or made private immediately.

---

## 7. Jurisdiction / 适用说明

This project is published for **educational and interoperability research purposes**.
Laws and platform terms differ by jurisdiction; nothing here constitutes legal advice.
If you are unsure whether your use is permitted, **do not use it**.

---

*Last updated: 2026-10-17*
