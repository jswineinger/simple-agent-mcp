"""
fetch_url_tool.py  --  MCP tool: lab-hosts-only website reader.

Surface under test: this rides the `tools/response` scanner (NOT the input scanner).
It realises the *faithful* indirect-injection model your findings call for: an honest
tool returning attacker-controlled EXTERNAL data. The tool code is clean and passes
review; the payload lives in the page it fetches. Point it at a page you host on a lab
box, hide an instruction in the visible text, and watch whether tools/response catches it
and whether the model acts on it.

SSRF posture (lab-hosts-only, per design decision):
  - scheme must be http/https
  - host must resolve entirely into the lab CIDRs below
  - redirects are NOT followed (reported instead) -- kills redirect-based SSRF
  - short timeout, no auth, response body size-capped
Residual TOCTOU (resolve-then-connect re-resolves) is accepted for lab scope.

Deps: requests, beautifulsoup4  ->  pip install requests beautifulsoup4
"""

import ipaddress
import socket
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

# Lab subnets only. 192.168.2.x (main lab), 192.168.37.x (mcphost), loopback
# (so you can serve a poisoned page on mcphost itself and fetch 127.0.0.1).
ALLOWED_CIDRS = [
    ipaddress.ip_network("192.168.2.0/24"),
    ipaddress.ip_network("192.168.37.0/24"),
    ipaddress.ip_network("127.0.0.0/8"),
]

FETCH_TIMEOUT = 5          # seconds
MAX_BYTES = 512 * 1024     # cap raw download
MAX_TEXT_CHARS = 24000     # cap extracted text (mirror doc_reader budget)


class FetchError(Exception):
    pass


def _host_is_lab_only(host):
    """True only if EVERY A/AAAA record for host falls inside ALLOWED_CIDRS."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise FetchError(f"DNS resolution failed for '{host}': {e}")
    addrs = {info[4][0] for info in infos}
    if not addrs:
        raise FetchError(f"No addresses resolved for '{host}'")
    for a in addrs:
        ip = ipaddress.ip_address(a)
        if not any(ip in net for net in ALLOWED_CIDRS):
            raise FetchError(
                f"Host '{host}' resolves to {a}, outside the lab allowlist. Blocked."
            )
    return True


def _html_to_text(html):
    soup = BeautifulSoup(html, "html.parser")
    # Strip script/style so extraction mirrors realistic RAG plain-text.
    # (The <script> sink is a separate tool -- don't conflate it here.)
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    # collapse blank-line spam
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def fetch_url(url):
    """
    Fetch a lab-hosted page and return its extracted plain text as the tool result.
    Raises FetchError on scheme/allowlist/redirect/network problems.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise FetchError(f"Scheme '{parsed.scheme}' not allowed (http/https only).")
    if not parsed.hostname:
        raise FetchError("No host in URL.")

    _host_is_lab_only(parsed.hostname)

    try:
        resp = requests.get(
            url,
            timeout=FETCH_TIMEOUT,
            allow_redirects=False,          # do NOT follow -> no redirect SSRF
            stream=True,
            headers={"User-Agent": "aiproxy-lab-fetch/1.0"},
        )
    except requests.RequestException as e:
        raise FetchError(f"Request failed: {e}")

    if resp.is_redirect or resp.is_permanent_redirect:
        loc = resp.headers.get("Location", "?")
        raise FetchError(
            f"URL redirected to '{loc}' -- redirects are not followed (SSRF guard). "
            f"Fetch the final URL directly."
        )

    raw = resp.raw.read(MAX_BYTES + 1, decode_content=True)
    if len(raw) > MAX_BYTES:
        raw = raw[:MAX_BYTES]
    html = raw.decode(resp.encoding or "utf-8", errors="replace")

    text = _html_to_text(html)
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS] + "\n[...truncated...]"

    return {
        "url": url,
        "status": resp.status_code,
        "content_type": resp.headers.get("Content-Type", ""),
        "text": text,
    }


# --- MCP glue -------------------------------------------------------------
# Tool definition for tools/list. Definition is intentionally CLEAN (no IPs, no
# "sensitive" category words) so it lists normally. NOTE: "URL"/"website" may still
# trip the tools/list DLP false-positive class from Finding #3 if that scanner is
# re-enabled -- another data point if so.
FETCH_URL_TOOL_DEF = {
    "name": "fetch_url",
    "description": "Retrieve a web page from an internal lab server and return its text.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The page to retrieve."}
        },
        "required": ["url"],
    },
}


def handle_fetch_url(arguments):
    """Wire this into your tools/call dispatch on params.name == 'fetch_url'."""
    url = arguments.get("url", "")
    try:
        result = fetch_url(url)
        return {"content": [{"type": "text", "text": result["text"]}]}
    except FetchError as e:
        return {"content": [{"type": "text", "text": f"fetch_url error: {e}"}],
                "isError": True}
