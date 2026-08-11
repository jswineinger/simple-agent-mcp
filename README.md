# mcp-lab

A learning project demonstrating the Model Context Protocol (MCP) with a
Flask agent, a Python-native MCP client, and an MCP server exposing lab tools.

**Phase 1** — agent talks directly to Ollama (qwen2.5:14b) on mcphost  
**Phase 2** — switch the UI's Mode toggle to route through the AI Proxy

## Lab Inventory

Four roles make up the lab:

- **Agent + MCP client** — the Flask app (`agent/`)
- **MCP server** — exposes lab tools over JSON-RPC/HTTP (`mcp-server/`)
- **Ollama** — the local LLM (not part of this repo)
- **AI Proxy** — the network AI-security gateway (Phase 2)

The AI Proxy is always a separate host. The other three can run on one box,
split across several, or any mix in between — nothing in the code assumes a
particular grouping. See **Topology** below.

## Topology

Both `.env.example` files declare the same three variables — `AGENT_IP`,
`MCPSERVER_IP`, `OLLAMA_URL` — describing where each of the three
components lives. Defaults are all `127.0.0.1`: clone the repo onto one box,
start both services, and they find each other over loopback with zero
editing. Each app only *uses* the vars relevant to its own outbound
connections (e.g. mcp-server doesn't call the agent back, so `AGENT_IP` in
`mcp-server.env` is informational only) — every var is still declared in
both files so either one shows the full topology at a glance.

To split across separate hosts, replace `127.0.0.1` with the real IP for
whichever component moved, **in both `.env` files**:

```bash
# agent/agent.env
MCPSERVER_IP=<mcp-server-ip>
OLLAMA_URL=http://<ollama-ip>:11434

# mcp-server/mcp-server.env
OLLAMA_URL=http://<ollama-ip>:11434
```

No code changes either way.

## Architecture

```
+----------------------------------------------------------------------------+
|                             Agent + MCP client                             |
|                              (Flask, app.py)                               |
+----------------------------------------------------------------------------+
     JSON-RPC/HTTP,            HTTP/HTTPS                HTTPS,
       bearer auth              (Direct)              AI-Proxy mode
            |                       |                       |
            v                       v                       v
+------------------------+   +--------------+   +------------------------+
|       MCP server       |   |    Ollama    |   |        AI Proxy        |
|    (mcp_server.py)     |   |              |   |       (always a        |
|                        |   |              |   |     separate host)     |
|       -> Ollama        |   |              |   |                        |
|  (list_ollama_models,  |   |              |   |       -> Ollama        |
|          HTTP)         |   |              |   |  (forwards requests)   |
+------------------------+   +--------------+   +------------------------+
```

Any of these four roles can be the same host or different hosts (except the
AI Proxy, always separate — see **Topology** above). The MCP server is
LLM-agnostic — tools work identically whether the agent is talking to
Ollama, Anthropic, or any OpenAI-compatible endpoint. Only the `OLLAMA_URL`
and `API_KEY` in `agent/agent.env` change between backends.

Agent <-> mcp-server speaks real JSON-RPC 2.0 MCP over HTTP — the same wire
format and the same `HTTPMCPClient` class used for the public dlptest
backend, just a different URL and token. Every request to mcp-server must
carry `Authorization: Bearer <MCP_AUTH_TOKEN>`, checked in `mcp_server.py`
before anything else runs. `setup-mcphost.sh` generates that token; copy it
into `MCP_AUTH_TOKEN` in `agent.env` on the agent VM (`setup-agent-vm.sh`
prompts for it).

## Repository Structure

```
mcp-lab/
├── agent/
│   ├── app.py                  Flask agent with MCP client wired in
│   ├── mcp_http_client.py      JSON-RPC/HTTP MCP client (mcp-server + dlptest)
│   ├── mcp_registry.py         Multi-backend tool router
│   ├── requirements.txt        flask, requests
│   ├── run.sh                  Manual start script
│   ├── agent.env               Environment variables (gitignored)
│   ├── agent.env.example       Safe template to commit
│   └── llm-agent.service       Systemd unit
├── mcp-server/
│   ├── mcp_server.py           MCP server (runs on mcphost, JSON-RPC/HTTP)
│   ├── mcp-server.env          Bearer token (gitignored)
│   ├── mcp-server.env.example  Safe template to commit
│   └── mcp-server.service      Systemd unit
├── scripts/
│   ├── setup-agent-vm.sh      One-time setup for agent VM
│   └── setup-mcphost.sh      One-time setup for mcphost
└── README.md
```

