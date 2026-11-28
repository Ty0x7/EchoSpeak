"""Check product versions before packaging. Run with Python 3.11 or newer."""
import ast
import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def python_constant(path, name):
    for statement in ast.parse((ROOT / path).read_text(encoding="utf-8")).body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in statement.targets
        ):
            return ast.literal_eval(statement.value)
    return None


def main():
    native = ROOT / "apps/desktop/src-tauri"
    version = read_json("apps/desktop/src-tauri/tauri.conf.json")["version"]
    if not re.fullmatch(r"(?:0|[1-9]\d*)(?:\.(?:0|[1-9]\d*)){2}", version):
        sys.exit(f"Expected a major.minor.patch milestone in tauri.conf.json, found {version!r}")
    crate = tomllib.loads((native / "Cargo.toml").read_text())["package"]
    lock = tomllib.loads((native / "Cargo.lock").read_text())
    versions = {
        "Cargo.toml": crate["version"],
        "Cargo.lock (desktop crate)": next(
            (p["version"] for p in lock["package"] if p["name"] == crate["name"] and "source" not in p), None
        ),
        "backend": python_constant("apps/backend/version.py", "APP_VERSION"),
        "terminal": python_constant("apps/terminal/echospeak_cli/__init__.py", "__version__"),
    }
    for app in ("apps/desktop", "apps/web"):
        versions[f"{app}/package.json"] = read_json(f"{app}/package.json")["version"]
        npm_lock = read_json(f"{app}/package-lock.json")
        versions[f"{app}/package-lock.json"] = npm_lock["version"]
        versions[f"{app}/package-lock.json (root package)"] = npm_lock["packages"][""]["version"]
    errors = [f"{path}: expected {version}, found {value!r}" for path, value in versions.items() if value != version]
    if not (ROOT / f"docs/releases/v{version}.md").is_file():
        errors.append(f"Missing docs/releases/v{version}.md")
    if errors:
        sys.exit("\n".join(errors))
    print(f"EchoSpeak {version}: package versions, runtime declarations and release note agree")


if __name__ == "__main__":
    main()
