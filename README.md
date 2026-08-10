# mcp-lab

A learning project demonstrating the Model Context Protocol (MCP) with a
Flask agent, a Python-native MCP client, and an MCP server exposing lab tools.

**Phase 1** — agent talks directly to Ollama (qwen2.5:14b) on mcphost  
**Phase 2** — switch the UI's Mode toggle to route through the AI Proxy

## Lab Inventory

| Role | Host | IP |
|------|------|----|
| Agent + MCP client | agent | 192.168.2.132 |
| MCP server + Ollama | mcphost | 192.168.37.6 |
| AI proxy (Phase 2) | fortiaigate | 192.168.2.131:30443 |

This table reflects this lab's current physical layout. The agent and
mcp-server (and Ollama) don't have to live on separate hosts, or on these
specific IPs — see **Topology** below.

## Architecture

```
+---------------------------+        +----------------------------+
|  agent (192.168.2.132)    |        |  mcphost (192.168.37.6)   |
|                            |  JSON- |                            |
|  Flask app (app.py)       |  RPC/  |  mcp_server.py (Flask)     |
|  MCP client                | HTTP  |  POST /mcp                 |
|    (mcp_http_client.py)   |------->|  Bearer-token auth         |
|                            |<-------|  get_system_info           |
|                            |        |  list_ollama_models        |
|                            |  HTTP  |  run_ping                  |
|                            |------->|                            |
|                            |<-------|                            |
|                            |        |  Ollama / qwen2.5:14b      |
+---------------------------+        +----------------------------+
```

The MCP server is LLM-agnostic — tools work identically whether the agent
is talking to Ollama, Anthropic, or any OpenAI-compatible endpoint. Only the
`OLLAMA_URL` and `API_KEY` in `agent/agent.env` change between backends.

Agent <-> mcp-server speaks real JSON-RPC 2.0 MCP over HTTP — the same wire
format and the same `HTTPMCPClient` class used for the public dlptest
backend, just a different URL and token. Every request to mcp-server must
carry `Authorization: Bearer <MCP_AUTH_TOKEN>`, checked in `mcp_server.py`
before anything else runs. `setup-mcphost.sh` generates that token; copy it
into `MCP_AUTH_TOKEN` in `agent.env` on the agent VM (`setup-agent-vm.sh`
prompts for it).

### Topology

Both `.env.example` files declare the same three variables — `AGENT_IP`,
`MCPSERVER_IP`, `OLLAMA_URL` — describing where each of the three
components lives. Defaults are all `127.0.0.1`: clone the repo onto one box,
start both services, and they find each other over loopback with zero
editing. Each app only *uses* the vars relevant to its own outbound
connections (e.g. mcp-server doesn't call the agent back, so `AGENT_IP` in
`mcp-server.env` is informational only) — every var is still declared in
both files so either one shows the full topology at a glance.

To split across separate hosts — including this lab's actual layout in the
table above — replace `127.0.0.1` with the real IP for whichever component
moved, **in both `.env` files**:

```bash
# agent/agent.env
MCPSERVER_IP=192.168.37.6
OLLAMA_URL=http://192.168.37.6:11434

# mcp-server/mcp-server.env
OLLAMA_URL=http://192.168.37.6:11434
```

No code changes either way.

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

The repo is public, so the setup scripts just clone it over plain HTTPS —
no GitHub auth needed. Bootstrap by downloading the script directly — the
repo doesn't need to exist locally yet. Run each script as the regular
(non-root) user you want the service to run as; it `sudo`s only the specific
steps that need it. That user becomes `INSTALL_USER` in the installed
systemd unit — override it (and `INSTALL_HOME`) if you want the service to
run as a different, already-existing user than the one running the script.

### 1 — mcphost (do this first)

```bash
curl -fsSL https://raw.githubusercontent.com/jswineinger/simple-agent-mcp/main/scripts/setup-mcphost.sh \
  -o setup-mcphost.sh && bash setup-mcphost.sh
```

The script will:
- Clone the repo to `~/mcp-lab`
- Create the Python venv
- Generate `mcp-server.env` with a random bearer token and pause so you can
  copy it — you'll paste this into `agent.env` in step 2
- Install and start the `mcp-server` systemd service

Self-test after setup (replace `TOKEN` with the value from `mcp-server.env`):
```bash
curl -s http://localhost:8765/mcp \
  -H "Authorization: Bearer TOKEN" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

### 2 — agent VM (192.168.2.132)

```bash
curl -fsSL https://raw.githubusercontent.com/jswineinger/simple-agent-mcp/main/scripts/setup-agent-vm.sh \
  -o setup-agent-vm.sh && bash setup-agent-vm.sh
```

The script will:
- Clone the repo to `~/mcp-lab`
- Create the Python venv
- Set up `agent.env` and prompt you to paste in the `MCP_AUTH_TOKEN`
  generated by `setup-mcphost.sh`
- Install and enable the `llm-agent` systemd service

```bash
sudo systemctl start llm-agent
```

Browse to `http://<agent-host>:8000` — the script prints the exact URL
(detected from the box's own network interface) at the end.

### 3 — Test tool calls

| Prompt | Tool fired |
|--------|-----------|
| "What's the system status on mcphost?" | `get_system_info` |
| "What models are available in Ollama?" | `list_ollama_models` |
| "Ping the AI Proxy at 192.168.2.131" | `run_ping` |

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
