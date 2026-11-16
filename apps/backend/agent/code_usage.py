"""Only numeric fields actually reported by the original coding harness."""
import math
import time


def safe_usage(value):
    if not isinstance(value, dict):
        return {}
    keys = {"input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
            "total_tokens", "cached_input_tokens", "reasoning_output_tokens"}
    return {key: number for key, number in value.items() if key in keys and isinstance(number, (int, float))
            and not isinstance(number, bool) and math.isfinite(number) and number >= 0}


def record(session, data):
    usage = safe_usage(data.get("usage"))
    cost = data.get("total_cost_usd")
    if not usage and not isinstance(cost, (int, float)):
        return
    row = {"tokens": usage, "observed_at": time.time(), "scope": "Latest completed harness turn, including its tool loop"}
    if isinstance(cost, (int, float)) and not isinstance(cost, bool) and math.isfinite(cost) and cost >= 0:
        row["reported_cost_usd"] = cost
    session.row["reported_usage"] = row
    session.save()
