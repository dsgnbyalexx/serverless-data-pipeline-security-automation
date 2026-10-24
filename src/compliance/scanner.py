"""Run every check and collect findings. One failing check never stops the others."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from compliance import checks
from compliance.models import CheckError, Finding


@dataclass
class ScanResult:
    findings: list[Finding] = field(default_factory=list)
    errors: list[CheckError] = field(default_factory=list)


def run_scan(session: boto3.Session | None = None, now: datetime | None = None) -> ScanResult:
    session = session or boto3.Session()
    planned: list[tuple[str, Callable[[], list[Finding]]]] = [
        ("s3_buckets", lambda: checks.check_s3_buckets(session.client("s3"))),
        ("security_groups", lambda: checks.check_security_groups(session.client("ec2"))),
        ("iam_access_keys", lambda: checks.check_access_keys(session.client("iam"), now)),
    ]

    result = ScanResult()
    for name, run in planned:
        try:
            result.findings += run()
        except (ClientError, BotoCoreError) as err:
            result.errors.append(CheckError(check=name, error=f"{type(err).__name__}: {err}"))

    result.findings.sort(key=lambda f: (-f.severity, f.rule_id, f.resource_id))
    return result
