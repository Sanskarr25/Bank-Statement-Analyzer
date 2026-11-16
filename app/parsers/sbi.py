"""State Bank of India - 'Statement of Account' PDF (email / OnlineSBI style).

Columns: Value Date | Post Date | Details | Ref No/Cheque No | Debit | Credit | Balance
Dates look like 02/04/2025. Amounts use Indian grouping (10,41,697.07).

How rows are found
  1. The table has horizontal rules between rows -> each gap between two rules is one row
     (if a PDF has no rules we fall back to splitting at each date).
  2. In a row: the date(s) on the left, the last two money values on the right
     (amount, balance), everything else is the description.
  3. Debit vs credit and OCR misreads are settled with the running balance:
     previous balance -/+ amount must equal the new balance.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from ..extractor import Page, Word
from .base import BankParser, ParsedStatement, Transaction, looks_like_money, parse_amount

_DIGITS = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "|": "1"})
_DATE = re.compile(r"^[\dOolI|]{2}/[\dOolI|]{2}/[\dOolI|]{4}$")
_TOL = 0.006


def _to_date(text: str) -> Optional[date]:
    try:
        d, m, y = text.translate(_DIGITS).split("/")
        return date(int(y), int(m), int(d))
    except (ValueError, TypeError):
        return None


@dataclass
class _Row:
    page: int
    date: Optional[date]
    lines: list[str]
    money: list[float]
    hint: Optional[str]          # "debit" / "credit" guessed from the text
    debit: float = 0.0
    credit: float = 0.0
    balance: Optional[float] = None
    review: bool = False
    fixed: bool = False


class SBIParser(BankParser):
    bank_id = "sbi"
    bank_name = "State Bank of India (SBI)"

    # ------------------------------------------------------------------ public
    def parse(self, pages: list[Page]) -> ParsedStatement:
        whole = " ".join(p.text() for p in pages)
        if not re.search(r"State\s+Bank\s+of\s+India|\bSBI\b|SBIN", whole, re.I):
            raise ValueError("This doesn't look like an SBI statement. Please pick the right bank or upload an SBI 'Statement of Account' PDF.")

        st = ParsedStatement(bank=self.bank_name)
        self._read_header_and_summary(pages, st)

        rows: list[_Row] = []
        for p in pages:
            rows.extend(self._rows_on_page(p))
        rows = [r for r in rows if _in_period(r.date, st)]
        if not rows:
            raise ValueError("No transactions were found in this PDF. Is it the 'Statement of Account' with the transaction table?")

        self._settle_with_balances(rows, st.opening_balance)

        prev_date = st.period_from
        for r in rows:
            if r.date is None:
                r.date, r.review = prev_date, True
            prev_date = r.date or prev_date
            if r.date is None:
                continue
            st.transactions.append(Transaction(
                date=r.date, description=_tidy(r.lines), debit=r.debit, credit=r.credit,
                balance=r.balance, needs_review=r.review, auto_fixed=r.fixed))
        if st.transactions:
            st.closing_balance = st.closing_balance if st.closing_balance is not None else st.transactions[-1].balance
        return st

    # ------------------------------------------------------------------ header + summary
    def _read_header_and_summary(self, pages: list[Page], st: ParsedStatement) -> None:
        for p in pages:
            t = p.text()
            if st.account_last4 == "":
                m = re.search(r"Account\s*Number\s*[:\-]?\s*(\d{6,})", t)
                if m:
                    st.account_last4 = m.group(1)[-4:]
            if st.period_from is None:
                m = re.search(r"Statement\s*From\s*[:\-]?\s*(\d{2})-(\d{2})-(\d{4})\s*to\s*(\d{2})-(\d{2})-(\d{4})", t, re.I)
                if m:
                    d1, m1, y1, d2, m2, y2 = map(int, m.groups())
                    st.period_from, st.period_to = date(y1, m1, d1), date(y2, m2, d2)
        # Closing summary: "Brought Forward | Dr Count | Cr Count | Total Debits | Total Credits | Closing Balance"
        for p in reversed(pages[-4:]):
            t = p.text()
            if re.search(r"Brought\s*Forward", t, re.I) and re.search(r"Total\s*Debits", t, re.I):
                tail = re.split(r"Closing\s*Balance[^\d]*", t, flags=re.I)[-1]
                money = [parse_amount(x) for x in re.findall(r"\d[\d,]*\.\d{2}", tail)]
                counts = re.findall(r"(?<![\d.,])(\d{1,6})(?![\d.,])", tail)
                if len(money) >= 4:
                    st.opening_balance, st.stated_total_debits, st.stated_total_credits, st.closing_balance = money[:4]
                if counts:
                    st.stated_debit_count = int(counts[0])
                break

    # ------------------------------------------------------------------ rows
    def _rows_on_page(self, page: Page) -> list[_Row]:
        words = _repair_numbers(sorted((w for w in page.words if w.text), key=lambda w: w.x0))
        anchors = [w for w in words if _DATE.match(w.text) and w.x0 < page.width * 0.4]
        if not anchors:
            return []
        bands = _bands_from_rules(page, anchors) or _bands_from_anchors(page, anchors)
        rows = []
        for y0, y1 in bands:
            in_band = [w for w in words if y0 <= (w.top + w.bottom) / 2 < y1]
            row = self._build_row(page.number, in_band)
            if row:
                rows.append(row)
        return rows

    def _build_row(self, page_no: int, in_band: list[Word]) -> Optional[_Row]:
        in_band = sorted(in_band, key=lambda w: w.x0)
        dates = [w for w in in_band if _DATE.match(w.text)][:2]
        money_w = [w for w in in_band if looks_like_money(w.text) and w not in dates]
        money_w = sorted(money_w, key=lambda w: w.x0)[-2:]
        if not dates and len(money_w) < 2:
            return None                                   # header/footer/blank band

        skip = {id(w) for w in dates + money_w}
        rest = [w for w in in_band if id(w) not in skip and re.search(r"[A-Za-z0-9]", w.text)]
        lines = _group_lines(rest)
        text = " ".join(lines).upper()
        hint = None
        if re.search(r"\bDEP\b|/CR/|\bBY TRANSFER|\bCREDIT\b|\bINT\.?\s*PD|\bINTEREST\b", text):
            hint = "credit"
        elif re.search(r"\bWDL\b|/DR/|\bTO TRANSFER|\bDEBIT\b|\bATM\b|\bCHARGES?\b", text):
            hint = "debit"
        return _Row(page_no, _to_date(dates[0].text) if dates else None, lines,
                    [parse_amount(w.text) for w in money_w], hint)

    # ------------------------------------------------------------------ balance chain
    def _settle_with_balances(self, rows: list[_Row], opening: Optional[float]) -> None:
        """Decide debit/credit for every row from the running balance (previous -/+ amount = balance).

        OCR sometimes drops a leading digit ('702.20' read as '02.20'). If the number we expect from the
        chain ends with exactly the digits we read, we accept the expected value (auto-fixed). Anything
        else that doesn't add up is kept as read and flagged for review: we never invent numbers."""
        prev: Optional[float] = opening
        for r in rows:
            vals = [v for v in r.money if v is not None]
            if len(vals) < 2:
                self._single_value(r, vals, prev)
                prev = r.balance if r.balance is not None else prev
                continue
            amt, bal = vals[-2], vals[-1]
            kind = _consistent_kind(prev, amt, bal)
            if kind is None and prev is not None:
                for k, sign in (("debit", -1), ("credit", 1)):
                    exp_bal = round(prev + sign * amt, 2)
                    exp_amt = round(abs(bal - prev), 2)
                    if _ends_with(exp_bal, bal) and (r.hint in (None, k)):
                        bal, kind, r.fixed = exp_bal, k, True          # balance lost a leading digit
                        break
                    if _ends_with(exp_amt, amt) and (bal < prev) == (k == "debit") and (r.hint in (None, k)):
                        amt, kind, r.fixed = exp_amt, k, True          # amount lost a leading digit
                        break
            if kind is None:
                kind = r.hint or ("debit" if (prev is None or bal <= prev) else "credit")
                r.review = prev is not None
            r.debit, r.credit = (amt, 0.0) if kind == "debit" else (0.0, amt)
            r.balance = bal
            prev = bal

    @staticmethod
    def _single_value(r: _Row, vals: list[float], prev: Optional[float]) -> None:
        """Only one number was readable in this row: use the previous balance to work out the rest."""
        r.review = True
        if not vals:
            return
        x = vals[0]
        if prev is not None and r.hint:
            r.debit, r.credit = (x, 0.0) if r.hint == "debit" else (0.0, x)
            r.balance = round(prev - x if r.hint == "debit" else prev + x, 2)
        elif prev is not None:
            d = round(x - prev, 2)                         # x looks like a balance
            r.debit, r.credit = (abs(d), 0.0) if d < 0 else (0.0, abs(d))
            r.balance = x


