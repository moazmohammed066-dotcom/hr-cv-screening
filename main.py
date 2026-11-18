# -*- coding: utf-8 -*-
"""
HR CV Screening - FastAPI backend.

Serves a small REST API used by the frontend and the static frontend files.

Run locally with:
    uvicorn main:app --reload
"""

import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import pipeline

# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class RankRequest(BaseModel):
    job_description: str


class ChatRequest(BaseModel):
    question: str


class StatusRequest(BaseModel):
    status: str


class DeleteCandidatesRequest(BaseModel):
    candidate_ids: list[int]


class FilterRequest(BaseModel):
    min_years: float = 0
    skills: list[str] = []


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="HR CV Screening")

STATIC_DIR = Path(__file__).parent / "static"
UPLOAD_DIR = Path(tempfile.gettempdir()) / "hr_cv_uploads"


# ---------------------------------------------------------------------------
# API: upload + process CVs
# ---------------------------------------------------------------------------

@app.post("/api/candidates/process")
async def process_cvs(files: list[UploadFile] = File(...)):
    """
    Upload one or more PDF CVs and run the existing processing pipeline
    (extract -> LLM chunking + metadata -> embeddings -> FAISS index).
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded.")

    UPLOAD_DIR.mkdir(exist_ok=True)

    # Save the uploaded files to disk so the pipeline can read them by path
    saved_paths = []
    for upload in files:
        if not upload.filename.lower().endswith(".pdf"):
            continue
        destination = UPLOAD_DIR / upload.filename
        with destination.open("wb") as buffer:
            shutil.copyfileobj(upload.file, buffer)
        saved_paths.append(str(destination))

    if not saved_paths:
        raise HTTPException(status_code=400, detail="Upload at least one PDF file.")

    # Run the blocking pipeline off the event loop
    try:
        result = await run_in_threadpool(pipeline.process_uploaded_cvs, saved_paths)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return result


# ---------------------------------------------------------------------------
# API: list + filter candidates
# ---------------------------------------------------------------------------

@app.get("/api/dashboard")
async def dashboard():
    """Summary statistics for the dashboard page."""
    return pipeline.get_dashboard_stats()


@app.get("/api/ranking/latest")
async def latest_ranking():
    """The most recent ranking run."""
    return pipeline.get_last_ranking()


@app.get("/api/candidates")
async def list_candidates(min_years: float = 0, skills: str = ""):
    """List all candidates, optionally filtered by min years and skills."""
    skill_list = [s for s in skills.split(",") if s.strip()] if skills else []
    return pipeline.filter_candidates(min_years=min_years, skills=skill_list)


@app.get("/api/candidates/{candidate_id}")
async def candidate_detail(candidate_id: int):
    """Return the full details of a single candidate."""
    detail = pipeline.get_candidate_detail(candidate_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Candidate not found.")
    return detail


@app.post("/api/candidates/{candidate_id}/status")
async def update_candidate_status(candidate_id: int, request: StatusRequest):
    """Change the workflow status of a candidate (New / Under Review / Shortlisted / Rejected)."""
    updated = pipeline.set_candidate_status(candidate_id, request.status)
    if updated is None:
        raise HTTPException(status_code=404, detail="Candidate not found or invalid status.")
    return updated


@app.post("/api/candidates/delete")
async def delete_candidates(request: DeleteCandidatesRequest):
    """Delete the given candidates and rebuild the FAISS index from the rest."""
    if not request.candidate_ids:
        raise HTTPException(status_code=400, detail="No candidate IDs provided.")
    result = await run_in_threadpool(pipeline.delete_candidates, request.candidate_ids)
    if result["deleted"] == 0:
        raise HTTPException(status_code=404, detail="No candidates found to delete.")
    return result


# ---------------------------------------------------------------------------
# API: ranking
# ---------------------------------------------------------------------------

@app.post("/api/rank")
async def rank_candidates(request: RankRequest):
    """Rank all candidates against a job description."""
    if not request.job_description.strip():
        raise HTTPException(status_code=400, detail="Job description is empty.")
    return await run_in_threadpool(pipeline.rank_candidates, request.job_description)


# ---------------------------------------------------------------------------
# API: RAG chat
# ---------------------------------------------------------------------------

@app.post("/api/chat")
async def chat(request: ChatRequest):
    """Answer a question about the uploaded candidates (FAISS + LLM)."""
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="Question is empty.")
    return {"answer": await run_in_threadpool(pipeline.chat_with_cvs, request.question)}


# ---------------------------------------------------------------------------
# Serve the frontend (must be mounted last so it does not shadow the API)
# ---------------------------------------------------------------------------

app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
