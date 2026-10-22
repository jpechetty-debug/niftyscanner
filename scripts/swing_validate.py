"""S4 offline preregistration/evaluation; reads real S1/S3 stores, never trades.

python -m scripts.swing_validate schema
python -m scripts.swing_validate register --protocol study.json
python -m scripts.swing_validate development --study study-id --output new-report.json
python -m scripts.swing_validate holdout --study study-id --output new-terminal.json
python -m scripts.swing_validate show --report <report-sha256>
python -m scripts.swing_validate verify --report <report-sha256>
python -m scripts.swing_validate audit --vintage <S1-sha256> --output new-audit.json
"""
import argparse
import json
from pathlib import Path
import sqlite3

from app.core.config import load_settings
from app.market.calendar import MarketCalendar
from app.market.clock import SystemClock
from app.swing.ledger import encoded
from app.swing.validation import ValidationService, audit_vintage
from app.swing.validation_protocol import Study, ValidationSettings
from app.swing.validation_store import ValidationRegistry


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["schema", "register", "development", "holdout", "show", "verify", "audit"])
    parser.add_argument("--protocol", type=Path)
    parser.add_argument("--study")
    parser.add_argument("--report")
    parser.add_argument("--vintage")
    parser.add_argument("--output", type=Path, help="New file only; existing files are never overwritten")
    args = parser.parse_args(argv)
    if args.command == "schema":
        print(json.dumps(Study.model_json_schema(), indent=2))
        return 0
    config, validation, clock = load_settings(), ValidationSettings(), SystemClock()
    calendar = MarketCalendar("NSE", config=config)
    registry = ValidationRegistry(validation, clock)
    service = ValidationService(registry, config, clock, calendar)  # validates storage separation even on register
    try:
        if args.output and args.output.exists():
            raise ValueError("Output already exists; choose a new review artifact")
        if args.output:
            protected = [registry.path, service.repository.path, service.ledger.path,
                         Path(config.SWING_ARTIFACT_DIR), Path(config.NSE_UNIVERSE_PATH), Path("data/history.db")]
            if config.SWING_SOURCE_DB_PATH:
                protected.append(Path(config.SWING_SOURCE_DB_PATH))
            if args.output.resolve() in {p.resolve() for p in protected} or args.output.resolve().is_relative_to(Path(config.SWING_ARTIFACT_DIR).resolve()):
                raise ValueError("Output cannot replace protected stores/evidence")
        if args.command == "register":
            if not args.protocol:
                parser.error("--protocol is required")
            material = json.loads(args.protocol.read_text(encoding="utf-8"))
            material.setdefault("gates", validation.VALIDATION_GATES.model_dump(mode="json"))
            result = registry.register(Study.model_validate(material), calendar)
        elif args.command in {"development", "holdout"}:
            if not args.study:
                parser.error("--study is required")
            result = service.evaluate(args.study, holdout=args.command == "holdout")
        elif args.command == "audit":
            if not args.vintage:
                parser.error("--vintage is required")
            result = registry.envelope(audit_vintage(service.repository, args.vintage))
        else:
            if not args.report:
                parser.error("--report is required")
            result = registry.report(args.report)
            if args.command == "verify":
                result = {"sha256": result["sha256"], "hash_verified": True,
                          "authentication": result["authentication"], "authentication_verified": bool(result["signature"])}
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as handle:
                handle.write(json.dumps(result, indent=2, default=str, allow_nan=False)+"\n")
        # Full reports remain available in the registry/new artifact; stdout stays compact.
        payload = result.get("payload", result)
        print(encoded({"sha256": result.get("sha256"), "authentication": result.get("authentication"),
                       **{k: payload[k] for k in ("study_id", "phase", "model_status", "failures", "summary", "hash_verified", "authentication_verified") if k in payload}}))
        return 0
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.exit(1, f"Swing validation error: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
