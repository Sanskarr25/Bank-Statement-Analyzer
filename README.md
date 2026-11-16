# Khata – Bank Statement Analyzer

Upload a bank statement PDF and get a simple dashboard: money in, money out, where it went,
month-by-month trends, balance over time, biggest payments, and a searchable transaction list.

* **Backend:** Python + FastAPI (+ pandas for the maths)
* **Frontend:** one HTML page, plain JavaScript, Chart.js (bundled, works offline). No React, no build step.
* **Banks:** SBI for now. Adding another bank is one file (see below).
* **Privacy:** runs on your own computer. The uploaded PDF is deleted as soon as it has been read.

## Setup

1. **Python 3.10+**
2. **Tesseract OCR** (only needed for scanned / "Print to PDF" statements, like the sample one)
   * Windows: install from https://github.com/UB-Mannheim/tesseract/wiki and add it to PATH
   * macOS: `brew install tesseract`
   * Ubuntu/Debian: `sudo apt install tesseract-ocr`
3. Install and run:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

Open http://127.0.0.1:8000, choose the PDF, click **Analyze statement**.
If the PDF has a password, type it in the password box.

A long scanned statement takes a few minutes (about 4 s per page per CPU core; the app uses up to 6 cores).
PDFs with a real text layer are read in seconds.

Run the tests: `pip install -r requirements-dev.txt && pytest -q`

## How it works

```
PDF ──> extractor.py ──> Pages (words + positions + table lines)
                              │
                parsers/sbi.py ──> Transactions (date, debit, credit, balance)
                              │
                categorizer.py ──> kind (UPI/ATM/…), payee, category
                              │
                analysis.py ───> numbers for the dashboard
                              │
                main.py (FastAPI) ──> static/index.html + app.js + Chart.js
```

**Reading scanned PDFs.** If a page has no text layer it is OCR'd with Tesseract. Each table row is found
from the horizontal lines of the table.

**Catching misread numbers.** OCR can misread a digit. Every row is checked with the running balance
(`previous balance ± amount = new balance`). If a row doesn't fit, the next row decides which number was
wrong and it is repaired. At the end, the parsed totals are compared with the totals SBI prints on the last
page, and the dashboard shows a green "everything matches" bar or a yellow warning with the rows to check.

**Categories.** `app/data/categories.json` is a plain list of keywords per category. Edit it, restart, done.
UPI payments are also split into *Shops & QR payments* (merchant QR codes) and *Sent to people*
(personal UPI IDs / phone numbers). Statements don't say what you bought, so names are the only clue;
add a keyword (for example a shop's name) to move it to the right category.

## Adding another bank (e.g. HDFC, ICICI)

You only touch two places.

**1. Create `app/parsers/hdfc.py`**

```python
from ..extractor import Page
from .base import BankParser, ParsedStatement, Transaction, parse_amount, looks_like_money

class HDFCParser(BankParser):
    bank_id = "hdfc"                      # used by the API
    bank_name = "HDFC Bank"               # shown in the dropdown

    def parse(self, pages: list[Page]) -> ParsedStatement:
        st = ParsedStatement(bank=self.bank_name)
        for page in pages:
            for w in page.words:          # every word has text, x0, x1, top, bottom (PDF points)
                ...
        # st.transactions.append(Transaction(date=..., description=..., debit=..., credit=..., balance=...))
        return st
```

**2. Register it in `app/parsers/__init__.py`**

```python
from .hdfc import HDFCParser
PARSERS = {SBIParser.bank_id: SBIParser, HDFCParser.bank_id: HDFCParser}
```

The dropdown, the API, the categories and the whole dashboard pick it up automatically.

**A practical recipe for a new bank**

1. Get a sample statement and look at one page with `pdftotext -layout sample.pdf -` (text PDF) or open it and check
   whether the text is selectable. Scanned PDFs are handled by the extractor already.
2. Write down the column order, the date format (`02/04/2025`, `02 Apr 2025`, `2025-04-02`), how debit and credit are
   shown (two columns, or one amount with `Dr/Cr`, or a minus sign) and how a row ends.
3. In `parse()`, group `page.words` into rows (see `_bands_from_rules` / `_bands_from_anchors` in `sbi.py`,
   you can reuse them), then pick out the date, the amounts and the description text.
4. Fill `opening_balance`, `stated_total_debits` and `stated_total_credits` if the statement prints them.
   The dashboard then double-checks your parsing against the bank's own totals.
5. Copy `tests/test_sbi_parser.py`, make a tiny fake statement for the new bank and assert on the result.

Shared helpers in `parsers/base.py`: `parse_amount("1,23,456.78CR") -> 123456.78`, `looks_like_money(text)`.

## API

| Method | Path | What |
|---|---|---|
| GET | `/api/banks` | supported banks |
| POST | `/api/upload` | form fields `file`, `bank`, optional `password` → `{job_id}` |
| GET | `/api/jobs/{id}` | `{status, progress, message, error}` |
| GET | `/api/jobs/{id}/dashboard?month=2025-06` | all dashboard numbers (month optional) |
| GET | `/api/jobs/{id}/transactions` | every transaction |
| GET | `/api/jobs/{id}/export.csv` | CSV download |

Interactive docs: http://127.0.0.1:8000/docs

## Known limits

* SBI statements in the `DD/MM/YYYY` table layout (like the sample) are supported. Other SBI layouts (for example
  `2 Apr 2025` dates) need a small change in `sbi.py` (`_DATE` and `_to_date`).
* Jobs are kept in memory for one hour; restarting the server clears them.
* Merchant names inside UPI payments are cut at a fixed width by the bank, so a name can occasionally lose a space.
