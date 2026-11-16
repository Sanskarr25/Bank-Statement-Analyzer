"""The contract every bank parser follows.

To add a bank: subclass BankParser, implement `parse()`, register it in
parsers/__init__.py. Nothing else in the app needs to change.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from ..extractor import Page


@dataclass
class Transaction:
    date: date
    description: str            # cleaned, single-line text from the statement
    debit: float = 0.0          # money that left the account
    credit: float = 0.0         # money that came in
    balance: Optional[float] = None
    needs_review: bool = False  # True if OCR/parsing was unsure about this row
    auto_fixed: bool = False    # True if the balance chain was used to repair a misread number


@dataclass
class ParsedStatement:
    bank: str
    account_last4: str = ""
    period_from: Optional[date] = None
    period_to: Optional[date] = None
    opening_balance: Optional[float] = None
    closing_balance: Optional[float] = None
    # Totals printed by the bank on the statement (used to double-check our parsing)
    stated_total_debits: Optional[float] = None
    stated_total_credits: Optional[float] = None
    stated_debit_count: Optional[int] = None
    transactions: list[Transaction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class BankParser(ABC):
    bank_id: str = ""      # short id used by the API / dropdown, e.g. "sbi"
    bank_name: str = ""    # label shown to the user

    @abstractmethod
    def parse(self, pages: list[Page]) -> ParsedStatement:
        """Turn extracted pages into transactions. Raise ValueError with a
        user-friendly message if this doesn't look like the right statement."""


# ----------------------------------------------------------------- shared helpers
_MONEY = re.compile(r"^\(?[\d.,]*\d[.,]\d{2}\)?(?:CR|DR|Cr|Dr)?$")


def looks_like_money(text: str) -> bool:
    return bool(_MONEY.match(text))


def parse_amount(text: str) -> Optional[float]:
    """'10,41,697.07' / '1,561.28CR' / '1.561.28' (OCR slip) -> float."""
    s = re.sub(r"[^\d.,]", "", text)
    if len(s) < 4 or s[-3] not in ".,":
        return None
    whole = re.sub(r"[.,]", "", s[:-3])
    if not whole:
        whole = "0"
    return float(f"{whole}.{s[-2:]}")
