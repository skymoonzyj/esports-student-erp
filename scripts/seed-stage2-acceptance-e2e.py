#!/usr/bin/env python3
from __future__ import annotations

import importlib
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPOSITORY_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

main = importlib.import_module("app.cli.seed_stage2_acceptance_e2e").main


if __name__ == "__main__":
    raise SystemExit(main())

