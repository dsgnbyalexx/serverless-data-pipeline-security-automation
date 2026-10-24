"""Demo: run the compliance CLI against a small, deliberately misconfigured SIMULATED
AWS account (moto). No AWS account or credentials are used.

    python scripts/demo_compliance.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
    os.environ[name] = "demo-not-a-real-credential"
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
os.environ.pop("AWS_PROFILE", None)

import boto3  # noqa: E402  (environment must be set first)
from moto import mock_aws  # noqa: E402

from compliance.cli import main  # noqa: E402
from compliance.remediation import OPT_IN_TAG  # noqa: E402


def build_account() -> None:
    s3, ec2 = boto3.client("s3"), boto3.client("ec2")
    s3.create_bucket(Bucket="demo-public-reports")
    s3.put_bucket_tagging(
        Bucket="demo-public-reports", Tagging={"TagSet": [{"Key": OPT_IN_TAG, "Value": "true"}]}
    )
    vpc_id = ec2.describe_vpcs()["Vpcs"][0]["VpcId"]
    open_ssh = ec2.create_security_group(
        GroupName="demo-bastion",
        Description="SSH open to the world",
        VpcId=vpc_id,
        TagSpecifications=[
            {"ResourceType": "security-group", "Tags": [{"Key": OPT_IN_TAG, "Value": "true"}]}
        ],
    )["GroupId"]
    ec2.authorize_security_group_ingress(
        GroupId=open_ssh,
        IpPermissions=[
            {
                "IpProtocol": "tcp",
                "FromPort": 22,
                "ToPort": 22,
                "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
            }
        ],
    )


def step(title: str, argv: list[str]) -> None:
    print(f"\n$ python -m compliance {' '.join(argv)}    # {title}")
    code = main(argv)
    print(f"(exit code {code})")


if __name__ == "__main__":
    with mock_aws():
        build_account()
        step("1. detect", ["scan"])
        step("2. plan fixes, change nothing", ["remediate"])
        step("3. apply fixes to opted-in resources", ["remediate", "--apply"])
        step("4. re-scan", ["scan"])
