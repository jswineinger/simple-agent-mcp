# MCP Lab — Agent + MCP Server: Architecture & Feature Synopsis
*Design-reference document. Written for a Claude.ai Project's knowledge base to support planning new features — not an ops/deployment runbook. (A separate local-only ops runbook with deployment-specific details exists but isn't part of this public repo.)*

*Last written: 2026-08. Update when architecture or feature set changes materially.*

---

## 1. What this project is

A security-testing lab and demo harness built around a Flask agent with MCP (Model Context Protocol) tool-calling. Its core purpose is **comparing LLM behavior with and without an AI Proxy — a network AI-security gateway — sitting in front of the model**, to evaluate the proxy's prompt-injection, DLP, and tool-call scanning. The agent supports four routing modes so the same prompt can be run through the AI Proxy or straight to the model as a control.

Everything in the app is designed around that comparison: identical UI and identical MCP tools regardless of mode, a mode/flow toggle instead of separate apps, and a system-prompt toggle so prompt-level defenses can be isolated from gateway-level defenses.

---

## 2. High-level architecture

```
┌────────────────────────────────────┐        ┌──────────────────────────────┐
│  Agent (Flask, app.py)              │ JSON-  │  mcphost                     │
│                                      │ RPC/   │  mcp_server.py (MCP server)  │
│  ┌────────────────────────────────┐ │ HTTP   │  9 tools (see §5)            │
│  │ MCPRegistry                     │ │ Bearer │                              │
│  │  ├─ HTTPMCPClient  → mcphost    │ │ auth   │                              │
│  │  └─ HTTPMCPClient  → dlptest    │ │──────▶ │  Ollama / qwen2.5:14b        │
│  └────────────────────────────────┘ │◀────── │                              │
│         │ HTTPS (JSON-RPC 2.0)       │        └──────────────────────────────┘
│         ▼                            │
│  ┌──────────────┐  public MCP server │
│  │  dlptest.com  │  (DLP test tools) │
│  └──────────────┘                    │
│                                      │
│  doc_reader.py (upload extraction)  │
│                                      │
│  Direct Private ─────────────────┐  │
│  Direct Public  ─────────────────┼──┼───────▶ api.anthropic.com (Claude)
│  AI Proxy Private ────────────────┤  │
│  AI Proxy Public  ────────────────┘  │
└──────────────────────────────────────┘
                    │
                    ▼
        ┌───────────────────────────────┐
        │  AI Proxy                      │
        │  /v1/private/chat/completions  │
        │  /v1/public/chat/completions   │──▶ Ollama / qwen2.5:14b (both flows
        └───────────────────────────────┘     currently forward to the same
                                               upstream in this lab)
```

**Key architectural fact for anyone designing new features:** the MCP tool calls (agent → mcphost via bearer-authenticated HTTP JSON-RPC, agent → dlptest via HTTPS) happen **directly, never through the AI Proxy.** The AI Proxy only ever sits in the LLM request/response leg. It only "sees" a tool's *result* indirectly, and only in AI-Proxy modes, because the result gets re-injected into `messages[]` on the *next* LLM call and the proxy re-scans the full resent history. Tool-call *arguments* going out to a tool are never inspected by the AI Proxy in this architecture — a real gap worth knowing about if you're designing anything that assumes gateway coverage of tool calls.

---

## 3. Routing matrix

Two independent UI toggles — **Mode** (Direct / AI Proxy) and **Flow** (Private / Public) — give four combinations:

| Mode | Flow | Endpoint | Model | Auth to that endpoint |
|------|------|----------|-------|------------------------|
| Direct | Private | Ollama on mcphost, no proxy | qwen2.5:14b | `API_KEY` (bearer) |
| Direct | Public | Anthropic API, no proxy | claude-sonnet-4-6 | `ANTHROPIC_API_KEY` |
| AI Proxy | Private | AI Proxy `/private/chat/completions` → Ollama | qwen2.5:14b | `API_KEY` upstream-placeholder + AI Proxy validation key (see §6.6) |
| AI Proxy | Public | AI Proxy `/public/chat/completions` → Ollama | qwen2.5:14b | same as above |

