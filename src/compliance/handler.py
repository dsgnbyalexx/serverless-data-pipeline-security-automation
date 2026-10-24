"""Scheduled Lambda (EventBridge, e.g. daily): scan, log findings, plan fixes.

This entry point NEVER applies changes: it always calls remediate(apply=False),
whatever environment variables say. Applying fixes is a human decision made
with the CLI (`python -m compliance remediate --apply`).
"""

from collections import Counter

from compliance.remediation import remediate
from compliance.scanner import run_scan
from pipeline.observability import emit_metrics, log


def lambda_handler(event, context):
    result = run_scan()

    for finding in result.findings:
        log("WARNING", "compliance finding", **finding.to_dict())
    for error in result.errors:
        log("ERROR", "compliance check could not run", check=error.check, error=error.error)

    plan = remediate(result.findings, apply=False)

    by_severity = Counter(f.severity.name for f in result.findings)
    emit_metrics(
        {
            "FindingsCritical": by_severity["CRITICAL"],
            "FindingsHigh": by_severity["HIGH"],
            "FindingsMedium": by_severity["MEDIUM"],
            "FindingsLow": by_severity["LOW"],
            "CheckErrors": len(result.errors),
            "FixesPlanned": sum(1 for r in plan if r.status == "PLANNED"),
        },
        Function="compliance-scan",
    )
    return {
        "findings": len(result.findings),
        "by_severity": dict(by_severity),
        "check_errors": len(result.errors),
        "fixes_planned": sum(1 for r in plan if r.status == "PLANNED"),
        "applied": 0,
    }
