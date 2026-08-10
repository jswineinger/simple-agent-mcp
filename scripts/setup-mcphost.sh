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
#   2. Generate a GitHub SSH deploy key and wait for you to add it
#   3. Clone the repo
#   4. Create Python venv for the MCP server
#   5. Generate mcp-server.env with a bearer-token secret (copy this token
#      into MCP_AUTH_TOKEN in agent.env on the agent VM)
#   6. Install and enable the systemd service

set -euo pipefail

GITHUB_USER="jswineinger"
GITHUB_REPO="simple-agent-mcp"                # actual GitHub repo slug
LOCAL_DIR_NAME="mcp-lab"                      # local clone dir name — kept
                                               # short; must match the paths
                                               # baked into *.service files
REPO_DIR="/home/labadmin/${LOCAL_DIR_NAME}"
SERVER_DIR="${REPO_DIR}/mcp-server"
GITHUB_KEY="/home/labadmin/.ssh/id_ed25519_github"

echo "=== MCP Lab — mcphost Setup ==="
echo ""

# ---------------------------------------------------------------------------
# 1. System packages
# ---------------------------------------------------------------------------
echo "[1/6] Installing system packages..."
sudo apt-get update -q
sudo apt-get install -y python3 python3-pip python3-venv git openssh-client curl openssl -q
echo "  Done."

# ---------------------------------------------------------------------------
# 2. GitHub SSH deploy key
# ---------------------------------------------------------------------------
echo "[2/6] GitHub SSH key setup..."
mkdir -p /home/labadmin/.ssh
chmod 700 /home/labadmin/.ssh

if [ ! -f "${GITHUB_KEY}" ]; then
    echo "  Generating GitHub deploy key..."
    ssh-keygen -t ed25519 -f "${GITHUB_KEY}" -N "" \
        -C "mcphost@192.168.37.6-github"
    echo ""
    echo "  ╔══════════════════════════════════════════════════════════════╗"
    echo "  ║  ACTION REQUIRED — Add this deploy key to GitHub            ║"
    echo "  ╚══════════════════════════════════════════════════════════════╝"
    echo ""
    echo "  1. Copy the public key below"
    echo "  2. Go to: https://github.com/${GITHUB_USER}/${GITHUB_REPO}/settings/keys"
    echo "  3. Click 'Add deploy key'"
    echo "  4. Title: mcphost  |  Key: (paste below)  |  Allow write: NO"
    echo ""
    echo "  ── PUBLIC KEY ──────────────────────────────────────────────────"
    cat "${GITHUB_KEY}.pub"
    echo "  ────────────────────────────────────────────────────────────────"
    echo ""
    read -rp "  Press Enter once the deploy key is saved in GitHub..."
else
    echo "  GitHub key already exists at ${GITHUB_KEY}"
fi

# Configure SSH to use this key for github.com
SSH_CONFIG="/home/labadmin/.ssh/config"
if ! grep -q "Host github.com" "${SSH_CONFIG}" 2>/dev/null; then
    cat >> "${SSH_CONFIG}" <<EOF

Host github.com
    HostName github.com
    User git
    IdentityFile ${GITHUB_KEY}
    StrictHostKeyChecking no
EOF
    chmod 600 "${SSH_CONFIG}"
    echo "  SSH config updated for github.com"
fi

# Test GitHub connectivity
echo "  Testing GitHub SSH access..."
if ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 \
       -i "${GITHUB_KEY}" -T git@github.com 2>&1 | grep -q "successfully authenticated"; then
    echo "  GitHub SSH: OK"
else
    echo "  WARNING: GitHub SSH test inconclusive — proceeding anyway."
    echo "  If clone fails, verify the deploy key is saved in GitHub."
fi

# ---------------------------------------------------------------------------
# 3. Clone the repo
# ---------------------------------------------------------------------------
echo "[3/6] Cloning ${GITHUB_USER}/${GITHUB_REPO}..."
if [ -d "${REPO_DIR}" ]; then
    echo "  Repo already exists — pulling latest..."
    GIT_SSH_COMMAND="ssh -i ${GITHUB_KEY} -o StrictHostKeyChecking=no" \
        git -C "${REPO_DIR}" pull
else
    GIT_SSH_COMMAND="ssh -i ${GITHUB_KEY} -o StrictHostKeyChecking=no" \
        git clone "git@github.com:${GITHUB_USER}/${GITHUB_REPO}.git" "${REPO_DIR}"
    echo "  Cloned to ${REPO_DIR}"
fi

# ---------------------------------------------------------------------------
# 4. Python venv
# ---------------------------------------------------------------------------
echo "[4/6] Creating Python venv for MCP server..."
python3 -m venv "${SERVER_DIR}/venv"
"${SERVER_DIR}/venv/bin/pip" install --upgrade pip -q
"${SERVER_DIR}/venv/bin/pip" install -r "${SERVER_DIR}/requirements.txt" -q
echo "  Venv ready."

# ---------------------------------------------------------------------------
# 5. mcp-server.env — bearer-token auth
# ---------------------------------------------------------------------------
echo "[5/6] Setting up mcp-server.env..."
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
# 6. Systemd service
# ---------------------------------------------------------------------------
echo "[6/6] Installing mcp-server systemd service..."
sudo cp "${SERVER_DIR}/mcp-server.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable mcp-server.service
sudo systemctl start mcp-server.service

sleep 1
if sudo systemctl is-active --quiet mcp-server.service; then
    echo "  mcp-server.service: running"
else
    echo "  WARNING: mcp-server.service did not start."
    echo "  Check: sudo journalctl -u mcp-server -n 30"
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo "=== Setup complete ==="
echo ""
echo "  Logs : sudo journalctl -u mcp-server -f"
echo ""
echo "  Self-test (replace TOKEN with the value from mcp-server.env):"
echo "    curl -s http://localhost:8765/mcp \\"
echo "      -H 'Authorization: Bearer TOKEN' -H 'Content-Type: application/json' \\"
echo "      -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}'"
echo ""
echo "  Next: run setup-agent-vm.sh on the agent VM (192.168.2.132)"
