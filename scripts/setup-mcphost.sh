#!/usr/bin/env bash
# setup-mcphost.sh
# Run once on mcphost (192.168.37.6) to install the MCP server.
#
# Bootstrap — this script must be downloaded before the repo exists.
# Grab it directly from GitHub:
#
#   curl -fsSL https://raw.githubusercontent.com/jswineinger/simple-agent-mcp/main/scripts/setup-mcphost.sh \
#     -o setup-mcphost.sh && bash setup-mcphost.sh
#
# What this does:
#   1. Install system packages
#   2. Clone the repo (public repo, plain HTTPS, no auth needed)
#   3. Create Python venv for the MCP server
#   4. Generate mcp-server.env with a bearer-token secret (copy this token
#      into MCP_AUTH_TOKEN in agent.env on the agent VM)
#   5. Install and enable the systemd service (does NOT start it — you
#      start it yourself after reviewing mcp-server.env, see the note
#      printed at the end)
#
# Run this as the regular (non-root) user you want the service to run as —
# sudo is invoked only for the specific steps that need it (packages,
# systemd). INSTALL_USER/INSTALL_HOME default to whoever runs this script;
# override them (e.g. `INSTALL_USER=svc INSTALL_HOME=/home/svc bash ...`)
# if you want the service to run as a different, already-existing user.

set -euo pipefail

GITHUB_USER="jswineinger"
GITHUB_REPO="simple-agent-mcp"                # actual GitHub repo slug
LOCAL_DIR_NAME="mcp-lab"                      # local clone dir name

INSTALL_USER="${INSTALL_USER:-$(whoami)}"
INSTALL_HOME="${INSTALL_HOME:-$HOME}"

REPO_DIR="${INSTALL_HOME}/${LOCAL_DIR_NAME}"
SERVER_DIR="${REPO_DIR}/mcp-server"

echo "=== MCP Lab — mcphost Setup ==="
echo "  Installing as: ${INSTALL_USER} (home: ${INSTALL_HOME})"
echo ""

# ---------------------------------------------------------------------------
# 1. System packages
# ---------------------------------------------------------------------------
echo "[1/5] Installing system packages..."
sudo apt-get update -q
sudo apt-get install -y python3 python3-pip python3-venv git curl openssl -q
echo "  Done."

# ---------------------------------------------------------------------------
# 2. Clone the repo
# ---------------------------------------------------------------------------
echo "[2/5] Cloning ${GITHUB_USER}/${GITHUB_REPO}..."
if [ -d "${REPO_DIR}" ]; then
    echo "  Repo already exists — pulling latest..."
    git -C "${REPO_DIR}" pull
else
    git clone "https://github.com/${GITHUB_USER}/${GITHUB_REPO}.git" "${REPO_DIR}"
    echo "  Cloned to ${REPO_DIR}"
fi

# ---------------------------------------------------------------------------
# 3. Python venv
# ---------------------------------------------------------------------------
echo "[3/5] Creating Python venv for MCP server..."
python3 -m venv "${SERVER_DIR}/venv"
"${SERVER_DIR}/venv/bin/pip" install --upgrade pip -q
"${SERVER_DIR}/venv/bin/pip" install -r "${SERVER_DIR}/requirements.txt" -q
echo "  Venv ready."

# ---------------------------------------------------------------------------
# 4. mcp-server.env — bearer-token auth
# ---------------------------------------------------------------------------
echo "[4/5] Setting up mcp-server.env..."
ENV_FILE="${SERVER_DIR}/mcp-server.env"
if [ ! -f "${ENV_FILE}" ]; then
    cp "${SERVER_DIR}/mcp-server.env.example" "${ENV_FILE}"
    TOKEN="$(openssl rand -hex 32)"
    sed -i "s|^MCP_AUTH_TOKEN=.*|MCP_AUTH_TOKEN=${TOKEN}|" "${ENV_FILE}"
    echo ""
    echo "  ╔══════════════════════════════════════════════════════════════╗"
    echo "  ║  ACTION REQUIRED — Copy this token to the agent VM           ║"
    echo "  ╚══════════════════════════════════════════════════════════════╝"
    echo ""
    echo "  Generated MCP_AUTH_TOKEN: ${TOKEN}"
    echo ""
    echo "  Set the SAME value as MCP_AUTH_TOKEN in agent.env on the"
    echo "  agent VM (192.168.2.132) — see setup-agent-vm.sh."
    echo ""
    read -rp "  Press Enter once you've copied the token..."
else
    echo "  mcp-server.env already exists — skipping (token unchanged)."
fi

# ---------------------------------------------------------------------------
# 5. Systemd service
# ---------------------------------------------------------------------------
echo "[5/5] Installing mcp-server systemd service..."
sed -e "s|__INSTALL_USER__|${INSTALL_USER}|g" -e "s|__INSTALL_HOME__|${INSTALL_HOME}|g" \
    "${SERVER_DIR}/mcp-server.service" | sudo tee /etc/systemd/system/mcp-server.service > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable mcp-server.service
echo "  Service enabled (not started)."

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo "=== Setup complete ==="
echo ""
echo "  Before starting: review/edit ${ENV_FILE}"
echo "  (MCP_SERVER_PORT, AGENT_IP, MCPSERVER_IP, OLLAMA_URL, MCP_AUTH_TOKEN)."
echo ""
echo "  Start  : sudo systemctl start mcp-server"
echo "  Status : sudo systemctl status mcp-server"
echo "  Logs   : sudo journalctl -u mcp-server -f"
echo ""
echo "  Self-test once started (replace TOKEN with the value from mcp-server.env):"
echo "    curl -s http://localhost:8765/mcp \\"
echo "      -H 'Authorization: Bearer TOKEN' -H 'Content-Type: application/json' \\"
echo "      -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}'"
echo ""
echo "  Next: run setup-agent-vm.sh on the agent VM"
