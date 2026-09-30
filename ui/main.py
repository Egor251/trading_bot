"""Entry: python ui/main.py → same as python -m ui"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.__main__ import main

if __name__ == "__main__":
    main()
