"""Work out WHAT a transaction is: payment type, who it was with, and a category.

Categories come from data/categories.json (keyword lists you can edit). On top of that,
UPI payments are split into 'Shops & QR payments' (merchant QR codes) and
'Sent to people' / 'Received from people' (personal UPI IDs / phone numbers).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

CATEGORY_FILE = Path(__file__).parent / "data" / "categories.json"

_BRANCH_TAIL = re.compile(r"\s*\d{8,}\s+AT\s+\d+.*$", re.I)       # "0097693162093 AT 18643 LOHARA"
_MERCHANT_VPA = re.compile(r"paytmqr|^paytm\.|^q\d{6,}|bharatpe|^gpay-|^mab\.|@pty|@ibl.*shop|\.rzp|razorpay", re.I)


@dataclass
class Tagged:
    kind: str       # UPI, IMPS, NEFT, RTGS, ATM, Cheque, Card, Charges, Interest, Other
    payee: str      # best-effort name of the other side ("" if unknown)
    category: str


def _load_rules() -> list[tuple[str, re.Pattern]]:
    raw = json.loads(CATEGORY_FILE.read_text(encoding="utf-8"))
    rules = []
    for cat, words in raw.items():
        if cat.startswith("_") or not words:
            continue
        # long keywords also match the start of a longer word (amazon -> amazonpay); short ones must be whole words
        parts = [re.escape(w.lower()) + ("" if len(w) >= 5 else r"(?![a-z0-9])") for w in words]
        pattern = r"(?<![a-z0-9])(?:" + "|".join(parts) + ")"
        rules.append((cat, re.compile(pattern)))
    return rules


_RULES = _load_rules()


def reload_rules() -> None:
    global _RULES
    _RULES = _load_rules()


def tag(description: str, is_debit: bool) -> Tagged:
    text = _BRANCH_TAIL.sub("", description).strip()
    low = text.lower()
    kind, payee, vpa = _kind_and_payee(text)

    # 1) keyword rules (name of shop / app / "salary" / "interest" ...)
    for cat, rx in _RULES:
        if rx.search(low):
            if kind == "ATM" and cat != "Bank charges":
                cat = "Cash withdrawal"
            return Tagged(kind, payee, cat)

    # 2) UPI fall-backs
    if kind == "UPI":
        if _MERCHANT_VPA.search(vpa):
            return Tagged(kind, payee, "Shops & QR payments" if is_debit else "Shop / business receipts")
        return Tagged(kind, payee, "Sent to people" if is_debit else "Received from people")

    # 3) other fall-backs
    if kind in {"IMPS", "NEFT", "RTGS", "Cheque"}:
        return Tagged(kind, payee, "Bank transfers out" if is_debit else "Bank transfers in")
    if kind == "Interest":
        return Tagged(kind, payee, "Interest earned")
    return Tagged(kind, payee, "Other spending" if is_debit else "Other income")


def _kind_and_payee(text: str) -> tuple[str, str, str]:
    up = text.upper()
    m = re.search(r"UPI/(?:DR|CR)/\d+/([^/]*)/([^/]*)/([^/\s]*)", text, re.I)
    if m:
        return "UPI", m.group(1).strip().title(), m.group(3).strip()
    if "UPI" in up:
        return "UPI", "", ""
    if re.search(r"\bATM\b|CASH WDL|CASH WITHDRAWAL", up):
        return "ATM", "", ""
    for k in ("IMPS", "NEFT", "RTGS"):
        if k in up:
            m = re.search(k + r"[^/\-*]*[/\-*]\s*[\dA-Z]*[/\-*]\s*([A-Za-z][A-Za-z .]{2,30})", text)
            return k, (m.group(1).strip().title() if m else ""), ""
    if re.search(r"CHQ|CHEQUE|CLEARING", up):
        return "Cheque", "", ""
    if re.search(r"\bPOS\b|DEBIT CARD|CARD", up):
        return "Card", "", ""
    if re.search(r"CHARGES?|\bAMC\b|\bSMS\b|\bGST\b", up):
        return "Charges", "", ""
    if re.search(r"INT\.?\s*PD|INTEREST", up):
        return "Interest", "", ""
    return "Other", "", ""
