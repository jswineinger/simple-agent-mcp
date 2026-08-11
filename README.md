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
