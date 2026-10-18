"""Repository entrypoint for the same helper shipped with the desktop application."""
from pathlib import Path
import runpy

main = runpy.run_path(str(Path(__file__).resolve().parents[1] / "apps/backend/resources/project_handoff.py"))["main"]

if __name__ == "__main__":
    raise SystemExit(main())
