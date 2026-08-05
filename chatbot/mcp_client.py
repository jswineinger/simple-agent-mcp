"""
mcp_client.py — MCP Client for mcp-lab
Runs inside the Flask chatbot on 192.168.2.132

Connects to mcp_server.py on mcphost via SSH subprocess (stdio transport).
A fresh SSH process is spawned for each tool call to avoid stale-connection
hangs when the chatbot sits idle between requests.

Public API
----------
MCPClient(server_host, server_script, ssh_user, python_bin)
    .get_tool_definitions()  → list for the 'tools' field in LLM API requests
    .run_tool_loop(messages, llm_post_fn, on_tool_call)  → final plain-text reply
"""

import json
import subprocess
import logging

logger = logging.getLogger(__name__)

# Timeout (seconds) waiting for the MCP server to respond
MCP_TIMEOUT = 30


class MCPClient:
    def __init__(self, server_host: str, server_script: str,
                 ssh_user: str = "mcpsvc", python_bin: str = None):
        """
        server_host   : IP/hostname of mcphost (e.g. "192.168.37.6")
        server_script : absolute path to mcp_server.py on mcphost
        ssh_user      : SSH user on mcphost
        python_bin    : python interpreter on mcphost
                        (defaults to venv alongside server_script)
        """
        self.name          = "mcphost"
        self.server_host   = server_host
        self.server_script = server_script
        self.ssh_user      = ssh_user
        self.python_bin    = python_bin or self._default_python(server_script)
        self.tools         = []
        self._connect()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _default_python(script_path: str) -> str:
        import os
        return os.path.join(os.path.dirname(script_path), "venv", "bin", "python3")

    def _ssh_cmd(self) -> list:
        """Build the base SSH command list."""
        return [
            "ssh",
            "-i", "/home/chatsvc/.ssh/id_ed25519_mcphost",
            "-o", "StrictHostKeyChecking=no",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10",
            f"{self.ssh_user}@{self.server_host}",
            f"{self.python_bin} {self.server_script}"
        ]

    def _send(self, obj: dict) -> dict:
        """
        Spawn a fresh SSH subprocess, send one JSON message, read one response.
        Each call is fully self-contained — no persistent connection to go stale.
        """
        cmd = self._ssh_cmd()
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )
            stdout, stderr = proc.communicate(
                input=json.dumps(obj) + "\n",
                timeout=MCP_TIMEOUT
            )
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise ConnectionError(
                f"MCP server timed out after {MCP_TIMEOUT}s "
                f"({self.ssh_user}@{self.server_host})"
            )
        except OSError as e:
            raise ConnectionError(f"SSH subprocess failed: {e}")

        stdout = stdout.strip()
        if not stdout:
            raise ConnectionError(
                f"MCP server returned no output. "
                f"stderr: {stderr.strip()[:300]}"
            )

        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            raise ValueError(
                f"MCP server returned invalid JSON: {stdout[:200]}"
            )

    def _connect(self):
        """Discover available tools from the MCP server (one SSH call)."""
        logger.info(f"Connecting to MCP server: {self.ssh_user}@{self.server_host}")
        self.tools = self._discover_tools()
        names = [t["name"] for t in self.tools]
        logger.info(f"MCP ready — {len(self.tools)} tools: {names}")

    def _discover_tools(self) -> list:
        response = self._send({"method": "tools/list"})
        return response.get("tools", [])

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_tool_definitions(self) -> list:
        """
        Returns tools formatted for the OpenAI 'tools' request field.
        Pass directly as:  payload["tools"] = mcp.get_tool_definitions()
        """
        return [
            {
                "type": "function",
                "function": {
                    "name":        t["name"],
                    "description": t["description"],
                    "parameters":  t.get("inputSchema", {
                        "type": "object", "properties": {}
                    })
                }
            }
            for t in self.tools
        ]

    def call_tool(self, name: str, arguments: dict) -> str:
        """Execute one tool and return the result as a string."""
        logger.info(f"Tool call: {name}({arguments})")
        response = self._send({
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments}
        })
        content = response.get("content", [])
        return content[0].get("text", "") if content else json.dumps(response)

    def run_tool_loop(self, messages: list, llm_post_fn,
                      on_tool_call=None) -> str:
        """
        Core tool-call loop.

        Parameters
        ----------
        messages      : full messages[] array (system prompt + history)
        llm_post_fn   : callable(messages) → OpenAI-format response dict
        on_tool_call  : optional callback(tool_name, arguments, result) called
                        after each tool executes (used by app.py to track
                        tools_used and, when DEBUG_JSON is on, to log the call
                        and its result)

        Returns
        -------
        Final plain-text assistant reply once all tool calls are resolved.
        """
        max_iterations = 10

        for iteration in range(1, max_iterations + 1):
            response   = llm_post_fn(messages)
            choice     = response.get("choices", [{}])[0]
            message    = choice.get("message", {})
            tool_calls = message.get("tool_calls") or []

            # No tool calls — we have the final answer
            if not tool_calls:
                return message.get("content", "")

            logger.info(
                f"Iteration {iteration} — tool calls: "
                f"{[tc['function']['name'] for tc in tool_calls]}"
            )

            # Append assistant's tool-call message to history
            messages.append(message)

            # Execute each tool and append results as role:tool messages
            for tc in tool_calls:
                name = tc.get("function", {}).get("name", "")
                try:
                    args = json.loads(
                        tc.get("function", {}).get("arguments", "{}")
                    )
                except (json.JSONDecodeError, TypeError):
                    args = {}

                try:
                    result = self.call_tool(name, args)
                except Exception as e:
                    result = json.dumps({"error": f"Tool execution failed: {e}"})
                    logger.error(f"Tool '{name}' failed: {e}")

                if on_tool_call:
                    on_tool_call(name, args, result)

                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content":      result
                })

        logger.warning(f"Tool loop hit max iterations ({max_iterations})")
        return "Unable to complete the request — too many tool call iterations."
