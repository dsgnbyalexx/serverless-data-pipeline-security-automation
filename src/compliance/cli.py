"""Command line:  python -m compliance scan | remediate [--apply]

Exit codes (useful in CI):
  0  no findings at or above --fail-on
  1  findings at or above --fail-on
  2  one or more checks could not run (e.g. missing permissions)
"""

import argparse
import json
import sys

import boto3

from compliance.models import Severity
from compliance.remediation import REMEDIATIONS, remediate
from compliance.scanner import run_scan


def _print_table(findings, errors) -> None:
    if not findings:
        print("No findings.")
    for f in findings:
        print(f"[{f.severity.name:<8}] {f.rule_id:<8} {f.resource_id}")
        print(f"           {f.title}: {f.detail}")
    for e in errors:
        print(f"[ERROR   ] check '{e.check}' could not run: {e.error}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m compliance")
    parser.add_argument("--region", help="AWS region (default: from your AWS configuration)")
    parser.add_argument("--profile", help="AWS CLI profile to use")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="detect findings (read-only)")
    scan.add_argument("--format", choices=["table", "json"], default="table")
    scan.add_argument(
        "--fail-on",
        choices=[s.name for s in Severity],
        default="HIGH",
        help="exit 1 if any finding is at or above this severity",
    )

    fix = sub.add_parser("remediate", help="plan fixes (dry run) or apply them with --apply")
    fix.add_argument(
        "--apply",
        action="store_true",
        help="actually change resources (only those tagged compliance:auto-remediate=true)",
    )
    fix.add_argument("--rule", action="append", choices=sorted(REMEDIATIONS), help="limit rules")

    args = parser.parse_args(argv)
    session = boto3.Session(region_name=args.region, profile_name=args.profile)
    result = run_scan(session)

    if args.command == "scan":
        if args.format == "json":
            payload = {
                "findings": [f.to_dict() for f in result.findings],
                "errors": [e.__dict__ for e in result.errors],
            }
            print(json.dumps(payload, indent=2, default=str))
        else:
            _print_table(result.findings, result.errors)
        if result.errors:
            return 2
        threshold = Severity[args.fail_on]
        return 1 if any(f.severity >= threshold for f in result.findings) else 0

    mode = "APPLY" if args.apply else "DRY RUN (no changes will be made)"
    print(f"Remediation mode: {mode}")
    outcomes = remediate(
        result.findings, apply=args.apply, session=session, rules=set(args.rule or [])
    )
    for r in outcomes:
        suffix = f" ({r.reason})" if r.reason else ""
        print(f"[{r.status:<8}] {r.rule_id:<8} {r.resource_id}: {r.action}{suffix}")
    return 2 if any(r.status == "FAILED" for r in outcomes) else 0


if __name__ == "__main__":
    sys.exit(main())