Note the AI Proxy's "Public" flow currently forwards to the same Ollama backend as "Private" in this lab — "Public"/"Private" is an AI-Proxy-side AI-Flow/policy distinction (e.g. different scanner sets), not a different upstream model. Direct+Public is the only combination that talks to Claude.

Model/auth selection logic lives in `chat()` in `agent/app.py`; the four-way fork is the one piece of routing logic every new feature has to stay compatible with.

---

## 4. Inputs & outputs

### Inputs (client → `/api/chat`)
- `messages` — full conversation history, resent every turn (no server-side session store)
- `mode` — `"direct" | "aiproxy"`
- `flow` — `"private" | "public"`
- `system_prompt` — `"off" | "standard" | "defensive"` (key only; prompt text is resolved server-side, never sent by the client — see §6.3)
- Optional file upload (multipart) — `.txt`/`.md`/`.pdf`/`.docx`, extracted server-side (see §6.2)

### Outputs (`/api/chat` JSON response)
One of four shapes, discriminated by the UI on `blocked` / `validation_failed` / `error` / default-success:

| Shape | Trigger | Key fields |
|-------|---------|-----------|
| Success | Normal completion | `ok:true, content, usage, tools_used, latency_ms, doc_ref` |
| Blocked | AI Proxy scanner verdict (`finish_reason=="content_filter"` or a block-range HTTP status) | `ok:false, blocked:true, status, content` |
| Validation failed | AI Proxy API Key Validation rejected the request (401, or 403 without scanner-verdict language) | `ok:false, validation_failed:true, status, content` |
| Error | Network failure, malformed response, or any other exception | `ok:false, error:true, content` |

