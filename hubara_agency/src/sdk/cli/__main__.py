"""``uv run python -m src.sdk.cli <verbo>`` — entry point del CLI hubara."""
from __future__ import annotations

import sys

from src.sdk.cli import main

if __name__ == "__main__":
    sys.exit(main())
