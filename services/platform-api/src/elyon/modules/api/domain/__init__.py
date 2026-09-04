"""Signing in, watching, configuring, and the one control that is always safe.

The engine cannot run on a phone -- MT5 is Windows and needs its terminal -- so
the phone is a remote control, not a host. The design follows one asymmetry:
**stopping is safe, starting is not.** Login gives that asymmetry a name rather
than softening it: an OPERATOR can stop the engine from anywhere, and only an
OWNER can start it, reconfigure it, or point it at real money.
"""

from .accounts import (
    DEFAULT_IDLE_TIMEOUT,
    DEFAULT_SESSION_TTL,
    MIN_PASSWORD_LENGTH,
    LoginService,
    LoginThrottle,
    Operator,
    OperatorStore,
    PasswordHasher,
    Role,
    TooManyAttempts,
    check_password_strength,
    normalise_username,
)
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
from .control import (
    LIVE_CONFIRMATION,
    Check,
    ConfigChange,
    EngineControl,
    Preflight,
    change_recorder,
    config_writer,
    control_for,
    live_control_for,
    preflight,
)
from .page import render_page
from .panel import live_panel_for, panel_for, session_snapshot
from .server import (
    LOCALHOST,
    MAX_BODY_BYTES,
    ControlPanel,
    Response,
    Router,
    ServerConfig,
    Unavailable,
    build_server,
    to_jsonable,
)

__all__ = [
    "AccessToken", "Capability", "Check", "ConfigChange", "ControlPanel",
    "DEFAULT_IDLE_TIMEOUT", "DEFAULT_SESSION_TTL", "EngineControl",
    "Forbidden", "LIVE_CONFIRMATION", "LOCALHOST", "LoginService",
    "LoginThrottle", "MAX_BODY_BYTES", "MIN_PASSWORD_LENGTH",
    "MIN_TOKEN_LENGTH", "Operator", "OperatorStore", "PHONE_CAPABILITIES",
    "PasswordHasher", "Preflight", "READ_ONLY", "Response", "Role", "Router",
    "ServerConfig", "TokenRegistry", "TooManyAttempts", "Unauthorised",
    "Unavailable", "build_server", "change_recorder", "check_password_strength",
    "command_token", "config_writer",
    "control_for", "live_control_for", "live_panel_for", "new_secret",
    "normalise_username", "panel_for", "phone_token", "preflight",
    "render_page", "session_snapshot", "to_jsonable",
]