### Other outputs
- **`/api/config`** — exposes model names, MCP availability/tool list, AI Proxy endpoint URLs, and AI Proxy validation posture (`enabled`/`header` only — never the key)
- **`debug_json.log`** (dev-only, off by default) — JSONL trace of every outbound request body and raw inbound response, correlated by `request_id`, plus tool-call/tool-result records. See §6.5.
- MCP tool calls out to mcphost/dlptest, and their results back (server-side only; never transit the browser directly — they ride back inside the assistant's next turn)

---

## 5. MCP tool inventory

Two backends, fronted by `MCPRegistry` (`agent/mcp_registry.py`), which namespaces every tool `"<server>__<tool>"` so the two backends can never collide and `tools_used` in the response shows which backend actually served a call.

### mcphost backend (JSON-RPC 2.0 over HTTP, bearer auth → `mcp_server.py`)

| Tool | Purpose | Category |
|------|---------|----------|
| `get_system_info` | Hostname, OS, uptime, memory | Ops/status |
| `list_ollama_models` | Models loaded in Ollama | Ops/status |
| `run_ping` | Ping a lab host | Ops/status |
| `fetch_url` | Fetches a lab-hosted page (SSRF-restricted to lab CIDRs, no redirects followed), returns stripped text | Indirect-injection test — honest tool, attacker-controlled *external* data |
| `check_service_health` | Returns a health report whose remediation text embeds a **keyword-free** instruction to ping a host | Indirect-injection test — tests whether the model acts on unrequested tool-embedded instructions |
| `get_field_injection_record` | Returns a structured record with a plausible-field injection payload | Structured-data injection test (Path A/B matrix) |
| `check_lab_license` | Always returns a keyword-laden prompt-injection payload in the result | Demo: trips the AI Proxy's `tools/response` scanner reliably |
| `lookup_record` | Stub record lookup; demo chip passes PII (SSN/card) in the query arg | Demo: trips the AI Proxy's `tools/call` DLP scanner (blocks before execution) |
| `move_money` | Safe-by-construction demo sink — no DB/file/network/state, just returns a formatted confirmation string | Gives the indirect-injection chain a consequence obvious to a non-technical audience |

All tool *definitions* are clean (no injection content in the schema itself) so `tools/list` scanning never blocks the tool catalog — only specific demo calls trip specific scanners, by design, so the block is attributable and repeatable.

### dlptest backend (HTTPS JSON-RPC 2.0 → public `mcp.dlptest.com` server)
Toggle: `DLPTEST_ENABLED` (default on). Tools are **not defined in this repo** — discovered live via `tools/list` against the public dlptest MCP server at connect time (`agent/mcp_http_client.py`). Known tools used in testing include `echo_sensitive_data` (payload rides the agent→dlptest call args, which the AI Proxy never sees in this architecture — only the echoed *result* on the next AI-Proxy-mode turn) and `generate_prompt_context` (returns PII embedded in prose, RAG/context-style — pairs with the doc-upload feature and any future URL/RAG-style vectors).

---

## 6. Features currently implemented

### 6.1 — Four-mode chat routing + AI Proxy block detection
Core routing described in §3. Block detection (`looks_blocked()` in `app.py`) is **scoped to AI-Proxy mode only** — Direct mode can never be "blocked by the AI Proxy," so it's excluded outright regardless of response content. The primary signal is structured: the AI Proxy returns HTTP 200 with `finish_reason == "content_filter"` on the blocked choice, not a keyword scan of the message body. (An earlier keyword-based version false-positived on ordinary responses that happened to mention words like "policy" or "denied.") A small set of HTTP status codes (400/403/406/409/413/422/429/451) is kept as a secondary signal, also AI-Proxy-scoped.

### 6.2 — Document/RAG upload reader
Upload via the composer's 📎 icon. Supports `.txt`/`.md` (plain), `.pdf` (PyMuPDF), `.docx` (python-docx, incl. table cells) — `agent/doc_reader.py`. Extraction happens inside `/api/chat` before the mode/flow fork, so uploaded content rides the same AI-Proxy-inspected path as typed text.

**Trust-boundary design worth carrying into new features:** the extracted/spliced text is *never* returned to the browser. It's stored server-side in an in-memory dict keyed by a random id; the client only ever holds an opaque `[[doc-upload:<id>]]` placeholder in its resent history. The server resolves the placeholder back to real text on every later turn, before the mode/flow fork. This closes the gap where extracted content would otherwise transit the browser as visible JSON on every turn. Any new feature that injects server-held content into the conversation should follow this same placeholder pattern rather than round-tripping raw content through the client.

Large docs truncate at ~24,000 chars (tuned for qwen's ~32k-token window), with truncation state surfaced in the UI.

### 6.3 — System prompt toggle (OFF / STANDARD / DEFENSIVE)
A three-way chip toggle, treated as a **controlled experimental variable** rather than a persistent setting — it's a per-request field, resolved server-side from a key (`_resolve_system_prompt`), and the client never sends or sees prompt text. Any client-supplied `role:"system"` message in resent history is stripped before forwarding. An unknown/missing key fails closed to `standard` (never silently sends no prompt).

- **OFF** — single line, `"Always reply in English."` (not a true no-system-prompt baseline anymore — see prompt text in `app.py`'s `SYSTEM_PROMPTS` dict for the historical note)
- **STANDARD** — injection-naive helpful-assistant prompt, represents an unhardened deployment
- **DEFENSIVE** — STANDARD plus explicit instruction that tool/document content is untrusted data, never to be treated as instructions

### 6.4 — Demo prompt chips
Eight one-click prompts in the UI: four security-behavior probes (benign question, prompt injection, extract-secret, generate-PII) plus a Spanish-language benign variant, and three MCP-triggering prompts (system info, list models, poisoned-license-result).

### 6.5 — DEBUG_JSON raw wire-format logging (dev-only)
Off by default (`DEBUG_JSON = False` in `app.py`). When on, writes JSONL to `agent/debug_json.log` — full outbound request body (messages/tools/model exactly as sent), raw inbound response (status/body, before any parsing), and tool-call/tool-result records, all correlated by a per-turn `request_id`. Headers are deliberately never logged (see §6.6 — keeps the AI Proxy validation key out of any log file). Gitignored; local-only; not for sharing outside the lab.

### 6.6 — AI Proxy API Key Validation support (most recent addition)
The AI Proxy has a per-AI-Flow setting, independent of the upstream LLM key, that validates a header (default `Authorization`, or a configurable custom header) against its own **Settings > API Keys** list before forwarding a request. The agent now attaches a proxy-specific key (`AI_PROXY_VALIDATION_KEY`, default `123456`) on every AI-Proxy-mode request (both flows, every tool-loop iteration), configurable via `AI_PROXY_VALIDATION_ENABLED` / `AI_PROXY_VALIDATION_KEY` / `AI_PROXY_VALIDATION_HEADER`. Direct modes are completely untouched — this only ever fires when `mode == "aiproxy"`.

A dedicated `ValidationKeyError` distinguishes "the AI Proxy rejected the key" (HTTP 401, or 403 without scanner-verdict language in the body) from "the AI Proxy's content scanner blocked this" (HTTP 403 with scanner-verdict language, or the `content_filter` signal from §6.1) — surfaced to the UI as a distinct `validation_failed` response shape rather than being lumped in with content blocks. The key itself is never exposed via `/api/config`, the startup log banner, or `debug_json.log`.

---

## 7. Architectural constraints relevant to future design

These aren't bugs — they're deliberate simplicity trade-offs for a lab — but they matter when scoping new features:

- **No server-side session/conversation store.** The client resends the full message history every turn; the server is otherwise stateless per request (except the in-memory `DOC_SPLICES` dict, which has no eviction and won't survive a restart or scale past one process).
- **No multi-user/auth model.** Single shared agent instance, no per-user identity, no isolation between concurrent users beyond the per-request `request_id`.
- **No streaming.** Every response is a single blocking JSON reply; the UI shows a "thinking" dots indicator, not token-by-token output.
- **MCP transport is synchronous, per-call HTTP POST** to mcphost (a fresh `requests.post` per tool call, no connection pooling/keep-alive tuning) — no SSH handshake cost anymore, but still one blocking round-trip per call; fine for a lab, would need a persistent-connection/async redesign to scale further.
- **mcphost's only auth is a single shared bearer token** (`MCP_AUTH_TOKEN`, checked in `mcp_server.py` before any method dispatch) — no per-client identity, no rotation mechanism, sent in cleartext unless the transport is upgraded to HTTPS. Adequate for a single-agent lab; a real multi-client deployment would want per-client tokens and TLS.
- **AI Proxy scanner latency vs. ingress timeout** is a live tension: heavier AI Guard scanner sets on tool-laden requests can exceed the proxy's ingress `proxy_read_timeout` (60s default), producing a 504 that has nothing to do with the LLM itself. Any feature that adds more round-trips per turn (more tools, bigger context) makes this worse.
- **Tool calls never traverse the AI Proxy directly** (see §2) — a structural gap, not a bug, but central to any new tool-related feature or scanning-coverage discussion.

---

## 8. Natural next-enhancement seeds
*Not commitments — starting points for a design conversation.*

- Tool-call **provenance** signaling (was a call user-requested vs. induced by retrieved content?) — the most serious gap identified in prior testing of the tool-call blind spot described in §2.
- Session/conversation persistence beyond full-history resend (would also bound `DOC_SPLICES` growth).
- Streaming responses.
- Per-tool or per-rule scanner exclusions on the AI Proxy side (current gap: all-or-nothing `tools/list` scanning).
- Extending AI Proxy API Key Validation coverage/testing to per-flow key rotation scenarios now that the agent supports it.
- Additional AI Guard scanner coverage (e.g. an Anthropic-backed AI Guard provider, previously explored, not yet landed).
