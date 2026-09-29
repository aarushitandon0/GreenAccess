"""Rules-driven trade-off engine (MASTERSPEC §10).

Finds where an accessibility fix and a carbon fix pull the same way (synergy)
or against each other (tension). Deterministic, offline, and independent of the
LLM.
"""

from app.tradeoffs.engine import evaluate, load_rules

__all__ = ["evaluate", "load_rules"]
