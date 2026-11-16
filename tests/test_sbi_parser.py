"""Builds a small SBI-style statement PDF (real text layer, no OCR) and checks the parser.

Run:  pip install pytest reportlab  &&  pytest -q
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app import analysis
from app.extractor import load_pages
from app.parsers import get_parser

# (date, details lines, debit, credit, balance)
ROWS = [
    ("02/04/2025", ["WDL TFR", "UPI/DR/545866594593/CHUNNI", "LAL/YESB/paytmqr5zk/UPI", "0097693162093 AT 18643", "LOHARA"], 55.00, 0, 1561.28),
    ("02/04/2025", ["DEP TFR", "UPI/CR/509274738490/AMIT", "VIJ/BKID/junankgg@o/UPI", "0097735162098 AT 18643", "LOHARA"], 0, 88.00, 1649.28),
    ("05/04/2025", ["DEP TFR", "UPI/CR/346061421815/SUYASH", "/SBIN/9763114550/UPI", "0097735162098 AT 18643", "LOHARA"], 0, 10000.00, 11649.28),
    ("06/04/2025", ["WDL TFR", "UPI/DR/100101451518/Samast", "T/INDB/magicpin.p/Upi", "0097695162091 AT 18643", "LOHARA"], 1076.38, 0, 10572.90),
]
OPENING = 1616.28


def build_pdf(path):
    c = canvas.Canvas(path, pagesize=letter)
    W, H = letter
    c.setFont("Helvetica", 10)
    c.drawString(60, H - 60, "State Bank of India   Account Number : 36115513881")
    c.drawString(60, H - 80, "Statement From : 01-04-2025 to 30-04-2025")
    y = H - 140
    c.line(40, y + 10, W - 40, y + 10)
    for date, lines, dr, cr, bal in ROWS:
        h = 12 * len(lines) + 8
        c.setFont("Helvetica", 8)
        c.drawString(45, y - 10, date)
        c.drawString(105, y - 10, date)
        for i, ln in enumerate(lines):
            c.drawString(170, y - 10 - 12 * i, ln)
        c.drawRightString(440, y - 10, f"{dr:,.2f}" if dr else "-")
        c.drawRightString(500, y - 10, f"{cr:,.2f}" if cr else "-")
        c.drawRightString(570, y - 10, f"{bal:,.2f}")
        y -= h
        c.line(40, y, W - 40, y)
    c.showPage()
    c.setFont("Helvetica", 9)
    td, tc = sum(r[2] for r in ROWS), sum(r[3] for r in ROWS)
    c.drawString(60, H - 60, "Statement Summary : 01-04-2025 To 30-04-2025")
    c.drawString(60, H - 75, "Brought Forward Dr Count Cr Count Total Debits Total Credits Closing Balance")
    c.drawString(60, H - 90, f"{OPENING:,.2f}CR 2 2 {td:,.2f} {tc:,.2f} {ROWS[-1][4]:,.2f}CR")
    c.save()


def test_text_pdf_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "s.pdf")
        build_pdf(p)
        pages = load_pages(p)
        assert all(pg.source == "text" for pg in pages)
        st = get_parser("sbi").parse(pages)

    assert st.account_last4 == "3881"
    assert len(st.transactions) == len(ROWS)
    for t, (date, _, dr, cr, bal) in zip(st.transactions, ROWS):
        assert (t.debit, t.credit, t.balance) == (dr, cr, bal)
        assert not t.needs_review
    assert "CHUNNILAL/YESB/paytmqr5zk/UPI" in st.transactions[0].description

    df = analysis.build_frame(st)
    check = analysis.statement_check(df, st)
    assert check["checked"] and check["ok"]
    assert df.loc[0, "category"] == "Shops & QR payments"      # merchant QR (paytmqr...)
    assert df.loc[1, "category"] == "Received from people"
    assert df.loc[3, "category"] == "Food & Drinks"              # magicpin


def test_inr_format():
    assert analysis.inr(1041697.07) == "₹10,41,697"
    assert analysis.inr(999) == "₹999"
    assert analysis.inr(-123456, 2) == "-₹1,23,456.00"
