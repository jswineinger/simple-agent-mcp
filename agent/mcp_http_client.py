"""
mcp_http_client.py — real MCP client over Streamable HTTP for mcp-lab

Speaks JSON-RPC 2.0 to a Streamable HTTP MCP server (single POST endpoint,
stateless or session-based). Written against the public dlptest server:

    https://mcp.dlptest.com/api/mcp/

NOTE: the dlptest landing page is at https://dlptest.com/mcp/ but the actual
JSON-RPC endpoint lives on a different host — https://mcp.dlptest.com/api/mcp/
(confirmed live: POST to https://dlptest.com/mcp returns 405; the endpoint
above returns a normal `application/json` initialize response). The server
runs in stateless mode — no `Mcp-Session-Id` response header is sent — but
this client still captures and replays one if a server ever sets it.

Also used, with a different URL and a bearer token via `auth_header`, for
the mcphost backend (mcp_server.py) — both backends speak the same wire
protocol, so this one class serves both; mcp_registry.py treats every
backend identically:
    .tools                        → list of raw {"name","description","inputSchema"}
    .call_tool(name, arguments)   → str
"""

import itertools
import json
import logging

import requests

logger = logging.getLogger(__name__)

_id_counter = itertools.count(1)


class HTTPMCPClient:
    def __init__(self, url: str, name: str = "dlptest", timeout: int = 30,
                 auth_header: str | None = None):
        self.url         = url
        self.name        = name
        self.timeout     = timeout
        self.auth_header = auth_header
        self.session_id  = None
        self.tools       = []
        self._connect()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Accept":       "application/json, text/event-stream",
        }
        if self.auth_header:
            headers["Authorization"] = self.auth_header
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    @staticmethod
    def _parse_sse(text: str, want_id) -> dict:
        """Parse a text/event-stream body, returning the JSON-RPC message
        whose id matches want_id (or the first non-notification message if
        want_id is None)."""
        data_lines = []
        messages = []
        for line in text.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[len("data:"):].strip())
            elif not line.strip() and data_lines:
                messages.append("\n".join(data_lines))
                data_lines = []
        if data_lines:
            messages.append("\n".join(data_lines))

        for raw in messages:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if want_id is not None and msg.get("id") == want_id:
                return msg
            if want_id is None and "id" not in msg:
                return msg
        raise ValueError(f"No matching SSE message for id={want_id}: {text[:300]}")

    def _rpc(self, method: str, params: dict = None, is_notification: bool = False) -> dict:
        """Send one JSON-RPC request/notification, return the `result` dict
        (empty for notifications). Raises ConnectionError/ValueError on
        transport or parse failure."""
        req_id = None
        body = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if not is_notification:
            req_id = next(_id_counter)
            body["id"] = req_id

        try:
            response = requests.post(
                self.url,
                headers=self._headers(),
                json=body,
                timeout=self.timeout,
            )
        except requests.exceptions.RequestException as e:
            raise ConnectionError(f"MCP HTTP request failed ({self.name}): {e}")

        session_id = response.headers.get("Mcp-Session-Id")
        if session_id:
            self.session_id = session_id

        if is_notification:
            return {}

        content_type = (response.headers.get("Content-Type") or "").lower()
        text = response.text

        if not text.strip():
            raise ConnectionError(
                f"MCP server ({self.name}) returned an empty body for {method} "
                f"(status {response.status_code})"
            )

        try:
            if content_type.startswith("application/json"):
                msg = response.json()
            elif content_type.startswith("text/event-stream"):
                msg = self._parse_sse(text, req_id)
            else:
                # Be lenient — some servers omit/mislabel content-type.
                try:
                    msg = json.loads(text)
                except json.JSONDecodeError:
                    msg = self._parse_sse(text, req_id)
        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError(
                f"MCP server ({self.name}) returned unparseable {method} response: "
                f"{str(e)[:200]} — body: {text[:200]}"
            )

        if "error" in msg:
            raise ValueError(f"MCP server ({self.name}) error on {method}: {msg['error']}")

        return msg.get("result", {})

    def _connect(self):
        logger.info(f"Connecting to MCP server ({self.name}): {self.url}")
        result = self._rpc("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "mcp-lab-agent", "version": "1.0"},
        })
        server_version = result.get("protocolVersion", "unknown")
        logger.info(f"{self.name} MCP handshake ok — protocolVersion={server_version}")

        self._rpc("notifications/initialized", is_notification=True)

        result = self._rpc("tools/list")
        self.tools = result.get("tools", [])
        names = [t["name"] for t in self.tools]
        logger.info(f"{self.name} MCP ready — {len(self.tools)} tools: {names}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def call_tool(self, name: str, arguments: dict) -> str:
        """Execute one tool and return the result as a string. Never raises
        on a tool-level error — returns a json-encoded {"error": ...} string
        instead, matching MCPClient's tool-loop-friendly behavior."""
        logger.info(f"Tool call ({self.name}): {name}({arguments})")
        try:
            result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        except (ConnectionError, ValueError) as e:
            logger.error(f"Tool '{name}' failed on {self.name}: {e}")
            return json.dumps({"error": str(e)})

        if result.get("isError"):
            return json.dumps({"error": result})

        content = result.get("content", [])
        texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
        if texts:
            return "".join(texts)
        return json.dumps(result)


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")

    url = sys.argv[1] if len(sys.argv) > 1 else "https://mcp.dlptest.com/api/mcp/"
    print(f"Connecting to {url} ...")
    client = HTTPMCPClient(url=url, name="dlptest")

    print(f"\n{len(client.tools)} tools:")
    for t in client.tools:
        desc_first_line = (t.get("description") or "").splitlines()[0] if t.get("description") else ""
        print(f"  - {t['name']}: {desc_first_line}")

    probe_name = "list_dlp_patterns" if any(t["name"] == "list_dlp_patterns" for t in client.tools) else "probe_request"
    print(f"\nCalling {probe_name}() ...")
    result = client.call_tool(probe_name, {})
    print(result[:200])
