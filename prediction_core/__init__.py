"""Prediction Core v1 — Luis Herrera sports desk (MLB / NFL / CFB).

Produces calibrated model probabilities, joins open/best/close prices,
applies locked CLEAR_RULES_SEP15 gates, and emits candidate JSON for
the sole CLEAR writer (Main).

Never invents MODEL%, lines, injuries, or stats. Soccer is OFF.
Luis scores side pass/fail first; CLV is internal for promote/kill.
"""

from prediction_core.gates import evaluate_ticket
from prediction_core.pipeline import CANDIDATES_SCHEMA_VERSION, run_slate

__version__ = "1.0.0"

__all__ = [
    "CANDIDATES_SCHEMA_VERSION",
    "__version__",
    "evaluate_ticket",
    "run_slate",
]
