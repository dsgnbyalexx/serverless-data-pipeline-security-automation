"""Compliance scanner and remediation tests against a deliberately misconfigured,
simulated AWS account (moto). Nothing here touches a real account."""

import json
from datetime import UTC, datetime, timedelta

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from compliance import checks, cli, handler
from compliance import remediation as remediation_module
from compliance.models import Severity
from compliance.remediation import OPT_IN_TAG, remediate
from compliance.scanner import run_scan
from helpers import REGION

OPT_IN = [{"Key": OPT_IN_TAG, "Value": "true"}]
TLS_ONLY_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "DenyInsecureTransport",
            "Effect": "Deny",
            "Principal": "*",
            "Action": "s3:*",
            "Resource": ["arn:aws:s3:::compliant-bucket", "arn:aws:s3:::compliant-bucket/*"],
            "Condition": {"Bool": {"aws:SecureTransport": "false"}},
        }
    ],
}


def _tcp(port_from, port_to, *cidrs):
    return {
        "IpProtocol": "tcp",
        "FromPort": port_from,
        "ToPort": port_to,
        "IpRanges": [{"CidrIp": c} for c in cidrs if ":" not in c],
        "Ipv6Ranges": [{"CidrIpv6": c} for c in cidrs if ":" in c],
    }


@pytest.fixture
def account():
    with mock_aws():
        s3 = boto3.client("s3", region_name=REGION)
        ec2 = boto3.client("ec2", region_name=REGION)
        iam = boto3.client("iam", region_name=REGION)

        # --- S3: one compliant bucket, two risky ones (only one opted in) ---
        s3.create_bucket(Bucket="compliant-bucket")
        s3.put_public_access_block(
            Bucket="compliant-bucket",
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        )
        s3.put_bucket_policy(Bucket="compliant-bucket", Policy=json.dumps(TLS_ONLY_POLICY))
        s3.put_bucket_versioning(
            Bucket="compliant-bucket", VersioningConfiguration={"Status": "Enabled"}
        )
        s3.create_bucket(Bucket="risky-opted-in-bucket")
        s3.put_bucket_tagging(Bucket="risky-opted-in-bucket", Tagging={"TagSet": OPT_IN})
        s3.create_bucket(Bucket="risky-untagged-bucket")

        # --- Security groups ---
        vpc_id = ec2.describe_vpcs()["Vpcs"][0]["VpcId"]
        groups = {}

        def group(name, permissions, tags=None):
            spec = [{"ResourceType": "security-group", "Tags": tags}] if tags else []
            gid = ec2.create_security_group(
                GroupName=name, Description=name, VpcId=vpc_id, TagSpecifications=spec
            )["GroupId"]
            if permissions:
                ec2.authorize_security_group_ingress(GroupId=gid, IpPermissions=permissions)
            groups[name] = gid

        group("ssh-open", [_tcp(22, 22, "0.0.0.0/0", "10.0.0.0/8")], tags=OPT_IN)
        group("all-open", [{"IpProtocol": "-1", "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}], OPT_IN)
        group("rdp-ipv6-untagged", [_tcp(3389, 3389, "::/0")])
        group("port-range-untagged", [_tcp(20, 25, "0.0.0.0/0")])
        group("web-https", [_tcp(443, 443, "0.0.0.0/0")])  # public HTTPS is fine
        group("internal-ssh", [_tcp(22, 22, "10.0.0.0/8")])  # private source is fine

        # --- IAM: one active key, one inactive key ---
        iam.create_user(UserName="alice")
        iam.create_access_key(UserName="alice")
        iam.create_user(UserName="bob")
        bob_key = iam.create_access_key(UserName="bob")["AccessKey"]["AccessKeyId"]
        iam.update_access_key(UserName="bob", AccessKeyId=bob_key, Status="Inactive")

        yield {"s3": s3, "ec2": ec2, "groups": groups, "session": boto3.Session()}


def _keys(findings, groups):
    names = {gid: name for name, gid in groups.items()}
    return {(f.rule_id, names.get(f.resource_id, f.resource_id)) for f in findings}


def _ingress(account, name):
    return account["ec2"].describe_security_groups(GroupIds=[account["groups"][name]])[
        "SecurityGroups"
    ][0]["IpPermissions"]


# ------------------------------------------------------------- detection -----


def test_scan_finds_every_misconfiguration_and_nothing_else(account):
    result = run_scan(account["session"])
    found = _keys(result.findings, account["groups"])

    expected = {
        ("S3-001", "risky-opted-in-bucket"),
        ("S3-002", "risky-opted-in-bucket"),
        ("S3-003", "risky-opted-in-bucket"),
        ("S3-001", "risky-untagged-bucket"),
        ("S3-002", "risky-untagged-bucket"),
        ("S3-003", "risky-untagged-bucket"),
        ("EC2-001", "ssh-open"),
        ("EC2-002", "all-open"),
        ("EC2-001", "rdp-ipv6-untagged"),
        ("EC2-001", "port-range-untagged"),
    }
    assert found == expected
    assert result.errors == []


def test_safe_resources_are_not_flagged(account):
    found = _keys(run_scan(account["session"]).findings, account["groups"])
    flagged = {resource for _, resource in found}
    assert {"compliant-bucket", "web-https", "internal-ssh"}.isdisjoint(flagged)


def test_findings_are_sorted_most_severe_first(account):
    severities = [f.severity for f in run_scan(account["session"]).findings]
    assert severities == sorted(severities, reverse=True)
    assert severities[0] is Severity.CRITICAL


@pytest.mark.parametrize(("days", "expected"), [(30, set()), (91, {"alice"})])
def test_only_active_keys_older_than_90_days_are_flagged(account, days, expected):
    now = datetime.now(UTC) + timedelta(days=days)
    result = run_scan(account["session"], now=now)
    users = {f.resource_id.split("/")[0] for f in result.findings if f.rule_id == "IAM-001"}
    assert users == expected  # bob's key is inactive, so never flagged


def test_a_failing_check_is_reported_and_others_still_run(account, monkeypatch):
    def denied(_ec2):
        raise ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "not authorized"}},
            "DescribeSecurityGroups",
        )

    monkeypatch.setattr(checks, "check_security_groups", denied)
    result = run_scan(account["session"])

    assert [e.check for e in result.errors] == ["security_groups"]
    assert "AccessDenied" in result.errors[0].error
    assert any(f.rule_id == "S3-001" for f in result.findings), "S3 checks still ran"