def _in_period(d: Optional[date], st: ParsedStatement) -> bool:
    """Drop header lines that merely contain a date (e.g. 'Account open Date : 21/09/2016')."""
    if d is None or st.period_from is None or st.period_to is None:
        return True
    return (st.period_from - timedelta(days=5)) <= d <= (st.period_to + timedelta(days=5))


def _ends_with(expected: float, read: float) -> bool:
    return expected != read and f"{expected:.2f}".endswith(f"{read:.2f}")


def _consistent(prev: Optional[float], amt: float, bal: float) -> bool:
    return prev is not None and (abs(prev - amt - bal) < _TOL or abs(prev + amt - bal) < _TOL)


def _consistent_kind(prev: Optional[float], amt: float, bal: float) -> Optional[str]:
    if prev is None:
        return None
    if abs(prev - amt - bal) < _TOL:
        return "debit"
    if abs(prev + amt - bal) < _TOL:
        return "credit"
    return None


# ---------------------------------------------------------------------- OCR repairs
_SPLIT_HEAD = re.compile(r"^\d[\d,]*[.,]$")        # "2."   (OCR cut "2.00" in two)
_SPLIT_TAIL = re.compile(r"^\d{2}$")                 # "00"
_ONE_DECIMAL = re.compile(r"^\d[\d,]*\.\d$")        # "1,012.2" (trailing zero lost)