## Features

### Mode (Direct / AI Proxy) and Flow (Private / Public)

Two independent toggles in the UI header, giving four combinations:

| Mode | Flow | Goes to |
|------|------|---------|
| Direct | Private | Ollama, directly |
| Direct | Public | Anthropic, directly |
| AI Proxy | Private | AI Proxy's private flow → Ollama |
| AI Proxy | Public | AI Proxy's public flow → Ollama (same backend as Private in this lab) |

**Mode** decides whether a request is inspected by the AI Proxy on the way
to the model or sent straight there — the whole point of this lab is
comparing those two side by side. **Flow** decides which model family:
Private always means Ollama; Public means Anthropic in Direct mode, or
whatever the AI Proxy's "Public" AI-Flow is configured to forward to (also
Ollama, in this lab). Switching either toggle is a pure UI action — no
`.env` edit or service restart, ever. See **Phase 2** below to try it.

### Public dlptest MCP server

A second MCP backend, off by default (`DLPTEST_ENABLED=false` in
`agent.env`), pointed at the public `mcp.dlptest.com` server. Unlike
`mcphost`'s tools, these aren't defined in this repo — they're discovered
live via `tools/list` against that public server at connect time, and
exposed to the model namespaced as `dlptest__<toolname>`. They generate
realistic-looking PII/DLP payloads (e.g. `echo_sensitive_data`,
`generate_prompt_context`) for testing scanner coverage against a tool-call
path outside this repo's own control. Set `DLPTEST_ENABLED=true` and
restart `llm-agent` to turn it on.

### AI Proxy API Key Validation

A per-AI-Flow setting some AI Proxies support, independent of the upstream
LLM key: it validates a header (default `Authorization`, or a custom one)
against the proxy's own **Settings > API Keys** list before forwarding a
request. Off by default (`AI_PROXY_VALIDATION_ENABLED=false` in
`agent.env`) — turn it on and set `AI_PROXY_VALIDATION_KEY` (default
`123456`) to match your proxy's configured value if your AI Proxy has this
feature enabled on its AI Flow. `AI_PROXY_VALIDATION_HEADER` selects which
header carries the key. Only ever fires in AI Proxy mode; Direct mode is
completely untouched by these three variables.

### Anthropic API key (Direct + Public mode)

Set `ANTHROPIC_API_KEY` in `agent.env`. It's used for exactly one
combination — Direct mode + Public flow — sent as the bearer token against
`PUBLIC_LLM_URL` (default `https://api.anthropic.com/v1`) with model
`PUBLIC_MODEL` (default `claude-sonnet-4-6`). Anthropic is currently the
only Public-flow provider this lab is built/tested against; `PUBLIC_LLM_URL`
and `PUBLIC_MODEL` are configurable if you want to point Direct+Public at a
different OpenAI-compatible endpoint, but that combination isn't tested.

### Attach a file

Click the 📎 icon in the composer to attach a document — `.txt`/`.md`
(plain text), `.pdf` (PyMuPDF), or `.docx` (python-docx, including table
cells). Extraction happens server-side and the text is spliced into your
message as RAG-style context, riding the same Mode/Flow routing (and the
same AI Proxy inspection, in AI Proxy mode) as anything you type. Large
documents truncate at ~24,000 characters, with truncation state shown in
the UI. The extracted text is never sent back to the browser — the client
only ever holds an opaque reference to it, resolved back to real text
server-side on each later turn.

### Shortcut query buttons

Eight one-click prompt chips below the chat log — clicking one sends it
immediately, as if you'd typed and submitted it:

| Chip | Purpose |
|------|---------|
| Benign question | Ordinary question, no security angle — a control |
| Benign question (Spanish) | Same benign control, non-English |
| Prompt injection | Direct "ignore previous instructions" attempt |
| Extract secret | Asks the model to reveal its configuration/keys |
| Generate PII | Asks for fabricated but realistic SSN/card data |
| System info | Triggers the `get_system_info` MCP tool |
| List models | Triggers the `list_ollama_models` MCP tool |
| Poisoned result | Triggers `check_lab_license`, whose result carries a planted injection payload |