# ----------------------------------------------------------- remediation -----


def test_dry_run_plans_but_changes_nothing(account):
    session = account["session"]
    before = run_scan(session).findings
    results = remediate(before, apply=False, session=session)
    after = run_scan(session).findings

    assert after == before, "a dry run must not change any resource"
    status = {(r.rule_id, r.resource_id): r.status for r in results}
    groups = account["groups"]
    assert status[("S3-001", "risky-opted-in-bucket")] == "PLANNED"
    assert status[("EC2-001", groups["ssh-open"])] == "PLANNED"
    assert status[("EC2-002", groups["all-open"])] == "PLANNED"
    assert status[("S3-001", "risky-untagged-bucket")] == "SKIPPED"
    assert status[("S3-002", "risky-opted-in-bucket")] == "SKIPPED"  # no automatic fix exists


def test_apply_fixes_only_opted_in_resources(account):
    session = account["session"]
    results = remediate(run_scan(session).findings, apply=True, session=session)
    remaining = _keys(run_scan(session).findings, account["groups"])

    applied = sorted((r.rule_id, r.status) for r in results if r.status == "APPLIED")
    assert applied == [("EC2-001", "APPLIED"), ("EC2-002", "APPLIED"), ("S3-001", "APPLIED")]
    assert ("S3-001", "risky-opted-in-bucket") not in remaining
    assert ("EC2-001", "ssh-open") not in remaining
    assert ("EC2-002", "all-open") not in remaining
    # Untagged resources and rules without an automatic fix are left for a human.
    assert ("S3-001", "risky-untagged-bucket") in remaining
    assert ("EC2-001", "rdp-ipv6-untagged") in remaining
    assert ("S3-002", "risky-opted-in-bucket") in remaining


def test_revoke_removes_only_the_internet_range(account):
    session = account["session"]
    remediate(run_scan(session).findings, apply=True, session=session, rules={"EC2-001"})

    [rule] = _ingress(account, "ssh-open")
    assert [r["CidrIp"] for r in rule["IpRanges"]] == ["10.0.0.0/8"], "private access kept"
    assert (rule["FromPort"], rule["ToPort"]) == (22, 22)


def test_rule_filter_limits_what_is_touched(account):
    session = account["session"]
    results = remediate(run_scan(session).findings, apply=True, session=session, rules={"S3-001"})
    assert {r.rule_id for r in results} == {"S3-001"}
    assert ("EC2-002", "all-open") in _keys(run_scan(session).findings, account["groups"])


def test_failed_fix_is_reported_not_raised(account, monkeypatch):
    def broken(_session, _finding):
        raise ClientError({"Error": {"Code": "Throttling", "Message": "slow down"}}, "PutX")

    action, opted_in, _ = remediation_module.REMEDIATIONS["S3-001"]
    monkeypatch.setitem(remediation_module.REMEDIATIONS, "S3-001", (action, opted_in, broken))
    results = remediate(
        run_scan(account["session"]).findings, apply=True, session=account["session"]
    )
    failed = [r for r in results if r.status == "FAILED"]
    assert [(r.rule_id, r.resource_id) for r in failed] == [("S3-001", "risky-opted-in-bucket")]


# --------------------------------------------- scheduled Lambda + CLI --------


def test_scheduled_lambda_never_applies_changes(account, monkeypatch):
    monkeypatch.setenv("REMEDIATION_DRY_RUN", "false")  # must be ignored
    before = run_scan(account["session"]).findings

    summary = handler.lambda_handler({}, None)

    assert run_scan(account["session"]).findings == before
    assert summary["applied"] == 0
    assert summary["fixes_planned"] == 3
    assert summary["by_severity"] == {"CRITICAL": 1, "HIGH": 5, "MEDIUM": 2, "LOW": 2}


def test_cli_scan_exit_codes_and_json(account, capsys):
    assert cli.main(["scan"]) == 1  # HIGH findings exist
    assert cli.main(["scan", "--fail-on", "CRITICAL"]) == 1  # the all-open group
    capsys.readouterr()
    assert cli.main(["scan", "--format", "json", "--fail-on", "CRITICAL"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["findings"]) == 10 and payload["errors"] == []


def test_cli_remediate_is_a_dry_run_unless_apply_is_given(account, capsys):
    before = run_scan(account["session"]).findings
    assert cli.main(["remediate"]) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "PLANNED" in out and "APPLIED" not in out
    assert run_scan(account["session"]).findings == before

    assert cli.main(["remediate", "--apply"]) == 0
    assert "APPLIED" in capsys.readouterr().out
    assert len(run_scan(account["session"]).findings) == 7