def _repair_numbers(words: list[Word]) -> list[Word]:
    """Repair split numbers/dates line by line."""
    lines: list[list[Word]] = []
    for w in sorted(words, key=lambda w: w.top):
        if lines and abs(w.top - lines[-1][0].top) < 4:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [w for ln in lines for w in _repair_line(sorted(ln, key=lambda w: w.x0))]


def _repair_line(words: list[Word]) -> list[Word]:
    """Glue '2.' + '00' back into '2.00' and pad '1,012.2' to '1,012.20' (amounts always have 2 decimals)."""
    out: list[Word] = []
    for w in words:
        prev = out[-1] if out else None
        if (prev is not None and _SPLIT_HEAD.match(prev.text) and _SPLIT_TAIL.match(w.text)
                and abs(w.top - prev.top) < 4 and 0 <= w.x0 - prev.x1 < 14):
            out[-1] = Word(prev.text + w.text, prev.x0, w.x1, prev.top, prev.bottom)
            continue
        if (prev is not None and re.fullmatch(r"\d", prev.text) and re.fullmatch(r"\d/\d{2}/\d{4}", w.text)
                and abs(w.top - prev.top) < 4 and 0 <= w.x0 - prev.x1 < 6):
            out[-1] = Word(prev.text + w.text, prev.x0, w.x1, prev.top, prev.bottom)
            continue
        if _ONE_DECIMAL.match(w.text):
            w = Word(w.text + "0", w.x0, w.x1, w.top, w.bottom)
        out.append(w)
    return out


# ---------------------------------------------------------------------- layout helpers
def _bands_from_rules(page: Page, anchors: list[Word]) -> list[tuple[float, float]]:
    """Gaps between consecutive table rules, split further if a gap holds several dates."""
    rules = sorted(page.rules)
    gaps = [(rules[i][1], rules[i + 1][0]) for i in range(len(rules) - 1) if rules[i + 1][0] - rules[i][1] > 10]
    gaps = [g for g in gaps if any(g[0] <= a.top < g[1] for a in anchors)]
    out = []
    for g in gaps:
        inside = sorted(a.top for a in anchors if g[0] <= a.top < g[1])
        cuts = [g[0]] + [(inside[i] + inside[i + 1]) / 2 for i in range(len(inside) - 1)] + [g[1]]
        out.extend(zip(cuts[:-1], cuts[1:]))
    return out


def _bands_from_anchors(page: Page, anchors: list[Word]) -> list[tuple[float, float]]:
    """No rules (e.g. a text PDF): each row runs from its date line to the next date line."""
    tops = sorted({round(a.top, 1) for a in anchors})
    footer = [w.top for w in page.words if w.text.lower() == "page" and w.top > tops[-1]]
    end = min(footer) if footer else page.height
    starts = [t - 2 for t in tops]
    return list(zip(starts, starts[1:] + [end]))


def _group_lines(words: list[Word]) -> list[str]:
    lines: list[list[Word]] = []
    for w in sorted(words, key=lambda w: w.top):
        if lines and abs(w.top - lines[-1][0].top) < 4:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [" ".join(x.text for x in sorted(l, key=lambda w: w.x0)) for l in lines]


def _tidy(lines: list[str]) -> str:
    """Join wrapped lines. SBI wraps at a fixed width, often in the middle of a word, so lines of
    the UPI string (which contains '/') are glued without a space; other lines get a space."""
    out = lines[0] if lines else ""
    for ln in lines[1:]:
        continuing = "/" in out.split(" ")[-1] and not re.match(r"\d{6,}\b", ln)
        out += ln if continuing else " " + ln
    out = re.sub(r"(?:^|(?<=\s))[UY]{1,2}\s?P\s?[Il1!|]\s?/(?=\s?(?:DR|CR)\b)", "UPI/", out)   # OCR: YPI/ YU PI/ UYPI/
    out = re.sub(r"^[^A-Za-z0-9]+", "", out)
    return re.sub(r"\s+", " ", out).strip()
