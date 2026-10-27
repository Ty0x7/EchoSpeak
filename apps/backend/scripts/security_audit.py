"""Check every locked dependency against OSV (osv.dev: GitHub advisories, PyPA, RustSec, npm, malware).

    python apps/backend/scripts/security_audit.py           # report
    python apps/backend/scripts/security_audit.py --json    # machine-readable

Reads exactly what a release is built from: apps/backend/requirements.lock.txt (the backend
the installer ships), apps/web and apps/desktop package-lock.json, and
apps/desktop/src-tauri/Cargo.lock. Exit code 1 when any package has a known advisory, so it
can gate a release (advisories already reviewed as not affecting the Windows build are listed in
REVIEWED and don't fail it; exit 2 means OSV couldn't be reached). Read-only: it sends package
names and versions to api.osv.dev and nothing else.
The record of the October 2026 audit is docs/research/security-audit-2026-10-25.md.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OSV = "https://api.osv.dev/v1"

# Advisories reviewed and found not to affect the Windows build (docs/research/security-audit-2026-10-25.md §4).
# Listed, not counted as failures. Re-review an entry when its package or the build target changes.
_LINUX_ONLY = "Linux/GTK/Wayland only: not compiled for the Windows build (cargo tree --target x86_64-pc-windows-msvc)"
_UNMAINTAINED = "unmaintained notice, no known vulnerability and no fix; pulled in by Tauri"
REVIEWED = {
    "GHSA-wrw7-89jp-8q8g": _LINUX_ONLY, "RUSTSEC-2024-0429": _LINUX_ONLY,  # glib
    "RUSTSEC-2026-0194": _LINUX_ONLY, "RUSTSEC-2026-0195": _LINUX_ONLY,    # quick-xml 0.39 (wayland-scanner)
    "RUSTSEC-2026-0235": _LINUX_ONLY,                                       # rkyv
    "RUSTSEC-2026-0371": _LINUX_ONLY, "RUSTSEC-2026-0370": _LINUX_ONLY, "RUSTSEC-2026-0372": _LINUX_ONLY,  # zbus/zvariant
    "RUSTSEC-2024-0370": _UNMAINTAINED,                                     # proc-macro-error (build time)
    "RUSTSEC-2025-0081": _UNMAINTAINED, "RUSTSEC-2025-0075": _UNMAINTAINED, "RUSTSEC-2025-0080": _UNMAINTAINED,
    "RUSTSEC-2025-0100": _UNMAINTAINED, "RUSTSEC-2025-0098": _UNMAINTAINED,  # unic-* via urlpattern
    # zip 4.6.1 via tauri-plugin-updater: Unix permission/symlink handling in extraction. On Windows the
    # updater runs the signed NSIS installer after checking its signature; fixing needs an updater major.
    "RUSTSEC-2026-0367": "Unix-only extraction issue; Windows updater installs the signed installer",
    "RUSTSEC-2026-0368": "Unix-only extraction issue; Windows updater installs the signed installer",
    "RUSTSEC-2026-0369": "Unix-only extraction issue; Windows updater installs the signed installer",
    # block-buffer 0.10.4 via sha2 0.10 -> tauri-codegen -> tauri-macros (a proc-macro): it only hashes
    # assets inside the compiler, and isn't linked into EchoSpeak.exe (cargo tree -e normal,no-proc-macro
    # --target x86_64-pc-windows-msvc prints nothing). The bug needs a caught panic in a hash callback.
    # The fix is only in 0.12, which digest 0.10 can't use; revisit when Tauri moves to digest 0.11.
    "GHSA-qwgh-2vcv-g2f7": "build-time only (Tauri proc-macro asset hashing); not in the shipped binary",
}


def python_lock(path: Path) -> list[tuple[str, str, str, str]]:
    pin = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)")
    return [("PyPI", m.group(1), m.group(2), "backend (installer)") for line in path.read_text(encoding="utf-8").splitlines()
            if (m := pin.match(line.strip()))]


def npm_lock(path: Path, label: str) -> list[tuple[str, str, str, str]]:
    seen: dict[tuple[str, str], str] = {}
    for key, meta in (json.loads(path.read_text(encoding="utf-8")).get("packages") or {}).items():
        if key and "version" in meta and not meta.get("link"):
            name = meta.get("name") or key.split("node_modules/")[-1]
            seen[(name, meta["version"])] = f"{label} ({'dev' if meta.get('dev') else 'shipped'})"
    return [("npm", name, version, where) for (name, version), where in seen.items()]


def cargo_lock(path: Path) -> list[tuple[str, str, str, str]]:
    out = []
    for block in path.read_text(encoding="utf-8").split("[[package]]")[1:]:
        name, version = re.search(r'name = "([^"]+)"', block), re.search(r'version = "([^"]+)"', block)
        if name and version and "registry+" in block:
            out.append(("crates.io", name.group(1), version.group(1), "desktop shell"))
    return out


def _post(url: str, body: dict) -> dict:
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.loads(response.read())


def scan(packages: list[tuple[str, str, str, str]]) -> list[dict]:
    hits: list[tuple[tuple[str, str, str, str], str]] = []
    for start in range(0, len(packages), 500):
        batch = packages[start:start + 500]
        results = _post(f"{OSV}/querybatch", {"queries": [
            {"package": {"ecosystem": e, "name": n}, "version": v} for e, n, v, _ in batch]})["results"]
        hits += [(pkg, vuln["id"]) for pkg, result in zip(batch, results) for vuln in result.get("vulns") or []]
    details: dict[str, dict] = {}
    findings = []
    for (ecosystem, name, version, where), vid in hits:
        vuln = details.setdefault(vid, _get(f"{OSV}/vulns/{vid}"))
        fixed = [event["fixed"] for affected in vuln.get("affected", [])
                 if (affected.get("package") or {}).get("name", "").lower() == name.lower()
                 for rng in affected.get("ranges", []) for event in rng.get("events", []) if "fixed" in event]
        findings.append({"ecosystem": ecosystem, "package": name, "version": version, "where": where, "id": vid,
                         "aliases": vuln.get("aliases", []), "summary": (vuln.get("summary") or vuln.get("details") or "")[:160],
                         "severity": (vuln.get("database_specific") or {}).get("severity", ""), "fixed": fixed})
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    packages = python_lock(REPO / "apps/backend/requirements.lock.txt")
    packages += npm_lock(REPO / "apps/web/package-lock.json", "web")
    packages += npm_lock(REPO / "apps/desktop/package-lock.json", "desktop")
    packages += cargo_lock(REPO / "apps/desktop/src-tauri/Cargo.lock")
    try:
        findings = scan(packages)
    except OSError as exc:  # offline, DNS, TLS, timeout: not a finding (exit 2, the release script warns)
        print(f"Couldn't reach OSV: {exc}", file=sys.stderr)
        return 2
    new = [f for f in findings if f["id"] not in REVIEWED]
    if args.json:
        print(json.dumps({"new": new, "reviewed": [f for f in findings if f["id"] in REVIEWED]}, indent=1))
    else:
        print(f"Checked {len(packages)} locked packages against OSV: {len(new)} new advisories, "
              f"{len(findings) - len(new)} reviewed as not affecting the Windows build.")
        for f in sorted(new, key=lambda f: (f["ecosystem"], f["package"], f["id"])):
            fix = ", ".join(f["fixed"][-2:]) or "no fix yet"
            print(f"  {f['ecosystem']:9} {f['package']} {f['version']} [{f['where']}] {f['id']} "
                  f"{f['severity'] or '-'}: {f['summary']} (fixed in {fix})")
    return 1 if new else 0


if __name__ == "__main__":
    sys.exit(main())
