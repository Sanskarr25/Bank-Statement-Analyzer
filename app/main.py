"""FastAPI app: serves the web page and a small JSON API."""
from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import analysis, jobs
from .parsers import list_banks

MAX_MB = 60
STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Khata - Bank Statement Analyzer")


@app.get("/")
def home():
    return FileResponse(STATIC / "index.html")


@app.get("/api/banks")
def banks():
    return list_banks()


@app.post("/api/upload")
async def upload(file: UploadFile = File(...), bank: str = Form("sbi"), password: Optional[str] = Form(None)):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF file.")
    if bank not in {b["id"] for b in list_banks()}:
        raise HTTPException(400, "Unsupported bank.")
    fd, path = tempfile.mkstemp(suffix=".pdf")
    size = 0
    with os.fdopen(fd, "wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_MB * 1024 * 1024:
                out.close()
                os.remove(path)
                raise HTTPException(413, f"File is too large (limit {MAX_MB} MB).")
            out.write(chunk)
    job = jobs.submit(path, bank, password)
    return {"job_id": job.id}


def _job(job_id: str) -> jobs.Job:
    job = jobs.JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "This session expired. Please upload the statement again.")
    return job


@app.get("/api/jobs/{job_id}")
def status(job_id: str):
    j = _job(job_id)
    return {"status": j.status, "progress": j.progress, "message": j.message, "error": j.error}


@app.get("/api/jobs/{job_id}/dashboard")
def dashboard(job_id: str, month: Optional[str] = None):
    j = _job(job_id)
    if j.status != "done":
        raise HTTPException(409, "Still processing.")
    return {"meta": j.meta, **analysis.analyze(j.df, month)}


@app.get("/api/jobs/{job_id}/transactions")
def transactions(job_id: str):
    j = _job(job_id)
    if j.status != "done":
        raise HTTPException(409, "Still processing.")
    d = j.df.copy()
    d["date"] = d["date"].dt.strftime("%Y-%m-%d")
    cols = ["id", "date", "month", "description", "debit", "credit", "balance", "kind", "payee", "category", "review", "fixed"]
    return JSONResponse(d[cols].where(d[cols].notna(), None).to_dict("records"))


@app.get("/api/jobs/{job_id}/export.csv")
def export(job_id: str):
    j = _job(job_id)
    if j.status != "done":
        raise HTTPException(409, "Still processing.")
    d = j.df[["date", "description", "debit", "credit", "balance", "kind", "payee", "category"]].copy()
    d["date"] = d["date"].dt.strftime("%Y-%m-%d")
    buf = io.StringIO()
    d.to_csv(buf, index=False)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=transactions.csv"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")
