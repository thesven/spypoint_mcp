"""Optional authenticated loopback HTTP transport for one configured account."""

import hmac

from mcp.server import MCPServer
from starlette.responses import JSONResponse

from .config import SpypointError


class RequireBearer:
    def __init__(self, app, token: str):
        self.app = app
        self.expected = ("Bearer " + token).encode("ascii")

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = [
                value for key, value in scope.get("headers", []) if key.lower() == b"authorization"
            ]
            if len(headers) != 1 or not hmac.compare_digest(headers[0], self.expected):
                response = JSONResponse(
                    {"error": "unauthorized"},
                    status_code=401,
                    headers={
                        "WWW-Authenticate": 'Bearer realm="spypoint-mcp"',
                        "Cache-Control": "no-store",
                    },
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def http_app(server: MCPServer, token: str):
    if (
        len(token) < 32
        or not token.isascii()
        or any(c.isspace() for c in token)
        or any(ord(c) < 33 or ord(c) > 126 for c in token)
    ):
        raise SpypointError(
            "HTTP requires SPYPOINT_MCP_HTTP_TOKEN with at least "
            "32 non-whitespace ASCII characters."
        )
    app = server.streamable_http_app(host="127.0.0.1", max_request_body_size=1_048_576)
    app.add_middleware(RequireBearer, token=token)
    return app
