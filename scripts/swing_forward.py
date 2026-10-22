"""Explicit local S6 journal commands. No downloads, trading or automatic promotion."""
import argparse
from datetime import date
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import load_settings
from app.market.calendar import MarketCalendar
from app.market.clock import SystemClock
from app.swing.forward import ForwardService
from app.swing.operations import OperationsSettings
from app.swing.validation_protocol import ValidationSettings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    register = commands.add_parser("register")
    register.add_argument("campaign")
    register.add_argument("--account", required=True)
    register.add_argument("--start", required=True, type=date.fromisoformat)
    register.add_argument("--end", required=True, type=date.fromisoformat)
    register.add_argument("--hypothesis", required=True)
    register.add_argument("--key", required=True)
    capture = commands.add_parser("capture")
    capture.add_argument("campaign")
    capture.add_argument("--vintage", required=True)
    capture.add_argument("--key", required=True)
    for action in ("report", "verify"):
        commands.add_parser(action).add_argument("campaign")
    commands.add_parser("list")
    args = parser.parse_args(argv)
    cfg, clock = load_settings(), SystemClock()
    service = ForwardService(cfg, clock, MarketCalendar("NSE", config=cfg), protected=(
        OperationsSettings().SWING_OPS_DB_PATH, ValidationSettings().VALIDATION_DB_PATH))
    try:
        if args.command == "register":
            result = service.register(args.campaign, args.account, args.start, args.end, args.hypothesis, args.key)
        elif args.command == "capture":
            result = service.capture(args.campaign, args.vintage, args.key)
        elif args.command == "report":
            result = service.report(args.campaign)
        elif args.command == "verify":
            result = {"campaign": args.campaign, "verified_head": service.journal.read(args.campaign)["head"]}
        else:
            result = service.campaigns()
        print(json.dumps(result, indent=2, default=str, allow_nan=False))
        return 0
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
