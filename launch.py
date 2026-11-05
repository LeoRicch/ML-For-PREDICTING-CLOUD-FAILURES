"""Shared launcher for the source and bundled offline editions."""
from pathlib import Path
import os
import runpy
import sys
import traceback

ROOT = Path(__file__).resolve().parent


def main():
    os.chdir(ROOT)
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "work").mkdir(exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(ROOT / "work" / "matplotlib"))
    try:
        runpy.run_path(str(ROOT / "app.py"), run_name="__main__")
    except Exception:
        details = traceback.format_exc()
        log = ROOT / "data" / "startup-error.log"
        log.write_text(details, encoding="utf-8")
        print("The app could not start. Details were saved to", log)
        print(details)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
