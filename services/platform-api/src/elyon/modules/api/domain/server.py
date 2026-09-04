"""The control surface.

A small HTTP server the engine exposes so a phone can watch it and, if needed,
stop it. Standard library only -- a trading system whose monitoring depends on a
package index is one that stops being monitorable on a bad day.

Three things this file is careful about, in order of how much they cost when
wrong:

**It binds to localhost by default.** An endpoint that can flatten positions is
not something to put on the open internet by accident. Reaching it from a phone
is a tunnel's job -- Tailscale, WireGuard, an SSH forward -- and the server says
so loudly if you bind it anywhere else.

**There is no TLS here.** Writing your own is worse than not having any, and
half-working TLS is more dangerous than none because it looks finished. Put it
behind a tunnel or a reverse proxy that someone else maintains.

**Every mutating route needs a capability, and the capabilities are graded by
which way they move risk.** Halting needs PROTECT, which a phone has. Resuming
needs COMMAND, which it does not.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from elyon.shared_kernel.edcs.canonical import canonical_decimal
from elyon.shared_kernel.edcs.numeric import DeterminismError

from .auth import (
    Capability,
    Forbidden,
    TokenRegistry,
    Unauthorised,
)

LOCALHOST = "127.0.0.1"


@dataclass(frozen=True, slots=True)
class Response:
    status: int
    body: Any
    content_type: str = "application/json"


@dataclass(slots=True)
class ControlPanel:
    """What the engine exposes, and what it will accept back.

    The session is reached through callables rather than held directly, so this
    module never needs to know how a session is built -- and so a test can drive
    it without one.
    """

    status: Callable[[], Mapping[str, Any]]
    halt: Callable[[str], str]
    resume: Callable[[str], str] | None = None
    page: Callable[[], str] | None = None

    def snapshot(self) -> Mapping[str, Any]:
        return self.status()


@dataclass(frozen=True, slots=True)
class ServerConfig:
    host: str = LOCALHOST
    port: int = 8787

    @property
    def is_exposed(self) -> bool:
        """Whether this binding is reachable from outside the machine."""
        return self.host not in (LOCALHOST, "localhost", "::1")

    def warnings(self) -> tuple[str, ...]:
        if not self.is_exposed:
            return ()
        return (
            f"binding to {self.host} exposes a control endpoint beyond this "
            f"machine, over plain HTTP. Anyone who reaches it and holds a "
            f"token can stop your bot and close your positions. Prefer "
            f"127.0.0.1 with a VPN or an SSH tunnel; if you must expose it, "
            f"put a TLS-terminating proxy in front.",
        )


def to_jsonable(value: Any) -> Any:
    """Decimals become strings, never floats.

    A price that has survived the whole engine as an exact decimal should not
    lose digits on its way to a screen -- and a number the user reads has to be
    the number the engine used.
    """
    if isinstance(value, Decimal):
        return canonical_decimal(value)
    if isinstance(value, Mapping):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return str(value)


class Router:
    """Maps a request to a response, with the capability it requires."""

    def __init__(self, panel: ControlPanel, tokens: TokenRegistry) -> None:
        self.panel = panel
        self.tokens = tokens

    def handle(
        self, method: str, path: str, token: str | None, body: Mapping[str, Any]
    ) -> Response:
        try:
            return self._route(method, path, token, body)
        except Unauthorised as exc:
            return Response(401, {"error": str(exc)})
        except Forbidden as exc:
            return Response(403, {"error": str(exc)})
        except DeterminismError as exc:
            return Response(400, {"error": str(exc)})

    def _route(
        self, method: str, path: str, token: str | None, body: Mapping[str, Any]
    ) -> Response:
        # The page itself is unauthenticated: it is markup with no data in it,
        # and every figure it shows is fetched with a token afterwards. Gating
        # it would only mean typing the token before seeing where to type it.
        if method == "GET" and path in ("/", "/index.html"):
            if self.panel.page is None:
                return Response(404, {"error": "no page configured"})
            return Response(200, self.panel.page(), "text/html; charset=utf-8")

        if method == "GET" and path == "/api/status":
            self.tokens.authorise(token, Capability.OBSERVE)
            return Response(200, to_jsonable(self.panel.snapshot()))

        if method == "GET" and path == "/api/whoami":
            granted = self.tokens.authorise(token, Capability.OBSERVE)
            return Response(200, {
                "label": granted.label,
                "capabilities": sorted(c.value for c in granted.capabilities),
                # So the page can hide controls it would only get a 403 from.
                "canCommand": granted.can_command,
            })

        if method == "POST" and path == "/api/halt":
            granted = self.tokens.authorise(token, Capability.PROTECT)
            reason = str(body.get("reason") or "").strip()
            if not reason:
                reason = f"halted from {granted.label}"
            return Response(200, {"halted": True,
                                  "message": self.panel.halt(reason)})

        if method == "POST" and path == "/api/resume":
            # The asymmetry, enforced: stopping is PROTECT, starting is COMMAND.
            granted = self.tokens.authorise(token, Capability.COMMAND)
            if self.panel.resume is None:
                return Response(
                    501,
                    {"error": "this engine was started without a resume hook; "
                              "resuming is a console action"},
                )
            reason = str(body.get("reason") or f"resumed from {granted.label}")
            return Response(200, {"halted": False,
                                  "message": self.panel.resume(reason)})

        return Response(404, {"error": f"no route for {method} {path}"})


class _Handler(BaseHTTPRequestHandler):
    router: Router = None  # type: ignore[assignment]
    server_version = "elyon"
    sys_version = ""

    def _token(self) -> str | None:
        header = self.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            return header[7:].strip()
        return self.headers.get("X-Elyon-Token")

    def _body(self) -> Mapping[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            parsed = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _respond(self, response: Response) -> None:
        if response.content_type.startswith("application/json"):
            payload = json.dumps(response.body, indent=2).encode()
        else:
            payload = str(response.body).encode()

        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(payload)))
        # The page never loads anything remote, so nothing here should be
        # allowed to either. A control surface is a poor place to run a CDN's
        # code.
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'unsafe-inline'; "
            "style-src 'unsafe-inline'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 -- stdlib naming
        path = urlparse(self.path).path
        self._respond(self.router.handle("GET", path, self._token(), {}))

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        self._respond(
            self.router.handle("POST", path, self._token(), self._body())
        )

    def log_message(self, fmt: str, *args: Any) -> None:
        """Log the request line without its query string.

        A token accidentally passed as a query parameter would otherwise sit in
        the log in plain text, which is how a credential outlives the session it
        was issued for.
        """
        line = args[0] if args else ""
        clean = str(line).split("?")[0]
        print(f"{self.address_string()} {clean}")


def build_server(
    panel: ControlPanel,
    tokens: TokenRegistry,
    config: ServerConfig | None = None,
) -> ThreadingHTTPServer:
    settings = config or ServerConfig()
    if len(tokens) == 0:
        raise DeterminismError(
            "no tokens configured; the server would refuse every request. "
            "Issue one with `elyon serve`, which prints it once."
        )

    handler = type("ElyonHandler", (_Handler,), {"router": Router(panel, tokens)})
    return ThreadingHTTPServer((settings.host, settings.port), handler)
