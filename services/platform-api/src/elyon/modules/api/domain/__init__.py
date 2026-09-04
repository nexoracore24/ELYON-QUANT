"""Remote observation and the one control that is always safe.

The engine cannot run on a phone -- MT5 is Windows and needs its terminal -- so
the phone is a remote control, not a host. The design follows one asymmetry:
**stopping is safe, starting is not.** A phone can observe and protect; resuming
and reconfiguring stay on the machine.
"""

from .auth import (
    MIN_TOKEN_LENGTH,
    PHONE_CAPABILITIES,
    READ_ONLY,
    AccessToken,
    Capability,
    Forbidden,
    TokenRegistry,
    Unauthorised,
    command_token,
    new_secret,
    phone_token,
)
from .page import render_page
from .panel import live_panel_for, panel_for, session_snapshot
from .server import (
    LOCALHOST,
    ControlPanel,
    Response,
    Router,
    ServerConfig,
    build_server,
    to_jsonable,
)

__all__ = [
    "AccessToken", "Capability", "ControlPanel", "Forbidden", "LOCALHOST",
    "MIN_TOKEN_LENGTH", "PHONE_CAPABILITIES", "READ_ONLY", "Response",
    "Router", "ServerConfig", "TokenRegistry", "Unauthorised", "build_server",
    "command_token", "live_panel_for", "new_secret", "panel_for",
    "phone_token", "render_page",
    "session_snapshot", "to_jsonable",
]
