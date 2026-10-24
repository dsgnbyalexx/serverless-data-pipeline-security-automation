"""Guarded remediation. A change is made only when ALL of these are true:

1. The rule is in REMEDIATIONS (a short allowlist of narrow, reversible fixes).
2. apply=True was passed explicitly. The default is a dry run that only plans.
3. The resource carries the opt-in tag  compliance:auto-remediate = true.

Everything else is reported as PLANNED or SKIPPED with a reason, and every
outcome is logged. Rules not in the allowlist always need a human.
"""

from collections.abc import Callable

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from compliance.models import Finding, RemediationResult
from pipeline.observability import log

OPT_IN_TAG = "compliance:auto-remediate"


# ------------------------------------------------------------- opt-in tags ---


def _bucket_opted_in(session, bucket: str) -> bool:
    try:
        tags = session.client("s3").get_bucket_tagging(Bucket=bucket)["TagSet"]
    except ClientError as err:
        if err.response["Error"]["Code"] == "NoSuchTagSet":
            return False
        raise
    return any(t["Key"] == OPT_IN_TAG and t["Value"].lower() == "true" for t in tags)


def _security_group_opted_in(session, group_id: str) -> bool:
    group = session.client("ec2").describe_security_groups(GroupIds=[group_id])["SecurityGroups"][0]
    tags = group.get("Tags", [])
    return any(t["Key"] == OPT_IN_TAG and t["Value"].lower() == "true" for t in tags)


# ------------------------------------------------------------------ fixes ----


def _block_bucket_public_access(session, finding: Finding) -> None:
    session.client("s3").put_public_access_block(
        Bucket=finding.resource_id,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )


def _revoke_world_ingress(session, finding: Finding) -> None:
    """Revoke ONLY the internet-wide ranges of the offending rule. Other sources
    on the same port (e.g. a VPN range) are left untouched."""
    permission = finding.context["permission"]
    world = finding.context["world_ranges"]
    narrow = {
        "IpProtocol": permission["IpProtocol"],
        "IpRanges": [{"CidrIp": c} for c in world if ":" not in c],
        "Ipv6Ranges": [{"CidrIpv6": c} for c in world if ":" in c],
    }
    if permission["IpProtocol"] != "-1":
        narrow["FromPort"] = permission["FromPort"]
        narrow["ToPort"] = permission["ToPort"]
    session.client("ec2").revoke_security_group_ingress(
        GroupId=finding.resource_id, IpPermissions=[narrow]
    )


# rule_id -> (description of the change, opt-in check, fix)
REMEDIATIONS: dict[str, tuple[str, Callable, Callable]] = {
    "S3-001": (
        "Enable all four S3 Block Public Access settings",
        _bucket_opted_in,
        _block_bucket_public_access,
    ),
    "EC2-001": (
        "Revoke internet-wide ingress on the exposed admin port",
        _security_group_opted_in,
        _revoke_world_ingress,
    ),
    "EC2-002": (
        "Revoke internet-wide all-traffic ingress",
        _security_group_opted_in,
        _revoke_world_ingress,
    ),
}


def remediate(
    findings: list[Finding],
    *,
    apply: bool = False,
    session: boto3.Session | None = None,
    rules: set[str] | None = None,
) -> list[RemediationResult]:
    session = session or boto3.Session()
    results: list[RemediationResult] = []

    for finding in findings:
        if rules and finding.rule_id not in rules:
            continue
        rule, resource = finding.rule_id, finding.resource_id

        if rule not in REMEDIATIONS:
            result = RemediationResult(
                rule, resource, "none", "SKIPPED", "no automatic fix; needs manual review"
            )
        else:
            action, opted_in, fix = REMEDIATIONS[rule]
            try:
                if not opted_in(session, resource):
                    result = RemediationResult(
                        rule, resource, action, "SKIPPED", f"resource not tagged {OPT_IN_TAG}=true"
                    )
                elif not apply:
                    result = RemediationResult(
                        rule, resource, action, "PLANNED", "dry run: rerun with --apply"
                    )
                else:
                    fix(session, finding)
                    result = RemediationResult(rule, resource, action, "APPLIED")
            except (ClientError, BotoCoreError) as err:
                result = RemediationResult(
                    rule, resource, action, "FAILED", f"{type(err).__name__}: {err}"
                )

        log(
            "WARNING" if result.status == "APPLIED" else "INFO",
            "remediation",
            rule_id=result.rule_id,
            resource_id=result.resource_id,
            action=result.action,
            status=result.status,
            reason=result.reason,
            dry_run=not apply,
        )
        results.append(result)
    return results
