"""Detection rules. Each check only READS configuration; nothing here changes anything."""

import json
from datetime import UTC, datetime, timedelta

from botocore.exceptions import ClientError

from compliance.models import Finding, Severity

WORLD = ("0.0.0.0/0", "::/0")
ADMIN_PORTS = {22: "SSH", 3389: "RDP"}
MAX_KEY_AGE = timedelta(days=90)


def _error_code(err: ClientError) -> str:
    return err.response["Error"]["Code"]


# ---------------------------------------------------------------- S3 ---------


def check_s3_buckets(s3) -> list[Finding]:
    findings: list[Finding] = []
    for bucket in s3.list_buckets().get("Buckets", []):
        name = bucket["Name"]
        findings += _check_public_access_block(s3, name)
        findings += _check_tls_only_policy(s3, name)
        findings += _check_versioning(s3, name)
    return findings


def _check_public_access_block(s3, name: str) -> list[Finding]:
    try:
        config = s3.get_public_access_block(Bucket=name)["PublicAccessBlockConfiguration"]
    except ClientError as err:
        if _error_code(err) != "NoSuchPublicAccessBlockConfiguration":
            raise
        config = {}
    settings = ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")
    disabled = [s for s in settings if not config.get(s, False)]
    if not disabled:
        return []
    return [
        Finding(
            rule_id="S3-001",
            title="S3 bucket does not fully block public access",
            severity=Severity.HIGH,
            resource_type="AWS::S3::Bucket",
            resource_id=name,
            detail=f"Block Public Access settings off: {', '.join(disabled)}",
            recommendation="Enable all four S3 Block Public Access settings on the bucket.",
        )
    ]


def _denies_insecure_transport(policy: dict) -> bool:
    for statement in policy.get("Statement", []):
        condition = statement.get("Condition", {}).get("Bool", {})
        if (
            statement.get("Effect") == "Deny"
            and str(condition.get("aws:SecureTransport", "")).lower() == "false"
        ):
            return True
    return False


def _check_tls_only_policy(s3, name: str) -> list[Finding]:
    try:
        policy = json.loads(s3.get_bucket_policy(Bucket=name)["Policy"])
    except ClientError as err:
        if _error_code(err) != "NoSuchBucketPolicy":
            raise
        policy = {}
    if _denies_insecure_transport(policy):
        return []
    return [
        Finding(
            rule_id="S3-002",
            title="S3 bucket allows unencrypted (HTTP) requests",
            severity=Severity.MEDIUM,
            resource_type="AWS::S3::Bucket",
            resource_id=name,
            detail="No bucket policy statement denies requests where aws:SecureTransport is false.",
            recommendation="Add a bucket policy Deny statement for aws:SecureTransport = false.",
        )
    ]


def _check_versioning(s3, name: str) -> list[Finding]:
    status = s3.get_bucket_versioning(Bucket=name).get("Status")
    if status == "Enabled":
        return []
    return [
        Finding(
            rule_id="S3-003",
            title="S3 bucket versioning is not enabled",
            severity=Severity.LOW,
            resource_type="AWS::S3::Bucket",
            resource_id=name,
            detail=f"Versioning status: {status or 'never enabled'}",
            recommendation="Enable versioning (and a lifecycle rule to expire old versions).",
        )
    ]


# ---------------------------------------------------------------- EC2 --------


def _world_ranges(permission: dict) -> list[str]:
    v4 = [r["CidrIp"] for r in permission.get("IpRanges", []) if r.get("CidrIp") in WORLD]
    v6 = [r["CidrIpv6"] for r in permission.get("Ipv6Ranges", []) if r.get("CidrIpv6") in WORLD]
    return v4 + v6


def check_security_groups(ec2) -> list[Finding]:
    findings: list[Finding] = []
    for page in ec2.get_paginator("describe_security_groups").paginate():
        for group in page["SecurityGroups"]:
            for permission in group.get("IpPermissions", []):
                findings += _check_permission(group, permission)
    return findings


def _check_permission(group: dict, permission: dict) -> list[Finding]:
    ranges = _world_ranges(permission)
    if not ranges:
        return []
    group_id = group["GroupId"]
    protocol = permission.get("IpProtocol")
    context = {"permission": permission, "world_ranges": ranges}

    if protocol == "-1":
        return [
            Finding(
                rule_id="EC2-002",
                title="Security group allows ALL traffic from the internet",
                severity=Severity.CRITICAL,
                resource_type="AWS::EC2::SecurityGroup",
                resource_id=group_id,
                detail=f"All protocols and ports open to {', '.join(ranges)}",
                recommendation="Remove the rule; allow only the specific ports and sources needed.",
                context=context,
            )
        ]

    if protocol not in ("tcp", "6"):
        return []
    low, high = permission.get("FromPort", 0), permission.get("ToPort", 65535)
    exposed = [name for port, name in ADMIN_PORTS.items() if low <= port <= high]
    if not exposed:
        return []
    return [
        Finding(
            rule_id="EC2-001",
            title="Security group exposes remote administration to the internet",
            severity=Severity.HIGH,
            resource_type="AWS::EC2::SecurityGroup",
            resource_id=group_id,
            detail=f"{'/'.join(exposed)} (tcp {low}-{high}) open to {', '.join(ranges)}",
            recommendation="Remove the rule; use SSM Session Manager or a restricted source range.",
            context=context,
        )
    ]


# ---------------------------------------------------------------- IAM --------


def check_access_keys(iam, now: datetime | None = None) -> list[Finding]:
    now = now or datetime.now(UTC)
    findings: list[Finding] = []
    for page in iam.get_paginator("list_users").paginate():
        for user in page["Users"]:
            keys = iam.list_access_keys(UserName=user["UserName"])["AccessKeyMetadata"]
            for key in keys:
                age = now - key["CreateDate"]
                if key["Status"] == "Active" and age > MAX_KEY_AGE:
                    findings.append(
                        Finding(
                            rule_id="IAM-001",
                            title="Active IAM access key is older than 90 days",
                            severity=Severity.MEDIUM,
                            resource_type="AWS::IAM::AccessKey",
                            resource_id=f"{user['UserName']}/{key['AccessKeyId']}",
                            detail=f"Key age: {age.days} days",
                            recommendation="Rotate the key, or replace it with IAM roles / SSO.",
                        )
                    )
    return findings
