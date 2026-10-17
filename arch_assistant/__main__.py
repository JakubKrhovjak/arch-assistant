"""CLI: `arch-assistant` (tray) | `check [ID…] [--json]` | `act ID [ARGS…]` | `once`."""
from __future__ import annotations

import argparse
import json
import sys

from .actions import run_action
from .checks import run_all
from .config import check_env, load_config
from .cycle import Cycle
from .log import get_logger
from .model import Action
from .secrets import read_api_key
from .state import State


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="arch-assistant", description="Tray assistant for Arch Linux + Plasma")
    sub = parser.add_subparsers(dest="cmd")
    p_check = sub.add_parser("check", help="run checks and print findings")
    p_check.add_argument("ids", nargs="*", help="check ids (default: all)")
    p_check.add_argument("--json", action="store_true", help="raw JSON output")
    p_act = sub.add_parser("act", help="run one action")
    p_act.add_argument("id")
    p_act.add_argument("args", nargs="*")
    sub.add_parser("once", help="run one full cycle without GUI (auto actions do run)")
    p_diag = sub.add_parser("diagnose", help="run one check and ask Claude to explain its finding")
    p_diag.add_argument("id")
    args = parser.parse_args(argv)

    cfg = load_config()
    log = get_logger(cfg.state_dir)
    if args.cmd == "check":
        findings = run_all(cfg.checks_dir, check_env(cfg), only=args.ids or None)
        if args.json:
            print(json.dumps([f.to_dict() for f in findings], indent=1, ensure_ascii=False))
        else:
            for f in findings:
                print(f"[{f.severity:8}] {f.id}: {f.title}" + (f" – {f.summary}" if f.summary else ""))
                if f.error:
                    print(f"           error: {f.error.splitlines()[0]}")
        return 0
    if args.cmd == "act":
        res = run_action(cfg.actions_dir, Action(args.id, args.id, tuple(args.args)), log)
        print(res.output)
        if res.detail:
            print(res.detail, file=sys.stderr)
        return 0 if res.ok else 1
    api_key = read_api_key(cfg.api_key_file)
    state = State(cfg.state_dir / "state.json")
    if args.cmd == "diagnose":
        from .diagnose import collect_context, diagnose
        findings = run_all(cfg.checks_dir, check_env(cfg), only=[args.id])
        if not findings:
            print(f"unknown check: {args.id}")
            return 1
        f = findings[0]
        print(f"[{f.severity:8}] {f.id}: {f.title}" + (f" – {f.summary}" if f.summary else ""))
        context = collect_context(f, cfg, log)
        result = diagnose(f, cfg, api_key, log, context=context)
        if result is None:
            print("diagnosis unavailable (see the log)")
            return 1
        print(f"\n{result.explanation}\n\nconfidence: {result.confidence}\n\nsuggested commands (not executed):")
        for c in result.commands:
            print(f"  {c}")
        return 0
    if args.cmd == "once":
        result = Cycle(cfg, state, log, api_key).run()
        if result is None:
            print("another cycle is running")
            return 1
        for f in result.findings:
            print(f"[{f.severity:8}] {f.id}: {f.title}" + (f" – {f.summary}" if f.summary else ""))
        print(f"\nnew: {[f.id for f in result.new]}")
        if result.summary:
            print(f"\n{result.summary.headline}\n{result.summary.body}")
            if result.summary.risks:
                print("risks: " + "; ".join(result.summary.risks))
        for res in result.auto_run:
            print(f"\nauto {res.action_id}: {'ok' if res.ok else 'FAILED'} – {res.output}")
        for p in result.pending:
            print(f"\npending: {p.label}" + (f" (confirm: {p.reason})" if p.reason else ""))
        return 0
    from .tray import run_tray
    return run_tray(cfg, state, log, api_key)


if __name__ == "__main__":
    sys.exit(main())
