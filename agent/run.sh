#!/usr/bin/env bash
# run.sh — manual start for the MCP Lab Agent
# Usage: ./run.sh
# Reads agent.env if present, otherwise uses defaults.

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load env file if present
ENV_FILE="${SCRIPT_DIR}/agent.env"
if [ -f "${ENV_FILE}" ]; then
    set -a
    source "${ENV_FILE}"
    set +a
fi

source "${SCRIPT_DIR}/venv/bin/activate"

echo "MCP Lab Agent"
echo "  Ollama    : ${OLLAMA_URL:-http://127.0.0.1:11434}"
echo "  Model     : ${PRIVATE_MODEL:-qwen2.5:14b}"
echo "  mcp-server: ${MCPSERVER_IP:-127.0.0.1}:${MCP_SERVER_PORT:-8765}"
echo "  UI        : http://$(hostname -I | awk '{print $1}'):${PORT:-8000}"
echo ""

python3 "${SCRIPT_DIR}/app.py"
