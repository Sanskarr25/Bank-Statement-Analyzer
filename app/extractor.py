"""Step 1 of the pipeline: PDF  ->  list of Page objects.

A Page holds every word with its position (in PDF points) plus the horizontal
rules of the table. Bank parsers only ever see Pages, so they don't care whether
the PDF had a real text layer or was a scanned/printed image that needed OCR.
"""
from __future__ import annotations

import os
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Optional

OCR_DPI = 200


class ExtractionError(Exception):
    """Raised with a message that is safe to show to the end user."""


@dataclass
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float

    @property
    def xc(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass
class Page:
    number: int                         # 1-based
    width: float
    height: float
    words: list[Word]
    rules: list[tuple[float, float]]    # (y_start, y_end) of wide horizontal lines/bands
    source: str                         # "text" or "ocr"

    def text(self) -> str:
        return " ".join(w.text for w in sorted(self.words, key=lambda w: (round(w.top / 4), w.x0)))


# --------------------------------------------------------------------------- helpers
def _merge_runs(flags, min_gap: int = 1) -> list[tuple[int, int]]:
    runs, start = [], None
    for y, on in enumerate(flags):
        if on and start is None:
            start = y
        elif not on and start is not None:
            runs.append((start, y - 1))
            start = None
    if start is not None:
        runs.append((start, len(flags) - 1))
    return runs


def _clean_ocr_text(t: str) -> str:
    t = t.strip().strip("|¦[]{}")
    return t.replace("—", "-").replace("–", "-").replace("−", "-").strip()


def dedupe_overlaps(words: list[Word]) -> list[Word]:
    """Tesseract sometimes reports the same text twice at nearly the same spot ('1,012.2' and '012.20').
    Keep one per overlap: prefer something that looks like a date/amount, then the longer text."""
    def score(w: Word) -> tuple[int, int]:
        good = re.fullmatch(r"[\d,]+\.\d{1,2}|[\dOolI|]{1,2}/[\dOolI|]{2}/[\dOolI|]{4}", w.text) is not None
        return (int(good), len(w.text))

    kept: list[Word] = []
    for w in sorted(words, key=lambda w: (-score(w)[0], -score(w)[1])):
        clash = False
        for k in kept:
            if abs(k.top - w.top) < 4:
                overlap = min(k.x1, w.x1) - max(k.x0, w.x0)
                if overlap > 0.5 * min(k.x1 - k.x0, w.x1 - w.x0):
                    clash = True
                    break
        if not clash:
            kept.append(w)
    return sorted(kept, key=lambda w: (w.top, w.x0))


# --------------------------------------------------------------------------- OCR path
def _ocr_chunk(args) -> list[Page]:
    """Runs inside a worker process: OCR a few pages and return Page objects."""
    path, password, indices = args
    import numpy as np
    import pypdfium2 as pdfium
    import pytesseract

    pdf = pdfium.PdfDocument(path, password=password or None)
    scale = 72 / OCR_DPI
    out: list[Page] = []
    for i in indices:
        img = pdf[i].render(scale=OCR_DPI / 72).to_pil().convert("L")
        w, h = img.size
        arr = np.asarray(img)

        # Table rules: rows that are non-white across most of the page width
        mid = arr[:, int(0.1 * w):int(0.9 * w)]
        rules = [(a * scale, b * scale) for a, b in _merge_runs((mid < 240).mean(axis=1) > 0.85)]

        try:
            d = pytesseract.image_to_data(img, config="--psm 6", output_type=pytesseract.Output.DICT)
        except pytesseract.TesseractNotFoundError:
            raise ExtractionError(
                "This PDF is a scanned image, so it needs OCR, but Tesseract is not installed. "
                "See the README for a 2-minute install."
            )
        words = []
        for j, raw in enumerate(d["text"]):
            t = _clean_ocr_text(raw)
            if not t or float(d["conf"][j]) < 0:
                continue
            x0, top = d["left"][j] * scale, d["top"][j] * scale
            words.append(Word(t, x0, x0 + d["width"][j] * scale, top, top + d["height"][j] * scale))
        out.append(Page(i + 1, w * scale, h * scale, dedupe_overlaps(words), rules, "ocr"))
    return out


# --------------------------------------------------------------------------- text path
def _text_page(number: int, p) -> Page:
    words = [Word(w["text"], w["x0"], w["x1"], w["top"], w["bottom"]) for w in p.extract_words()]
    rules = []
    for obj in list(p.lines) + list(p.rects):
        if obj["x1"] - obj["x0"] > 0.5 * p.width:
            rules.append((obj["top"], obj["bottom"]))
    rules = [(a, b) for a, b in _merge_overlaps(sorted(rules))]
    return Page(number, p.width, p.height, words, rules, "text")


def _merge_overlaps(spans):
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1] + 1.5:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


# --------------------------------------------------------------------------- public API
def load_pages(path: str, password: Optional[str] = None,
               progress: Optional[Callable[[int, int, str], None]] = None) -> list[Page]:
    """Read every page. progress(done, total, message) is called as pages finish."""
    import pdfplumber

    progress = progress or (lambda *_: None)
    import pypdfium2 as pdfium

    try:
        doc = pdfium.PdfDocument(path, password=password or None)
    except pdfium.PdfiumError as e:
        if "password" in str(e).lower():
            raise ExtractionError("This PDF is password-protected. Please enter the password and try again.")
        raise ExtractionError("Could not open this file. Is it a valid PDF?")

    total = len(doc)
    # Cheap check first: does the page have a real text layer? (Printed/scanned PDFs don't.)
    has_text = [doc[i].get_textpage().count_chars() >= 40 for i in range(total)]
    doc.close()

    pages: dict[int, Page] = {}
    ocr_needed = [i for i in range(total) if not has_text[i]]
    if any(has_text):
        with pdfplumber.open(path, password=password or None) as plumber:
            for i in range(total):
                if has_text[i]:
                    pages[i + 1] = _text_page(i + 1, plumber.pages[i])

    done = len(pages)
    progress(done, total, "Reading pages…")
    if ocr_needed:
        workers = max(1, min(os.cpu_count() or 1, 6))
        chunks = [ocr_needed[k:k + 4] for k in range(0, len(ocr_needed), 4)]
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_ocr_chunk, (path, password, c)) for c in chunks]
            for f in as_completed(futures):
                try:
                    for pg in f.result():
                        pages[pg.number] = pg
                        done += 1
                except ExtractionError:
                    raise
                except Exception as e:
                    raise ExtractionError(f"Reading the PDF failed: {e}")
                progress(done, total, f"Reading page {done} of {total} (scanned PDF, this can take a few minutes)…")
    return [pages[k] for k in sorted(pages)]
