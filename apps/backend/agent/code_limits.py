"""Provider-reported subscription windows only; no token-to-quota estimates."""
import math
import time


def _number(value, lower, upper=math.inf):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and lower <= value <= upper)


def windows(payload):
    if not isinstance(payload, dict):
        return []
    buckets = payload.get('rateLimitsByLimitId')
    buckets = list(buckets.values()) if isinstance(buckets, dict) else [payload.get('rateLimits', payload)]
    result = []
    for bucket in buckets[:20]:
        if not isinstance(bucket, dict):
            continue
        for key in ('primary', 'secondary'):
            window = bucket.get(key)
            if not isinstance(window, dict):
                continue
            value = window.get('usedPercent')
            if not _number(value, 0, 100):
                continue
            label = str(bucket.get('limitName') or bucket.get('limitId') or 'Plan')[:80]
            row = {'label': f'{label} · {key}', 'used_percent': value}
            for source, target in [('resetsAt', 'resets_at'), ('windowDurationMins', 'window_minutes')]:
                number = window.get(source)
                if _number(number, 1):
                    row[target] = number
            result.append(row)
    return result


def codex(payload):
    rows = windows(payload)
    return {'windows': rows, 'observed_at': time.time(), 'source': 'Codex account/rateLimits/read',
            'unavailable': '' if rows else 'This connection did not report subscription windows.'}


def claude(payload):
    info = payload if isinstance(payload, dict) else {}
    status = info.get('status')
    row = {'windows': [], 'observed_at': time.time(), 'source': 'Claude Code rate_limit_event',
           'status': status if isinstance(status, str) and status in {'allowed', 'allowed_warning', 'rejected'} else 'unknown',
           'unavailable': 'Claude Code has not reported a numeric subscription window.'}
    used, reset = info.get('utilization'), info.get('resetsAt')
    if _number(reset, 1):
        row['resets_at'] = reset
    if _number(used, 0, 1):
        window = {'label': 'Reported plan window', 'used_percent': round(used * 100, 2)}
        if _number(reset, 1):
            window['resets_at'] = reset
        row.update(windows=[window], unavailable='')
    return row
