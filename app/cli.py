"""Command line entry point.

python -m app.cli run                  migrate, seed if empty, start the API
python -m app.cli migrate              apply Alembic migrations
python -m app.cli seed [--reset]       load drugs, stations, synthetic orders
python -m app.cli serve                start the API only
python -m app.cli simulate --orders 2000 --ticks 480
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parent.parent


def migrate(database_url: str | None = None) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    if database_url:
        config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


def cmd_seed(args: argparse.Namespace) -> None:
    from app.db.session import get_session_factory
    from app.seed import seed

    with get_session_factory()() as session:
        result = seed(session, orders=args.orders, seed_value=args.seed, force=args.reset)
    if result.skipped:
        print("Database already seeded (use --reset to wipe and reseed).")
    else:
        print(f"Seeded {result.drugs} drugs, {result.stations} stations, {result.orders} orders.")


def cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn

    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=False)


def cmd_run(args: argparse.Namespace) -> None:
    migrate()
    args.reset = False
    cmd_seed(args)
    print(f"API docs: http://{args.host}:{args.port}/docs")
    print(f"Dashboard: http://{args.host}:{args.port}/dashboard")
    cmd_serve(args)


def cmd_simulate(args: argparse.Namespace) -> None:
    from app.db.session import get_session_factory
    from app.services.snapshot import load_snapshot
    from app.simulation import Simulation, SimulationConfig

    with get_session_factory()() as session:
        drugs, inventory, stations = load_snapshot(session)
    if not drugs:
        raise SystemExit("Database is empty. Run: python -m app.cli seed")
    config = SimulationConfig(
        orders=args.orders,
        ticks=args.ticks,
        seed=args.seed,
        station_failure_rate=args.failure_rate,
    )
    report = Simulation(drugs, inventory, stations, config).run()
    print(json.dumps(asdict(report), indent=2, default=str))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="apply database migrations").set_defaults(
        func=lambda _: migrate()
    )

    for name, func, help_text in (
        ("seed", cmd_seed, "load drugs, stations, and synthetic orders"),
        ("run", cmd_run, "migrate, seed if empty, and start the API"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--orders", type=int, default=40)
        p.add_argument("--seed", type=int, default=42)
        if name == "seed":
            p.add_argument("--reset", action="store_true", help="wipe all tables first")
        else:
            p.add_argument("--host", default="127.0.0.1")
            p.add_argument("--port", type=int, default=8000)
        p.set_defaults(func=func)

    p = sub.add_parser("serve", help="start the API")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("simulate", help="run the in-memory simulation and print the report")
    p.add_argument("--orders", type=int, default=2000)
    p.add_argument("--ticks", type=int, default=480)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--failure-rate", type=float, default=0.002)
    p.set_defaults(func=cmd_simulate)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
