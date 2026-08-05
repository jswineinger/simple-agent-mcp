#!/usr/bin/env bash
# run.sh — manual start for the MCP Lab Chatbot
# Usage: ./run.sh
# Reads chatbot.env if present, otherwise uses defaults.

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load env file if present
ENV_FILE="${SCRIPT_DIR}/chatbot.env"
if [ -f "${ENV_FILE}" ]; then
    set -a
    source "${ENV_FILE}"
    set +a
fi

source "${SCRIPT_DIR}/venv/bin/activate"

echo "MCP Lab Chatbot"
echo "  LLM  : ${LLM_URL:-http://192.168.37.6:11434/v1}"
echo "  Model: ${MODEL:-qwen2.5:14b}"
echo "  MCP  : ${MCP_USER:-mcpsvc}@${MCP_HOST:-192.168.37.6}"
echo "  UI   : http://$(hostname -I | awk '{print $1}'):${PORT:-8000}"
echo ""

python3 "${SCRIPT_DIR}/app.py"
