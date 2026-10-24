"""Structured logging and CloudWatch metrics.

Logs are one JSON object per line, which CloudWatch Logs Insights can query by
field. Metrics use CloudWatch Embedded Metric Format (EMF): a specially shaped
log line that CloudWatch turns into metrics automatically, so the function
needs no extra API calls or IAM permissions to publish them.
"""

import json
import sys
import time
from datetime import UTC, datetime

NAMESPACE = "DataPipeline"


def log(level: str, message: str, **fields) -> None:
    entry = {"time": datetime.now(UTC).isoformat(), "level": level, "message": message, **fields}
    print(json.dumps(entry, default=str), file=sys.stdout, flush=True)


def emit_metrics(metrics: dict[str, int], **dimensions: str) -> None:
    """Write one EMF log line containing several Count metrics."""
    payload = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": NAMESPACE,
                    "Dimensions": [list(dimensions)],
                    "Metrics": [{"Name": name, "Unit": "Count"} for name in metrics],
                }
            ],
        },
        **dimensions,
        **metrics,
    }
    print(json.dumps(payload), file=sys.stdout, flush=True)
