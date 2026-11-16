"""Tiny in-memory job runner: upload -> background processing -> poll for progress."""
from __future__ import annotations

import os
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from . import analysis
from .extractor import ExtractionError, load_pages
from .parsers import get_parser

_pool = ThreadPoolExecutor(max_workers=1)      # one statement at a time keeps the laptop responsive
JOBS: dict[str, "Job"] = {}
KEEP_SECONDS = 60 * 60


@dataclass
class Job:
    id: str
    status: str = "queued"                      # queued | reading | analyzing | done | error
    progress: int = 0
    message: str = "Waiting to start…"
    error: Optional[str] = None
    created: float = field(default_factory=time.time)
    df: Optional[pd.DataFrame] = None
    meta: dict = field(default_factory=dict)


def submit(path: str, bank_id: str, password: Optional[str]) -> Job:
    _cleanup()
    job = Job(id=uuid.uuid4().hex[:12])
    JOBS[job.id] = job
    _pool.submit(_run, job, path, bank_id, password)
    return job


def _run(job: Job, path: str, bank_id: str, password: Optional[str]) -> None:
    try:
        job.status, job.message = "reading", "Opening your PDF…"

        def on_progress(done: int, total: int, msg: str) -> None:
            job.progress = int(85 * done / max(total, 1))
            job.message = msg

        pages = load_pages(path, password, on_progress)
        job.status, job.progress, job.message = "analyzing", 88, "Finding your transactions…"
        st = get_parser(bank_id).parse(pages)
        df = analysis.build_frame(st)
        if df.empty:
            raise ValueError("No transactions found in this statement.")
        job.progress, job.message = 95, "Building your dashboard…"
        job.meta = {
            "bank": st.bank, "account_last4": st.account_last4,
            "period": [st.period_from.isoformat() if st.period_from else None, st.period_to.isoformat() if st.period_to else None],
            "pages": len(pages), "check": analysis.statement_check(df, st), "warnings": st.warnings,
            "months": analysis.months_list(df),
        }
        job.df = df
        job.status, job.progress, job.message = "done", 100, "Done"
    except (ExtractionError, ValueError) as e:
        job.status, job.error = "error", str(e)
    except Exception:                          # never leak a stack trace to the browser
        traceback.print_exc()
        job.status, job.error = "error", "Something went wrong while reading this file. Please try again or try another statement."
    finally:
        try:
            os.remove(path)                    # the uploaded PDF is deleted as soon as we're done with it
        except OSError:
            pass


def _cleanup() -> None:
    now = time.time()
    for k in [k for k, j in JOBS.items() if now - j.created > KEEP_SECONDS]:
        JOBS.pop(k, None)
