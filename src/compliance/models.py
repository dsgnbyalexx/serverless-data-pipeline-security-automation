from dataclasses import asdict, dataclass, field
from enum import IntEnum


class Severity(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


@dataclass(frozen=True)
class Finding:
    rule_id: str
    title: str
    severity: Severity
    resource_type: str
    resource_id: str
    detail: str
    recommendation: str
    # Extra data a remediation needs (e.g. which port and CIDR to revoke).
    context: dict = field(default_factory=dict, compare=False)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["severity"] = self.severity.name
        return data


@dataclass(frozen=True)
class CheckError:
    """A check that could not run (e.g. AccessDenied). Reported, never silently ignored."""

    check: str
    error: str


@dataclass(frozen=True)
class RemediationResult:
    rule_id: str
    resource_id: str
    action: str
    status: str  # PLANNED | APPLIED | SKIPPED | FAILED
    reason: str = ""
