"""
float <-> NUMERIC. The calculator works in float (to be moved to Decimal
later), the database stores NUMERIC; both directions are exact.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional


def to_decimal(x: Optional[float]) -> Optional[Decimal]:
    return None if x is None else Decimal(str(x))  # str: 1.1 stays 1.1, not 1.100000000000000088...


def to_money(x: Optional[float]) -> Optional[Decimal]:
    """Exactly two digits, as NUMERIC(12, 2) returns it: 7560.0 -> 7560.00."""
    return None if x is None else Decimal(str(round(x, 2))).quantize(Decimal("0.01"))


def to_float(x: Optional[Decimal]) -> Optional[float]:
    return None if x is None else float(x)
