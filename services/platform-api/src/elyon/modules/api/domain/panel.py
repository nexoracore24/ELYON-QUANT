"""What the phone is shown.

Deliberately a projection rather than the session itself. Two reasons, and the
second is the one that matters:

*   Nothing here can reach back into the engine. The panel reads; it cannot be
    walked into a mutation by a route that forgot to check a capability.
*   **Nothing sensitive is in the shape at all.** No account number, no server
    name, no token, no file paths. Credentials cannot leak through a field that
    does not exist, and a screenshot of this page in a chat is not an incident.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from elyon.shared_kernel.edcs.numeric import ZERO

from .server import ControlPanel


def session_snapshot(session: Any) -> Mapping[str, Any]:
    """The whole state of a running session, as a phone needs to see it."""
    oms = session.oms
    position = session.position

    snapshot: dict[str, Any] = {
        "symbol": session.config.symbol,
        "mode": session.config.mode.value,
        "halted": oms.is_halted,
        "haltReason": oms.halt_reason,
        "bars": len(session.outcomes),
        "entries": sum(1 for o in session.outcomes if o.traded),
        "closed": len(session.closed_positions),
        "realizedR": session.realized_r,
        "orders": len(oms.orders),
        "deadLetters": len(oms.dlq),
        "stoppedAt": session.stopped_at_counts(),
        "warnings": list(session.config.warnings()),
        "position": {"open": False},
    }

    if position is not None:
        snapshot["position"] = {
            "open": True,
            "direction": position.direction.name,
            "quantity": position.quantity,
            "entry": position.entry,
            "stop": position.stop,
            "target": position.target,
            # The number that answers "can this still lose?" -- positive means
            # the stop is past entry and the trade is already safe.
            "lockedR": position.locked_r(),
            "barsHeld": position.bars_held,
            "brokeEven": position.broke_even,
            "partialTaken": position.partial_taken,
        }

    return snapshot


def live_panel_for(runner: Any, *, allow_resume: bool = False) -> ControlPanel:
    """Wire a *running* session to the control surface.

    Every read goes through the runner's lock. Reading the session directly
    while a feed thread is appending to it would hand a phone a snapshot of a
    state that never existed -- a candle half-added, a position half-written.
    """
    from .page import render_page

    def status() -> Mapping[str, Any]:
        snapshot = dict(runner.read(session_snapshot))
        snapshot.update(runner.health())
        return snapshot

    def halt(reason: str) -> str:
        runner.read(lambda s: s.oms.halt(reason))
        return f"halted: {reason}. Open positions are protected, not closed."

    def resume(reason: str) -> str:
        runner.read(lambda s: s.oms.resume(reason))
        return f"resumed: {reason}"

    return ControlPanel(
        status=status,
        halt=halt,
        resume=resume if allow_resume else None,
        page=render_page,
    )


def panel_for(session: Any, *, allow_resume: bool = False) -> ControlPanel:
    """Wire a session to the control surface.

    ``allow_resume`` is False by default and is not something the phone can
    change. Restarting a halted engine from a device you carry is exactly the
    action the capability split exists to prevent, so the hook is simply absent
    unless someone starting the server chose otherwise.
    """
    from .page import render_page

    def halt(reason: str) -> str:
        session.oms.halt(reason)
        return f"halted: {reason}. Open positions are protected, not closed."

    def resume(reason: str) -> str:
        session.oms.resume(reason)
        return f"resumed: {reason}"

    return ControlPanel(
        status=lambda: session_snapshot(session),
        halt=halt,
        resume=resume if allow_resume else None,
        page=render_page,
    )
