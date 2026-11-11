"""Entry point for Rasgulla.

The application lives in ``core_app``; this file only boots it and writes a
startup log if anything goes wrong, so a silent crash is never a mystery.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
LOG_PATH = APP_DIR / "rasgulla-error.log"


def main() -> None:
    if APP_DIR.is_dir() and str(APP_DIR) not in sys.path:
        sys.path.insert(0, str(APP_DIR))
    try:
        from core_app import RasgullaApp

        RasgullaApp().run()
    except Exception:
        try:
            LOG_PATH.write_text(traceback.format_exc(), encoding="utf-8")
        except OSError:
            pass
        raise


if __name__ == "__main__":
    main()
