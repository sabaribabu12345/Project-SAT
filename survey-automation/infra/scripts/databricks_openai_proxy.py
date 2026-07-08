from __future__ import annotations

import base64
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


HOST = os.environ.get("DATABRICKS_HOST", "").rstrip("/")
CLIENT_ID = os.environ.get("DATABRICKS_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("DATABRICKS_CLIENT_SECRET", "")
PORT = int(os.environ.get("DBX_OPENAI_PROXY_PORT", "9000"))

_token_lock = threading.Lock()
_token_value = ""
_token_exp = 0


def _decode_exp(jwt_token: str) -> int:
    parts = jwt_token.split(".")
    if len(parts) < 2:
        return int(time.time()) + 300
    payload = parts[1]
    padding = "=" * ((4 - len(payload) % 4) % 4)
    decoded = base64.urlsafe_b64decode(payload + padding).decode("utf-8")
    payload_json = json.loads(decoded)
    return int(payload_json.get("exp", int(time.time()) + 300))


def _mint_token() -> tuple[str, int]:
    body = urllib.parse.urlencode({"grant_type": "client_credentials", "scope": "all-apis"}).encode("utf-8")
    basic = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(
        f"{HOST}/oidc/v1/token",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    token = str(data.get("access_token", "")).strip()
    if not token:
        raise RuntimeError("Databricks token response missing access_token")
    return token, _decode_exp(token)


def _get_token() -> str:
    global _token_value, _token_exp
    now = int(time.time())
    with _token_lock:
        if _token_value and now < (_token_exp - 120):
            return _token_value
        token, exp = _mint_token()
        _token_value = token
        _token_exp = exp
        return _token_value


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _inject_prompt_caching(self, body: bytes) -> bytes:
        """Add cache_control breakpoints to the system prompt and first human turn.

        Anthropic prompt caching requires min 1024 tokens in the cached block.
        Skyvern's system prompt easily exceeds that. We mark:
          - system[last] → cache_control ephemeral (5-min TTL)
          - messages[first human content block] → cache_control ephemeral

        If the body is not valid JSON or doesn't match the expected shape,
        return the original bytes unchanged.
        """
        try:
            payload = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return body

        if not isinstance(payload, dict):
            return body

        modified = False

        # Mark the last system block
        system = payload.get("system")
        if isinstance(system, list) and system:
            last = system[-1]
            if isinstance(last, dict) and "cache_control" not in last:
                last["cache_control"] = {"type": "ephemeral"}
                modified = True
        elif isinstance(system, str) and system:
            payload["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
            modified = True

        # Mark the first human message content block
        messages = payload.get("messages")
        if isinstance(messages, list):
            for msg in messages:
                if not isinstance(msg, dict) or msg.get("role") != "user":
                    continue
                content = msg.get("content")
                if isinstance(content, list) and content:
                    first = content[-1]
                    if isinstance(first, dict) and "cache_control" not in first:
                        first["cache_control"] = {"type": "ephemeral"}
                        modified = True
                elif isinstance(content, str) and content:
                    msg["content"] = [{"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}]
                    modified = True
                break

        if not modified:
            return body

        return json.dumps(payload).encode("utf-8")

    def _proxy(self) -> None:
        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
            return

        if not HOST or not CLIENT_ID or not CLIENT_SECRET:
            self._send_json(
                500,
                {
                    "error": "missing_databricks_config",
                    "required": ["DATABRICKS_HOST", "DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET"],
                },
            )
            return

        incoming_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(incoming_length) if incoming_length > 0 else None

        # Inject prompt caching headers for POST requests (chat completions)
        if body and self.command == "POST":
            content_type = self.headers.get("Content-Type", "")
            if "application/json" in content_type:
                body = self._inject_prompt_caching(body)

        token = _get_token()
        target_url = f"{HOST}/serving-endpoints{self.path}"

        forward_headers: dict[str, str] = {
            "Authorization": f"Bearer {token}",
            "Content-Length": str(len(body)) if body else "0",
        }
        for header in ("Content-Type", "Accept"):
            value = self.headers.get(header)
            if value:
                forward_headers[header] = value

        req = urllib.request.Request(target_url, data=body, method=self.command, headers=forward_headers)
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                response_body = resp.read()
                self.send_response(resp.status)
                content_type = resp.headers.get("Content-Type")
                if content_type:
                    self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(response_body)))
                self.end_headers()
                if response_body:
                    self.wfile.write(response_body)
        except urllib.error.HTTPError as err:
            response_body = err.read()
            self.send_response(err.code)
            content_type = err.headers.get("Content-Type") if err.headers else None
            if content_type:
                self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            if response_body:
                self.wfile.write(response_body)
        except Exception as exc:
            self._send_json(502, {"error": "proxy_failure", "detail": str(exc)})

    def do_GET(self) -> None:  # noqa: N802
        self._proxy()

    def do_POST(self) -> None:  # noqa: N802
        self._proxy()

    def do_PUT(self) -> None:  # noqa: N802
        self._proxy()

    def do_PATCH(self) -> None:  # noqa: N802
        self._proxy()

    def do_DELETE(self) -> None:  # noqa: N802
        self._proxy()


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), ProxyHandler)
    print(f"Databricks OpenAI proxy listening on :{PORT}")
    server.serve_forever()
