"""Allowlisted local observations. No prompt, transcript, credential or raw log export."""
import argparse
import json
import platform
import time
from pathlib import Path


def snapshot(supervisor=None):
    from version import APP_VERSION
    from agent import privacy
    observed = {"schema_version": 1, "version": APP_VERSION, "observed_at": time.time(),
                "platform": platform.system(), "python": platform.python_version(), "privacy_mode": privacy.mode(),
                "scope": "This local host only; no remote routing or provider health inference", "resources": {}}
    try:
        import psutil
        memory, process = psutil.virtual_memory(), psutil.Process()
        observed["resources"] = {"cpu_percent": psutil.cpu_percent(interval=0.05), "memory_total_bytes": memory.total,
                                 "memory_available_bytes": memory.available, "backend_rss_bytes": process.memory_info().rss}
    except (ImportError, OSError):
        observed["resources"] = {"unavailable": "Resource observations unavailable"}
    if supervisor:
        observed["coding_runtimes"] = [{"runtime": s.row["runtime_id"], "state": s.row["state"], "active": bool(s.task),
                                        "pending_decisions": len(s.approvals)} for s in supervisor.sessions.values()]
    observed["excluded"] = ["credentials", "prompts", "conversation text", "raw logs", "file paths", "provider account identifiers"]
    return observed


def main():
    parser = argparse.ArgumentParser(description="Create a local EchoSpeak triage report. Never uploads or calls a model.")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    path = Path(args.output)
    # Do not silently overwrite a prior report or any user file.
    with path.open("x", encoding="utf-8") as handle:
        json.dump(snapshot(), handle, indent=2)
    print("Saved local triage report")


if __name__ == "__main__":
    main()
