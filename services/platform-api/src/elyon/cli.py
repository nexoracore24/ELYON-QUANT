"""The command line.

Everything the engine can do, reachable without writing Python. Stdlib only --
a trading system whose entry point depends on a package index is a trading
system that stops starting one day.

    elyon strategies              the catalog and what each tier means
    elyon dna EURUSD              an instrument's profile
    elyon config > session.json   a starting configuration
    elyon run --config c.json --data bars.csv
    elyon calibrate --data bars.csv --strategy SIX_PILLARS
    elyon conformance --adapter mybroker:build
    elyon doctor                  can this machine run the engine?
    elyon bars --symbol EURUSD --out bars.csv     history from the terminal
    elyon useradd owner --role OWNER
    elyon serve --config c.json --data bars.csv --login
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Iterator, Sequence

from elyon.modules.backtesting.domain import (
    DEFAULT_COSTS,
    Sample,
    SimulationConfig,
    calibration_from,
    report_from,
    research_config,
    simulate,
    tier_of,
)
from elyon.modules.market_context.domain import (
    REFERENCE_PROFILES,
    learn_dna,
    profile_for,
)
from elyon.modules.market_data.domain.model import Candle, CandleState, Timeframe
from elyon.modules.market_data.domain.series import CandleSeries
from elyon.modules.execution.domain import (
    JsonlEventStore,
    ManualClock,
    PaperBroker,
    check_adapter,
)
from elyon.modules.session.domain import Mode, SessionConfig, TradingSession
from elyon.modules.strategy.domain import (
    CATALOG,
    ProbabilityTier,
    StrategyId,
    StrategyRegistry,
    profile,
)
from elyon.shared_kernel.edcs.numeric import DeterminismError, dec

EXIT_OK = 0
EXIT_ERROR = 1


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def read_bars(path: Path, symbol: str, timeframe: Timeframe) -> CandleSeries:
    """Load OHLC bars from CSV.

    Expected header: ``time,open,high,low,close[,volume]``. Time may be an
    epoch in seconds, milliseconds or nanoseconds, or an ISO timestamp.

    Prices are read as **strings** and converted with ``dec``, never through
    float. Parsing "1.10005" as a float and back is how a price becomes
    1.1000499999999999 and two runs on the same file stop agreeing.
    """
    rows = list(csv.DictReader(path.open()))
    if not rows:
        raise DeterminismError(f"{path} contains no rows")

    required = {"time", "open", "high", "low", "close"}
    missing = required - set(rows[0])
    if missing:
        raise DeterminismError(
            f"{path} is missing column(s): {', '.join(sorted(missing))}. "
            f"Expected header: time,open,high,low,close[,volume]"
        )

    candles = []
    for number, row in enumerate(rows, start=2):
        try:
            open_ns = _parse_time(row["time"])
            candles.append(Candle(
                symbol=symbol,
                timeframe=timeframe,
                open_time_ns=open_ns,
                close_time_ns=open_ns + timeframe.duration_ns,
                open=dec(row["open"]), high=dec(row["high"]),
                low=dec(row["low"]), close=dec(row["close"]),
                volume=dec(row.get("volume") or "0"),
                tick_count=1,
                state=CandleState.CONFIRMED,
            ))
        except (ValueError, DeterminismError) as exc:
            raise DeterminismError(f"{path} line {number}: {exc}") from exc

    return CandleSeries.of(candles)


def _parse_time(raw: str) -> int:
    """Epoch nanoseconds from whatever the file happens to carry."""
    value = raw.strip()
    if value.isdigit():
        number = int(value)
        # Disambiguate by magnitude. A seconds epoch for any plausible trading
        # date is 10 digits; milliseconds 13; nanoseconds 19.
        if number < 10_000_000_000:
            return number * 1_000_000_000
        if number < 10_000_000_000_000:
            return number * 1_000_000
        return number
    from datetime import datetime, timezone

    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1_000_000_000)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_strategies(args: argparse.Namespace) -> int:
    registry = StrategyRegistry.default()
    print("ELYON QUANT — strategy catalog\n")
    print("  ● live   ◐ shadow   ○ off")
    print("  🟢 high   🟡 medium   🔴 low   ⚪ unproven\n")
    print(registry.summary())
    print()
    print("Every effective tier is ⚪ UNPROVEN: the catalog ships with")
    print("hypotheses, not blessings. A tier is earned by a calibration run,")
    print("never by the confidence of whoever wrote the strategy. Until then")
    print("nothing may open a trade on its own.")
    print()
    print("  elyon calibrate --data bars.csv --strategy SIX_PILLARS")
    return EXIT_OK


def cmd_dna(args: argparse.Namespace) -> int:
    if args.symbol:
        print(profile_for(args.symbol).describe())
        return EXIT_OK

    print("ELYON QUANT — Market DNA\n")
    for name in sorted(REFERENCE_PROFILES):
        dna = REFERENCE_PROFILES[name]
        print(f"  {name:<8} {dna.asset_class.value:<10} "
              f"ATR {dna.typical_atr:<10} spread {dna.typical_spread} "
              f"(max {dna.max_spread})")
    print()
    print("All reference profiles. DNA adapts filters, never rules -- and a")
    print("hand-written profile is a guess until it is learned from real bars.")
    return EXIT_OK


def cmd_config(args: argparse.Namespace) -> int:
    """Print a starting configuration to stdout."""
    template = {
        "symbol": args.symbol,
        "mode": "PAPER",
        "timeframe": "M1",
        "strategies": ["SIX_PILLARS"],
        "shadowStrategies": [
            s.value for s in StrategyId if s is not StrategyId.SIX_PILLARS
        ],
        "conflictPolicy": "VETO",
        "risk": {
            "equity": "10000",
            "riskPerTrade": "0.005",
            "dailyLossLimit": "0.02",
            "minRewardRisk": "1.5",
            "maxConcurrentPositions": 1,
        },
        "management": {
            "breakEvenAtR": "1.0",
            "trailFromR": "1.5",
            "partialAtR": "1.5",
            "partialFraction": "0.5",
            "timeStopBars": 40,
        },
        "atrPeriod": 14,
        "warmupBars": 40,
        "lookbackBars": 120,
    }
    print(json.dumps(template, indent=2))
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    config = SessionConfig.load(args.config)
    for warning in config.warnings():
        print(f"⚠ {warning}", file=sys.stderr)

    series = read_bars(Path(args.data), config.symbol, Timeframe(config.timeframe))
    dna = profile_for(config.symbol)
    if args.learn_dna:
        dna = learn_dna(series, dna, atr_period=config.atr_period)
        print(f"learned DNA from {len(series)} bars: "
              f"typical ATR {dna.typical_atr}", file=sys.stderr)

    store = JsonlEventStore(Path(args.journal)) if args.journal else None
    session = TradingSession(config, dna=dna, store=store)
    for candle in series:
        session._on_candle(candle)

    print(session.summary())

    if args.verbose:
        print("\nEntries:")
        for outcome in session.outcomes:
            if outcome.traded:
                print(f"  {outcome}")
        for position in session.closed_positions:
            print(f"  closed {position.realized_r}R "
                  f"({position.close_reason.value if position.close_reason else '?'})")

    print(f"\nconfig hash {config.config_hash[:16]}…  ·  "
          f"dna hash {dna.dna_hash[:16]}…")
    if store is not None:
        print(f"order journal: {store.path} ({store.size_bytes} bytes)")
    return EXIT_OK


def cmd_calibrate(args: argparse.Namespace) -> int:
    strategy = StrategyId(args.strategy)
    timeframe = Timeframe(args.timeframe)
    series = read_bars(Path(args.data), args.symbol, timeframe)

    config = SimulationConfig(costs=DEFAULT_COSTS)
    registry = StrategyRegistry.all_off().live(strategy)
    trades = simulate(
        series, registry, symbol=args.symbol, config=config,
        playbook=research_config((strategy,)),
    )
    report = report_from(
        trades, strategy=strategy, dataset=Path(args.data).stem,
        sample=Sample(args.sample), data_hash=series[0].data_hash,
        config_hash=config.config_hash, registry_hash=registry.config_hash,
    )

    print(report.summary())
    print()

    tier = tier_of(report)
    print(f"this run would award: {tier.badge} {tier.value}")

    try:
        calibration = calibration_from(report)
    except DeterminismError as exc:
        print(f"\nnot certified:\n  {exc}")
        return EXIT_OK

    # Say what the record will actually *do*, not merely that one was produced.
    # "Certified" next to an UNPROVEN tier reads as a green light for something
    # that will change nothing, which is worse than no message at all.
    from elyon.modules.strategy.domain import MIN_CALIBRATION_SAMPLE

    if tier is ProbabilityTier.UNPROVEN:
        print(
            f"\nThis changes nothing: {calibration.sample_size} trades is below "
            f"the {MIN_CALIBRATION_SAMPLE} needed for a record to count, so "
            f"{strategy.value} stays ⚪ and still cannot open a trade alone.\n"
            f"Run it over more data."
        )
        return EXIT_OK

    needed = tier.corroboration_required
    unlocks = (
        "it can now open a trade on its own"
        if needed == 0
        else f"it needs {needed} corroborating famil"
             f"{'y' if needed == 1 else 'ies'} before it can trade"
    )
    print(f"\nCertified as {tier.badge} {tier.value} — {unlocks}.")
    print("Add to your config:")
    print(json.dumps({
        "strategy": strategy.value,
        "sampleSize": calibration.sample_size,
        "wins": calibration.wins,
        "expectancyR": str(calibration.expectancy_r),
        "dataset": calibration.dataset,
    }, indent=2))
    return EXIT_OK


DEFAULT_OPERATORS_FILE = "operators.json"


def _password_from(args: argparse.Namespace, *, confirm: bool) -> str:
    """Read a password without it ending up anywhere it can be read back.

    ``getpass`` rather than an argument: a password on a command line is in the
    shell history, in ``ps`` output, and in whatever ships the shell history
    somewhere else. ``--stdin`` exists for scripts, which is a deliberate
    choice someone makes rather than the easy default.
    """
    import getpass

    if getattr(args, "stdin", False):
        return sys.stdin.readline().rstrip("\n")
    password = getpass.getpass("password: ")
    if confirm and getpass.getpass("again: ") != password:
        raise SystemExit("passwords do not match")
    return password


def _store(args: argparse.Namespace):
    from elyon.modules.api.domain import OperatorStore

    return OperatorStore.load(getattr(args, "operators", None)
                              or DEFAULT_OPERATORS_FILE)


def cmd_useradd(args: argparse.Namespace) -> int:
    """Create the account that signs in to the app."""
    from elyon.modules.api.domain import Role

    store = _store(args)
    role = Role(args.role)
    operator = store.add(args.username, _password_from(args, confirm=True), role)

    print(f"\ncreated {operator.username} ({role.value}: {role.summary})")
    print(f"  stored in {store.path} with permissions 0600")
    if role is Role.OWNER:
        print(
            "\n  An OWNER can start the engine, change the risk and switch to\n"
            "  LIVE. If you only want to watch and stop from a phone, make\n"
            "  that account an OPERATOR instead -- a device you carry through\n"
            "  airports should not be able to raise your risk."
        )
    return EXIT_OK


def cmd_users(args: argparse.Namespace) -> int:
    store = _store(args)
    print(f"\n{store.path}\n")
    print(store.summary())
    if not len(store):
        print("\n  Create one with: elyon useradd <name> --role OWNER")
    print()
    return EXIT_OK


def cmd_passwd(args: argparse.Namespace) -> int:
    store = _store(args)
    store.set_password(args.username, _password_from(args, confirm=True))
    print(f"password changed for {args.username}")
    print(
        "  Sessions already signed in stay valid until they expire. Restart "
        "the engine to end them now."
    )
    return EXIT_OK


def cmd_userdel(args: argparse.Namespace) -> int:
    store = _store(args)
    store.remove(args.username)
    print(f"removed {args.username}")
    return EXIT_OK


def cmd_serve(args: argparse.Namespace) -> int:
    """Serve the control surface a phone connects to."""
    from elyon.modules.api.domain import (
        DEFAULT_IDLE_TIMEOUT, LoginService, OperatorStore, ServerConfig,
        TokenRegistry, build_server, change_recorder, command_token,
        config_writer, live_panel_for, panel_for, phone_token,
    )

    config = SessionConfig.load(args.config)
    series = read_bars(Path(args.data), config.symbol, Timeframe(config.timeframe))
    dna = profile_for(config.symbol)
    if args.learn_dna:
        dna = learn_dna(series, dna, atr_period=config.atr_period)

    store = JsonlEventStore(Path(args.journal)) if args.journal else None
    session = TradingSession(config, dna=dna, store=store)
    for candle in series:
        session._on_candle(candle)

    # With accounts, the engine comes up halted. "Start" has to be a real
    # action or it is decoration -- and an app whose first screen shows an
    # engine already trading gives nobody a chance to check the settings first.
    accounts = None
    login = None
    if args.login:
        accounts = OperatorStore.load(args.operators or DEFAULT_OPERATORS_FILE)
        if not len(accounts):
            print(
                f"no operators in {accounts.path}. Create one first:\n\n"
                f"    elyon useradd owner --role OWNER\n\n"
                f"There is no default login. A default credential on something "
                f"that can place orders is not a convenience.",
                file=sys.stderr,
            )
            return EXIT_ERROR
        session.oms.halt("not started yet -- press Start when you are ready")

    runner = None
    if args.live:
        from elyon.modules.execution.infrastructure.mt5_feed import Mt5TickFeed
        from elyon.modules.session.domain import LiveRunner

        feed = Mt5TickFeed(config.symbol)
        feed.ensure_symbol()
        runner = LiveRunner(session, feed)
        # With accounts the feed waits for Start too, so that a person sees the
        # settings before the engine sees a tick.
        if not args.login:
            runner.start()
            print(f"live feed started on {feed.venue_symbol}\n")

    tokens = TokenRegistry(
        idle_timeout_seconds=DEFAULT_IDLE_TIMEOUT if args.login else None
    )
    phone = None
    if accounts is not None:
        login = LoginService(accounts, tokens)
    else:
        phone = phone_token("phone")
        tokens.add(phone)
        if args.allow_command:
            tokens.add(command_token("console"))

    settings = ServerConfig(host=args.host, port=args.port)
    for warning in settings.warnings():
        print(f"⚠ {warning}\n", file=sys.stderr)

    # A setting changed from a phone that vanishes on the next reboot is worse
    # than one that was refused: the engine comes back looking right and sized
    # against something else. Applied changes are written back to the same file
    # the engine was started with -- two places to look for what an engine is
    # configured to do is one too many, and the second is always stale.
    wiring: dict = {}
    if args.login and not args.no_save:
        wiring["persist"] = config_writer(args.config)
        wiring["record"] = change_recorder(
            args.changelog or Path(args.config).with_suffix(".changes.jsonl")
        )

    panel = (
        live_panel_for(
            runner, allow_resume=args.allow_command, login=login, **wiring,
        )
        if runner is not None
        else panel_for(
            session,
            allow_resume=args.allow_command,
            login=login,
            configurable=args.login,
            **wiring,
        )
    )
    server = build_server(panel, tokens, settings)

    print("ELYON QUANT control surface\n")
    print(f"  http://{args.host}:{args.port}/\n")
    if accounts is not None:
        print(f"  Sign in with one of the {len(accounts)} account(s) in "
              f"{accounts.path}:\n")
        print(accounts.summary())
        print("\n  The engine is halted. An OWNER starts it from the app,")
        print("  after checking the settings. An OPERATOR can only stop it --")
        print("  which is the point: stopping is safe, starting is not.")
    else:
        print("  Paste this token into the page. It is printed once:\n")
        print(f"    {phone.secret}\n")
        print("  The phone can watch and can stop. It cannot resume, cannot")
        print("  change risk, and cannot enable a strategy -- those stay here.")
        print("\n  For a login instead of a pasted token: elyon serve --login")
    if not settings.is_exposed:
        print("\n  Bound to localhost. To reach it from a phone, put both")
        print("  devices on a VPN (Tailscale, WireGuard) or forward the port")
        print("  over SSH. Do not put this on the open internet.")
    print("\n  Ctrl-C to stop serving.\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        if runner is not None:
            runner.stop()
        server.server_close()
    return EXIT_OK


def cmd_bars(args: argparse.Namespace) -> int:
    """Export closed candles from the MT5 terminal into a CSV."""
    from elyon.modules.execution.infrastructure.mt5 import Mt5Config
    from elyon.modules.execution.infrastructure.mt5_history import Mt5History

    history = Mt5History(
        args.symbol,
        Mt5Config(symbol_suffix=args.suffix),
        server_offset_hours=args.server_offset,
    )
    timeframe = Timeframe(args.timeframe)
    series = history.candles(timeframe, args.count)
    history.to_csv(args.out, timeframe, args.count)

    print(f"\n{args.out}: {history.describe_window(series)}\n")
    print(f"  Symbol at the venue: {history.venue_symbol}")
    print(f"  Server clock assumed: UTC{args.server_offset:+d}")
    if args.server_offset == 0:
        print()
        print("  If your broker's server runs at UTC+2 or UTC+3, pass")
        print("  --server-offset 2 (or 3). Getting this wrong does not fail --")
        print("  it moves every killzone by that many hours, and the engine")
        print("  looks like it is working. Check the timestamps above against")
        print("  a session you know: London opens 07:00 UTC in winter.")
    print()
    print("  The bar still forming was not exported. A file that carries it")
    print("  would hand the engine a high and a low that change later.")
    print()
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace) -> int:
    """Say whether this machine can run the engine, and what it can do."""
    from elyon.host import inspect, verdict

    report = inspect(args.path)
    print("ELYON QUANT — host check\n")
    print(report)
    print()
    print(verdict(report))
    if report.advisories and not report.blockers:
        print()
        print("  Advisories above are things to know, not reasons to stop.")
    print()
    return EXIT_ERROR if report.blockers else EXIT_OK


def cmd_conformance(args: argparse.Namespace) -> int:
    """Check a broker adapter against the OMS's safety contract."""
    if args.adapter:
        module_name, _, attribute = args.adapter.rpartition(":")
        if not module_name:
            print(
                "error: --adapter takes module:factory, e.g. "
                "myvenue.adapter:build",
                file=sys.stderr,
            )
            return EXIT_ERROR
        import importlib

        factory = getattr(importlib.import_module(module_name), attribute)
    else:
        print("No --adapter given; checking the built-in paper broker as a "
              "demonstration of what a passing report looks like.\n")
        factory = lambda clock: PaperBroker(clock)  # noqa: E731

    report = check_adapter(factory, ManualClock(), symbol=args.symbol)
    print(report)
    return EXIT_OK if report.safe_to_use else EXIT_ERROR


