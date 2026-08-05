#!/usr/bin/env bash
# setup-chatbot-vm.sh
# Run once on the chatbot VM (192.168.2.132) after Ubuntu is provisioned.
#
# Bootstrap — this script must be downloaded before the repo exists.
# Grab it directly from GitHub:
#
#   curl -fsSL https://raw.githubusercontent.com/jswineinger/mcp-lab/main/scripts/setup-chatbot-vm.sh \
#     -o setup-chatbot-vm.sh && bash setup-chatbot-vm.sh
#
# What this does:
#   1. Install system packages
#   2. Generate a GitHub SSH deploy key and wait for you to add it
#   3. Clone the mcp-lab repo
#   4. Create Python venv and install dependencies
#   5. Set up chatbot.env
#   6. Generate SSH key for mcphost MCP server access
#   7. Install and enable the systemd service

set -euo pipefail

GITHUB_USER="jswineinger"
REPO_NAME="mcp-lab"
REPO_DIR="/home/mcpsvc/${REPO_NAME}"
CHATBOT_DIR="${REPO_DIR}/chatbot"
GITHUB_KEY="/home/mcpsvc/.ssh/id_ed25519_github"
MCPHOST_KEY="/home/mcpsvc/.ssh/id_ed25519_mcphost"
MCPHOST="192.168.37.6"
MCPHOST_USER="mcpsvc"

echo "=== MCP Lab — Chatbot VM Setup ==="
echo ""

# ---------------------------------------------------------------------------
# 1. System packages
# ---------------------------------------------------------------------------
echo "[1/7] Installing system packages..."
sudo apt-get update -q
sudo apt-get install -y python3 python3-pip python3-venv git openssh-client curl -q
echo "  Done."

# ---------------------------------------------------------------------------
# 2. GitHub SSH deploy key
# ---------------------------------------------------------------------------
echo "[2/7] GitHub SSH key setup..."
mkdir -p /home/mcpsvc/.ssh
chmod 700 /home/mcpsvc/.ssh

if [ ! -f "${GITHUB_KEY}" ]; then
    echo "  Generating GitHub deploy key..."
    ssh-keygen -t ed25519 -f "${GITHUB_KEY}" -N "" \
        -C "chatbot@192.168.2.132-github"
    echo ""
    echo "  ╔══════════════════════════════════════════════════════════════╗"
    echo "  ║  ACTION REQUIRED — Add this deploy key to GitHub            ║"
    echo "  ╚══════════════════════════════════════════════════════════════╝"
    echo ""
    echo "  1. Copy the public key below"
    echo "  2. Go to: https://github.com/${GITHUB_USER}/${REPO_NAME}/settings/keys"
    echo "  3. Click 'Add deploy key'"
    echo "  4. Title: chatbot-vm  |  Key: (paste below)  |  Allow write: NO"
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
SSH_CONFIG="/home/mcpsvc/.ssh/config"
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
echo "[3/7] Cloning ${GITHUB_USER}/${REPO_NAME}..."
if [ -d "${REPO_DIR}" ]; then
    echo "  Repo already exists — pulling latest..."
    git -C "${REPO_DIR}" pull
else
    GIT_SSH_COMMAND="ssh -i ${GITHUB_KEY} -o StrictHostKeyChecking=no" \
        git clone "git@github.com:${GITHUB_USER}/${REPO_NAME}.git" "${REPO_DIR}"
    echo "  Cloned to ${REPO_DIR}"
fi

# ---------------------------------------------------------------------------
# 4. Python venv
# ---------------------------------------------------------------------------
echo "[4/7] Creating Python venv..."
python3 -m venv "${CHATBOT_DIR}/venv"
"${CHATBOT_DIR}/venv/bin/pip" install --upgrade pip -q
"${CHATBOT_DIR}/venv/bin/pip" install -r "${CHATBOT_DIR}/requirements.txt" -q
echo "  Venv ready."

# ---------------------------------------------------------------------------
# 5. chatbot.env
# ---------------------------------------------------------------------------
echo "[5/7] Setting up chatbot.env..."
if [ ! -f "${CHATBOT_DIR}/chatbot.env" ]; then
    cp "${CHATBOT_DIR}/chatbot.env.example" "${CHATBOT_DIR}/chatbot.env"
    echo "  Created chatbot.env from example."
    echo "  Review and edit if needed: ${CHATBOT_DIR}/chatbot.env"
else
    echo "  chatbot.env already exists — skipping."
fi

# ---------------------------------------------------------------------------
# 6. SSH key for mcphost (MCP server access)
# ---------------------------------------------------------------------------
echo "[6/7] SSH key setup for mcphost (MCP server)..."

if [ ! -f "${MCPHOST_KEY}" ]; then
    echo "  Generating mcphost SSH key..."
    ssh-keygen -t ed25519 -f "${MCPHOST_KEY}" -N "" \
        -C "chatbot@192.168.2.132-mcphost"
    echo ""
    echo "  ╔══════════════════════════════════════════════════════════════╗"
    echo "  ║  ACTION REQUIRED — Add this key to mcphost                ║"
    echo "  ╚══════════════════════════════════════════════════════════════╝"
    echo ""
    echo "  On mcphost (${MCPHOST}), run:"
    echo ""
    echo "    mkdir -p ~/.ssh && chmod 700 ~/.ssh"
    echo "    echo '$(cat ${MCPHOST_KEY}.pub)' >> ~/.ssh/authorized_keys"
    echo "    chmod 600 ~/.ssh/authorized_keys"
    echo ""
    read -rp "  Press Enter once the key is added to mcphost..."
else
    echo "  MCP host key already exists at ${MCPHOST_KEY}"
fi

# Add mcphost to SSH config
if ! grep -q "Host mcphost" "${SSH_CONFIG}" 2>/dev/null; then
    cat >> "${SSH_CONFIG}" <<EOF

Host mcphost
    HostName ${MCPHOST}
    User ${MCPHOST_USER}
    IdentityFile ${MCPHOST_KEY}
    StrictHostKeyChecking no
EOF
    chmod 600 "${SSH_CONFIG}"
    echo "  SSH config updated for mcphost"
fi

# Test mcphost connectivity
echo "  Testing SSH to mcphost..."
if ssh -o ConnectTimeout=5 -i "${MCPHOST_KEY}" \
       "${MCPHOST_USER}@${MCPHOST}" "echo SSH_OK" 2>/dev/null | grep -q "SSH_OK"; then
    echo "  MCP host SSH: OK"
else
    echo "  WARNING: SSH to mcphost failed."
    echo "  The chatbot will start but MCP tools will be unavailable until"
    echo "  the key is in place on mcphost."
fi

# Update chatbot.env with the mcphost key path
sed -i "s|^MCP_USER=.*|MCP_USER=${MCPHOST_USER}|" "${CHATBOT_DIR}/chatbot.env"

# ---------------------------------------------------------------------------
# 7. Systemd service
# ---------------------------------------------------------------------------
echo "[7/7] Installing systemd service..."
sudo cp "${CHATBOT_DIR}/mcp-chatbot.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable mcp-chatbot.service
echo "  Service enabled."

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo "=== Setup complete ==="
echo ""
echo "  Start  : sudo systemctl start mcp-chatbot"
echo "  Status : sudo systemctl status mcp-chatbot"
echo "  Logs   : sudo journalctl -u mcp-chatbot -f"
echo "  UI     : http://192.168.2.132:8000"
echo ""
echo "  NOTE: Run setup-mcphost.sh on mcphost before starting the chatbot."
