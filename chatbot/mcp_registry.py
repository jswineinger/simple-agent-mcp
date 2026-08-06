"""
mcp_registry.py — multi-backend MCP router for mcp-lab

Fronts N MCP backends (currently: mcphost and dlptest, both real JSON-RPC
2.0 MCP over HTTP via HTTPMCPClient — mcphost additionally requires a
bearer token, see mcp_http_client.py's auth_header) behind the single
surface app.py already consumes: get_tool_definitions() / run_tool_loop() /
call_tool(). Each tool the model sees is namespaced as "<server>__<tool>"
so a name collision between backends can't happen and tools_used shows
which server actually served a call.

Backends receive BARE tool names — the prefix exists only in the
model-facing tool list built here; call_tool() strips it before dispatch.
One backend failing at startup does not disable the other (see app.py's
construction block); this registry itself just assumes every backend
handed to it is already connected.

--- Threat-model note (why this matters for reading lab results) ---
The chatbot invokes MCP tools DIRECTLY — HTTP(S) to mcphost, HTTPS to
dlptest. These tool calls do not traverse FortiAIGate; FAIG only sits in
the LLM request path. So FAIG only ever sees a tool's *result*, and only
because it re-enters messages[] on the NEXT LLM call in the FAIG modes —
in Direct modes the result reaches the model completely unscanned (the
control arm). Concretely: dlptest__echo_sensitive_data's payload rides
the outbound tools/call *arguments* on the chatbot→dlptest leg, which FAIG
never sees in this architecture; only the echoed *result*, re-injected
into the next prompt, is subject to FAIG scanning, and only in FAIG modes.
Don't misread that tool as testing outbound tool-call inspection — it
isn't, in this architecture. dlptest__generate_prompt_context returns PII
embedded in prose (RAG/context-style), pairing with doc_reader.py and any
fetch_url-style vectors.
"""

import json
import logging
import re

logger = logging.getLogger(__name__)

SEP = "__"
_VALID_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class MCPRegistry:
    def __init__(self, backends: dict):
        """backends: ordered {server_name: backend}. Each backend must expose
        .tools (raw list of {"name","description","inputSchema"}) and
        .call_tool(bare_name, arguments) -> str."""
        self.backends = backends

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def any_available(self) -> bool:
        return len(self.backends) > 0

    def __len__(self) -> int:
        return len(self.backends)

    def tool_names(self) -> list:
        return [t["function"]["name"] for t in self.get_tool_definitions()]

    # ------------------------------------------------------------------
    # Public API — mirrors MCPClient
    # ------------------------------------------------------------------

    def get_tool_definitions(self) -> list:
        """Returns tools formatted for the OpenAI 'tools' request field,
        with each name prefixed 'server__tool'. Skips (with a warning) any
        tool whose namespaced name violates the OpenAI tool-name charset
        (^[a-zA-Z0-9_-]{1,64}$) — required for all four modes, including
        Direct+Public's OpenAI-compat translation."""
        definitions = []
        for server_name, backend in self.backends.items():
            for t in backend.tools:
                prefixed = f"{server_name}{SEP}{t['name']}"
                if not _VALID_NAME_RE.match(prefixed):
                    logger.warning(
                        f"Skipping tool with invalid namespaced name: {prefixed!r} "
                        f"(must match ^[a-zA-Z0-9_-]{{1,64}}$)"
                    )
                    continue
                definitions.append({
                    "type": "function",
                    "function": {
                        "name":        prefixed,
                        "description": t.get("description", ""),
                        "parameters":  t.get("inputSchema", {
                            "type": "object", "properties": {}
                        })
                    }
                })
        return definitions

    def call_tool(self, prefixed_name: str, arguments: dict) -> str:
        """Split 'server__tool' on the first SEP and dispatch to that
        backend with the bare tool name."""
        if SEP not in prefixed_name:
            raise ValueError(f"Unroutable tool name: {prefixed_name!r}")

        server_name, bare_name = prefixed_name.split(SEP, 1)
        backend = self.backends.get(server_name)
        if backend is None:
            raise ValueError(f"Unroutable tool name: {prefixed_name!r}")

        return backend.call_tool(bare_name, arguments)

    def run_tool_loop(self, messages: list, llm_post_fn, on_tool_call=None) -> str:
        """
        Core tool-call loop (moved verbatim from MCPClient.run_tool_loop —
        backend-agnostic, only uses self.call_tool() and llm_post_fn).

        Parameters
        ----------
        messages      : full messages[] array (system prompt + history)
        llm_post_fn   : callable(messages) → OpenAI-format response dict
        on_tool_call  : optional callback(tool_name, arguments, result) called
                        after each tool executes (used by app.py to track
                        tools_used and, when DEBUG_JSON is on, to log the call
                        and its result). tool_name here is the PREFIXED name.

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
