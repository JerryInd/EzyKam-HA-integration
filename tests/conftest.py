"""Pytest bootstrap.

Puts the repository root on ``sys.path`` so ``custom_components.ezykam`` can be
imported as a normal package, exactly the way Home Assistant will import it.
"""

from __future__ import annotations

import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