## Quick Start

Starting from a clean Ubuntu VM. The repo is public, so the setup scripts
just clone it over plain HTTPS — no GitHub auth needed. Run each script as
the regular (non-root) user you want the service to run as; it `sudo`s only
the specific steps that need it. That user becomes `INSTALL_USER` in the
installed systemd unit — override it (and `INSTALL_HOME`) if you want the
service to run as a different, already-existing user than the one running
the script.

### 1 — Create a user (skip if you already have one)

```bash
sudo adduser labadmin      # follow the prompts
sudo usermod -aG sudo labadmin
```

Log in as that user for the rest of these steps, if you weren't already.

### 2 — Download the setup scripts

Run this from that user's home directory — the scripts install into
`~/mcp-lab` (via `$HOME`) regardless of where you run them from, but
keeping the downloaded scripts there too avoids confusion.

```bash
wget -qO setup-mcphost.sh https://raw.githubusercontent.com/jswineinger/simple-agent-mcp/main/scripts/setup-mcphost.sh
wget -qO setup-agent-vm.sh https://raw.githubusercontent.com/jswineinger/simple-agent-mcp/main/scripts/setup-agent-vm.sh
chmod 755 *.sh
```

### 3 — mcphost (do this first)

```bash
./setup-mcphost.sh
```

The script will:
- Clone the repo to `~/mcp-lab`
- Create the Python venv
- Generate `mcp-server.env` with a random bearer token and pause so you can
  copy it — you'll paste this into `agent.env` in step 4
- Install and **enable** (but not start) the `mcp-server` systemd service

Review/edit `mcp-server.env`:
- `OLLAMA_URL` — point at wherever Ollama actually runs, e.g.
  `http://<ollama-ip>:11434`
- `MCP_SERVER_PORT`, `AGENT_IP`, `MCPSERVER_IP` — only if you're not using
  the single-box defaults (see **Topology** above)

Then start it:
```bash
sudo systemctl start mcp-server
```

Self-test once started (replace `TOKEN` with the value from `mcp-server.env`):
```bash
curl -s http://localhost:8765/mcp \
  -H "Authorization: Bearer TOKEN" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

### 4 — agent VM

```bash
./setup-agent-vm.sh
```

The script will:
- Clone the repo to `~/mcp-lab`
- Create the Python venv
- Set up `agent.env` and prompt you to paste in the `MCP_AUTH_TOKEN`
  generated in step 3
- Install and **enable** (but not start) the `llm-agent` systemd service

Review/edit `agent.env`:
- `OLLAMA_URL` — point at wherever Ollama actually runs, e.g.
  `http://<ollama-ip>:11434`
- `AI_PROXY_URL` — use the AI Proxy's **hostname**, not a bare IP (the proxy
  routes/matches by hostname, e.g. `https://myproxy.lab.local:30443/v1`).
  Add an `/etc/hosts` entry on this box if you don't have real DNS for it.
- **Note:** `AI_PROXY_VALIDATION_ENABLED` defaults to `false`. If your AI
  Proxy has API Key Validation turned on for its AI Flow, set it to `true`
  and change `AI_PROXY_VALIDATION_KEY` (default `123456`) to match.

Then start it, making sure `mcp-server` is already running:
```bash
sudo systemctl start llm-agent
```

Browse to `http://<agent-host>:8000` — the script prints the exact URL
(detected from the box's own network interface) at the end.

### 5 — Test tool calls

| Prompt | Tool fired |
|--------|-----------|
| "What's the system status on mcphost?" | `get_system_info` |
| "What models are available in Ollama?" | `list_ollama_models` |
| "Ping localhost" | `run_ping` |

## Phase 2 — Route Through the AI Proxy

In the running UI, switch the **Mode** toggle from Direct to AI Proxy — no
`.env` edit or restart needed. (`AI_PROXY_URL` in `agent/agent.env`
configures *where* the AI Proxy is; it doesn't need to change to flip modes.)

## Logs

```bash
# Agent VM
sudo journalctl -u llm-agent -f

# mcphost
sudo journalctl -u mcp-server -f
```