def cmd_demo(args: argparse.Namespace) -> int:
    import demo_pipeline

    demo_pipeline.main()
    return EXIT_OK


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="elyon",
        description="ELYON QUANT — algorithmic trading engine",
    )
    subs = parser.add_subparsers(dest="command", required=True)

    subs.add_parser(
        "strategies", help="list the strategy catalog and its tiers"
    ).set_defaults(func=cmd_strategies)

    dna = subs.add_parser("dna", help="show Market DNA profiles")
    dna.add_argument("symbol", nargs="?", help="one instrument, or all if omitted")
    dna.set_defaults(func=cmd_dna)

    config = subs.add_parser("config", help="print a starting configuration")
    config.add_argument("--symbol", default="EURUSD")
    config.set_defaults(func=cmd_config)

    run = subs.add_parser("run", help="run a session over a bar file")
    run.add_argument("--config", required=True, help="session configuration JSON")
    run.add_argument("--data", required=True, help="CSV of OHLC bars")
    run.add_argument("--learn-dna", action="store_true",
                     help="derive the instrument profile from this data")
    run.add_argument("--journal", help="append the order log to this file, so "
                                       "a restart can recover what was in flight")
    run.add_argument("--verbose", "-v", action="store_true")
    run.set_defaults(func=cmd_run)

    calibrate = subs.add_parser(
        "calibrate", help="measure a strategy and produce a calibration"
    )
    calibrate.add_argument("--data", required=True)
    calibrate.add_argument("--strategy", default="SIX_PILLARS")
    calibrate.add_argument("--symbol", default="EURUSD")
    calibrate.add_argument("--timeframe", default="M1")
    calibrate.add_argument(
        "--sample", default="IN_SAMPLE",
        choices=[s.value for s in Sample],
        help="IN_SAMPLE runs are measured but never certified",
    )
    calibrate.set_defaults(func=cmd_calibrate)

    serve = subs.add_parser(
        "serve", help="serve the control surface for a phone or browser"
    )
    serve.add_argument("--config", required=True)
    serve.add_argument("--data", required=True)
    serve.add_argument("--journal")
    serve.add_argument("--learn-dna", action="store_true")
    serve.add_argument(
        "--host", default="127.0.0.1",
        help="binding anywhere else exposes a control endpoint over plain HTTP",
    )
    serve.add_argument("--port", type=int, default=8787)
    serve.add_argument(
        "--live", action="store_true",
        help="drive the session from the MT5 tick feed instead of stopping at "
             "the end of the bar file (requires the terminal)",
    )
    serve.add_argument(
        "--allow-command", action="store_true",
        help="also issue a token that can resume trading. Not for a phone.",
    )
    serve.add_argument(
        "--login", action="store_true",
        help="sign in with a username and password instead of a printed "
             "token, and enable the settings screen. The engine starts halted.",
    )
    serve.add_argument(
        "--operators", help=f"the account file (default {DEFAULT_OPERATORS_FILE})"
    )
    serve.add_argument(
        "--changelog",
        help="where configuration changes are appended (default: the config "
             "file's name with .changes.jsonl)",
    )
    serve.add_argument(
        "--no-save", action="store_true",
        help="apply changes to the running engine but never write them back. "
             "Every edit then lasts until the next restart.",
    )
    serve.set_defaults(func=cmd_serve)

    # -- accounts ---------------------------------------------------------
    for name, handler, summary in (
        ("useradd", cmd_useradd, "create an account that can sign in"),
        ("passwd", cmd_passwd, "change an account's password"),
        ("userdel", cmd_userdel, "remove an account"),
    ):
        parser_ = subs.add_parser(name, help=summary)
        parser_.add_argument("username")
        parser_.add_argument("--operators")
        parser_.add_argument(
            "--stdin", action="store_true",
            help="read the password from stdin instead of prompting. For "
                 "scripts; a password on a command line is in the shell "
                 "history and in ps output.",
        )
        if name == "useradd":
            parser_.add_argument(
                "--role", default="OPERATOR",
                choices=["VIEWER", "OPERATOR", "OWNER"],
                help="VIEWER watches; OPERATOR watches and stops; OWNER also "
                     "configures and starts",
            )
        parser_.set_defaults(func=handler)

    bars = subs.add_parser(
        "bars", help="export closed candles from the MT5 terminal to a CSV"
    )
    bars.add_argument("--symbol", required=True)
    bars.add_argument("--out", required=True)
    bars.add_argument(
        "--timeframe", default="M5", choices=[t.value for t in Timeframe]
    )
    bars.add_argument("--count", type=int, default=1500)
    bars.add_argument(
        "--suffix", default="",
        help="Exness Standard and Cent accounts append one (EURUSDm); Pro and "
             "Raw do not",
    )
    bars.add_argument(
        "--server-offset", type=int, default=0,
        help="hours the broker's server clock runs ahead of UTC. Wrong values "
             "do not fail; they move every killzone.",
    )
    bars.set_defaults(func=cmd_bars)

    doctor = subs.add_parser(
        "doctor", help="check whether this machine can run the engine"
    )
    doctor.add_argument(
        "--path", help="the directory the engine will write to (default: here)"
    )
    doctor.set_defaults(func=cmd_doctor)

    users = subs.add_parser("users", help="list the accounts that can sign in")
    users.add_argument("--operators")
    users.set_defaults(func=cmd_users)

    conformance = subs.add_parser(
        "conformance",
        help="check a broker adapter against the OMS's safety contract",
    )
    conformance.add_argument(
        "--adapter",
        help="module:factory returning a BrokerAdapter, e.g. myvenue:build. "
             "Omit to check the built-in paper broker.",
    )
    conformance.add_argument("--symbol", default="EURUSD")
    conformance.set_defaults(func=cmd_conformance)

    subs.add_parser("demo", help="run the end-to-end demonstration").set_defaults(
        func=cmd_demo
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except DeterminismError as exc:
        # A domain refusal is a message for the user, not a stack trace. The
        # messages are written to be read.
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
