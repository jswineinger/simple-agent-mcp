# mcp-lab

A learning project demonstrating the Model Context Protocol (MCP) with a
Flask chatbot, a Python-native MCP client, and an MCP server exposing lab tools.

**Phase 1** — chatbot talks directly to Ollama (qwen2.5:14b) on mcphost  
**Phase 2** — swap one environment variable to route through FortiAIGate

## Lab Inventory

| Role | Host | IP |
|------|------|----|
| Chatbot + MCP client | chatbot | 192.168.2.132 |
| MCP server + Ollama | mcphost | 192.168.37.6 |
| AI proxy (Phase 2) | fortiaigate | 192.168.2.131:30443 |

## Architecture

```
┌─────────────────────────┐        ┌──────────────────────────┐
│  chatbot (192.168.2.132)│        │ mcphost (192.168.37.6)  │
│                         │  SSH   │                          │
│  Flask app (app.py)     │──────▶ │  mcp_server.py (stdio)   │
│  MCP client             │◀──────  │  get_system_info         │
│    (mcp_client.py)      │        │  list_ollama_models       │
│                         │        │  run_ping                 │
│                         │  HTTP  │  get_gpu_status           │
│                         │──────▶ │                          │
│                         │◀──────  │  Ollama / qwen2.5:14b    │
└─────────────────────────┘        └──────────────────────────┘
```

The MCP server is LLM-agnostic — tools work identically whether the chatbot
is talking to Ollama, Anthropic, or any OpenAI-compatible endpoint. Only the
`LLM_URL` and `API_KEY` in `chatbot/chatbot.env` change between backends.

## Repository Structure

```
mcp-lab/
├── chatbot/
│   ├── app.py                  Flask chatbot with MCP client wired in
│   ├── mcp_client.py           Tool-call loop (SSH stdio transport)
│   ├── requirements.txt        flask, requests
│   ├── run.sh                  Manual start script
│   ├── chatbot.env             Environment variables (gitignored)
│   ├── chatbot.env.example     Safe template to commit
│   └── mcp-chatbot.service     Systemd unit
├── mcp-server/
│   ├── mcp_server.py           MCP server (runs on mcphost)
│   └── mcp-server.service      Systemd unit
├── scripts/
│   ├── setup-chatbot-vm.sh     One-time setup for chatbot VM
│   └── setup-mcphost.sh      One-time setup for mcphost
└── README.md
```

## Quick Start

The setup scripts handle everything including GitHub SSH key generation and
repo cloning. Bootstrap by downloading the script directly — the repo doesn't
need to exist yet.

### 1 — mcphost (do this first)

```bash
curl -fsSL https://raw.githubusercontent.com/jswineinger/mcp-lab/main/scripts/setup-mcphost.sh \
  -o setup-mcphost.sh && bash setup-mcphost.sh
```

The script will:
- Generate a GitHub deploy key and pause for you to add it at
  `github.com/jswineinger/mcp-lab/settings/keys`
- Clone the repo to `~/mcp-lab`
- Create the Python venv
- Install and start the `mcp-server` systemd service

Self-test after setup:
```bash
echo '{"method":"tools/list"}' | \
  ~/mcp-lab/mcp-server/venv/bin/python3 ~/mcp-lab/mcp-server/mcp_server.py
```

### 2 — chatbot VM (192.168.2.132)

```bash
curl -fsSL https://raw.githubusercontent.com/jswineinger/mcp-lab/main/scripts/setup-chatbot-vm.sh \
  -o setup-chatbot-vm.sh && bash setup-chatbot-vm.sh
```

The script will:
- Generate a GitHub deploy key (add it to GitHub when prompted)
- Clone the repo to `~/mcp-lab`
- Create the Python venv
- Generate a mcphost SSH key and pause for you to add it to mcphost
- Install and enable the `mcp-chatbot` systemd service

```bash
sudo systemctl start mcp-chatbot
```

Browse to **http://192.168.2.132:8000**

### 3 — Test tool calls

| Prompt | Tool fired |
|--------|-----------|
| "What's the system status on mcphost?" | `get_system_info` |
| "What models are available in Ollama?" | `list_ollama_models` |
| "Ping the FortiAIGate at 192.168.2.131" | `run_ping` |
| "What's the GPU memory usage?" | `get_gpu_status` |

## Phase 2 — Route Through FortiAIGate

Edit `chatbot/chatbot.env`:

```bash
LLM_URL=https://192.168.2.131:30443/v1
VERIFY_TLS=false
```

```bash
sudo systemctl restart mcp-chatbot
```

No code changes. Tools, MCP server, and tool-call loop are unchanged.

## Logs

```bash
# Chatbot VM
sudo journalctl -u mcp-chatbot -f

# mcphost
sudo journalctl -u mcp-server -f
```
