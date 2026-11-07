"""
Shared logger — everything logs to logs/finance.log (and the console).

    from core.log import get_logger
    log = get_logger()            # or get_logger("chase")
    log.info("message")
"""

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "finance.log"

_configured = False


def get_logger(name: str = "finance") -> logging.Logger:
    global _configured
    LOG_DIR.mkdir(exist_ok=True)
    root = logging.getLogger("finance")
    if not _configured:
        root.setLevel(logging.INFO)
        fmt = logging.Formatter("%(asctime)s [%(name)s] %(levelname)s %(message)s",
                                datefmt="%Y-%m-%d %H:%M:%S")
        fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
        fh.setFormatter(fmt)
        try:
            sh = logging.StreamHandler(sys.stdout)
            sh.stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        root.addHandler(fh)
        root.addHandler(sh)
        _configured = True
    return root if name in ("finance", None) else root.getChild(name)
