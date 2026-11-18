# -*- coding: utf-8 -*-
"""
HR CV Screening - AI pipeline.

This module contains the existing AI pipeline taken from the Gradio prototype
(untitled3.py) and refactored only to:

  1. Read the OpenRouter API key from an environment variable
     (the prototype read it from Google Colab's userdata).
  2. Return structured data instead of pre-formatted text, so the web
     frontend can render it.

The AI pipeline itself is unchanged:

  PDF Upload -> Extract CV Text -> LLM Context-Aware Chunking + Metadata
  -> OpenAI text-embedding-3-small (via OpenRouter) -> FAISS Vector Store
  -> Semantic Search / Filtering / Ranking / RAG Chat
  -> GPT-4o-mini (via OpenRouter) for generation
"""
from dotenv import load_dotenv
load_dotenv()
import json
import os
import re
from pathlib import Path

import faiss
import numpy as np
from openai import OpenAI
from pypdf import PdfReader

# ---------------------------------------------------------------------------
# OpenRouter configuration (unchanged from the prototype)
# ---------------------------------------------------------------------------

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

if not OPENROUTER_API_KEY:
    raise RuntimeError(
        "OPENROUTER_API_KEY environment variable is missing. "
        "Set it before starting the server, e.g.: "
        "$env:OPENROUTER_API_KEY='your-key'"
    )

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

LLM_MODEL = "openai/gpt-4o-mini"
EMBEDDING_MODEL = "openai/text-embedding-3-small"

# Candidate workflow statuses
CANDIDATE_STATUSES = ["New", "Under Review", "Shortlisted", "Rejected"]

# ---------------------------------------------------------------------------
# In-memory store (same approach as the prototype - no external database)
# ---------------------------------------------------------------------------

candidates = []          # list of processed candidate dicts
chunks = []              # list of indexed chunk dicts
faiss_index = None       # FAISS vector index over the chunks
last_ranking_results = []  # most recent ranking run, for comparison/CSV export


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def extract_text_from_pdf(pdf_file):
    """Extract all text from a PDF file."""
    reader = PdfReader(pdf_file)
    text = ""
    for page in reader.pages:
        page_text = page.extract_text()
        if page_text:
            text += page_text + "\n"
    return text


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

def clean_json_response(text):
    """Remove markdown code fences and parse the LLM JSON output."""
    text = text.strip()
    text = re.sub(r"^```json", "", text, flags=re.MULTILINE)
    text = re.sub(r"^```", "", text, flags=re.MULTILINE)
    text = text.strip()
    return json.loads(text)


def process_cv_with_llm(cv_text, file_name):
    """Extract candidate metadata and split the CV into meaningful chunks."""
    prompt = f"""
You are processing a candidate CV for an HR screening system.

Analyze the CV and return ONLY valid JSON.

Extract the candidate metadata and split the CV into meaningful sections.

Use this exact format:

{{
    "metadata": {{
        "candidate_name": "",
        "job_title": "",
        "years_of_experience": 0,
        "skills": [],
        "education": ""
    }},

    "chunks": [
        {{
            "section": "professional_summary",
            "content": ""
        }},
        {{
            "section": "experience",
            "content": ""
        }},
        {{
            "section": "projects",
            "content": ""
        }},
        {{
            "section": "skills",
            "content": ""
        }},
        {{
            "section": "education",
            "content": ""
        }}
    ]
}}

Rules:

1. Return only valid JSON.
2. Do not invent information.
3. If information is missing, use an empty string, empty list, or 0.
4. Estimate years_of_experience only from actual work experience.
5. Create chunks based on meaning and sections.
6. Do not create extremely small chunks.

CV file name:
{file_name}

CV content:

{cv_text}
"""

    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
    )
    return clean_json_response(response.choices[0].message.content)


def create_embedding(text):
    """Create a text embedding using text-embedding-3-small via OpenRouter."""
    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=text,
    )
    return np.array(response.data[0].embedding, dtype="float32")


# ---------------------------------------------------------------------------
# CV processing
# ---------------------------------------------------------------------------

def summarize_candidate(candidate):
    """Small public view of a candidate (used for lists and cards)."""
    metadata = candidate["metadata"]
    return {
        "candidate_id": candidate["candidate_id"],
        "name": metadata.get("candidate_name", "Unknown"),
        "job_title": metadata.get("job_title", ""),
        "years_of_experience": metadata.get("years_of_experience", 0),
        "skills": metadata.get("skills", []),
        "education": metadata.get("education", ""),
        "file_name": candidate["file_name"],
        "status": candidate.get("status", "New"),
    }


def set_candidate_status(candidate_id, status):
    """Update the workflow status of a candidate. Returns the updated summary."""
    if status not in CANDIDATE_STATUSES:
        return None

    for candidate in candidates:
        if candidate["candidate_id"] == candidate_id:
            candidate["status"] = status
            return summarize_candidate(candidate)
    return None


def delete_candidates(candidate_ids):
    """
    Remove the given candidates from the in-memory store, including their
    chunks, and rebuild the FAISS index from the remaining candidates.

    Returns a summary of the deletion.
    """
    global candidates, chunks, faiss_index, last_ranking_results

    ids = set(int(cid) for cid in candidate_ids)
    if not ids or not candidates:
        return {"deleted": 0, "remaining": len(candidates)}

    before = len(candidates)
    candidates = [c for c in candidates if c["candidate_id"] not in ids]
    chunks = [chunk for chunk in chunks if chunk["candidate_id"] not in ids]

    if chunks:
        embeddings = np.array([chunk["embedding"] for chunk in chunks], dtype="float32")
        faiss_index = faiss.IndexFlatL2(embeddings.shape[1])
        faiss_index.add(embeddings)
    else:
        faiss_index = None

    last_ranking_results = [
        r for r in last_ranking_results if r["candidate_id"] not in ids
    ]

    return {"deleted": before - len(candidates), "remaining": len(candidates)}


def process_uploaded_cvs(uploaded_files):
    """
    Process a list of PDF file paths through the full pipeline.

    Returns a dict with the processing summary and the list of candidates.
    """
    global candidates, chunks, faiss_index

    # Reset the in-memory store (same behaviour as the prototype)
    candidates = []
    chunks = []
    faiss_index = None

    all_embeddings = []

    for candidate_id, pdf_file in enumerate(uploaded_files):
        file_name = Path(pdf_file).name
        try:
            # -------------------------
            # Extract PDF text
            # -------------------------
            cv_text = extract_text_from_pdf(pdf_file)
            if not cv_text.strip():
                continue

            # -------------------------
            # Smart Chunking + Metadata
            # -------------------------
            processed_cv = process_cv_with_llm(
                cv_text=cv_text,
                file_name=file_name,
            )
            metadata = processed_cv["metadata"]

            # -------------------------
            # Save candidate
            # -------------------------
            candidate = {
                "candidate_id": candidate_id,
                "file_name": file_name,
                "metadata": metadata,
                "full_text": cv_text,
                "status": "New",
            }
            candidates.append(candidate)

            # -------------------------
            # Process chunks + embed
            # -------------------------
            for chunk in processed_cv["chunks"]:
                content = chunk.get("content", "")
                if not content.strip():
                    continue

                embedding = create_embedding(content)
                all_embeddings.append(embedding)

                chunks.append({
                    "candidate_id": candidate_id,
                    "candidate_name": metadata.get("candidate_name", ""),
                    "file_name": file_name,
                    "section": chunk.get("section", "unknown"),
                    "content": content,
                    "metadata": metadata,
                    "embedding": embedding,
                })

        except Exception as e:
            print(f"Error processing {file_name}: {e}")

    # -------------------------
    # Create FAISS index
    # -------------------------
    if all_embeddings:
        embeddings_array = np.array(all_embeddings, dtype="float32")
        dimension = embeddings_array.shape[1]
        faiss_index = faiss.IndexFlatL2(dimension)
        faiss_index.add(embeddings_array)

    return {
        "processed": len(candidates),
        "total_chunks": len(chunks),
        "vector_count": faiss_index.ntotal if faiss_index else 0,
        "candidates": [summarize_candidate(c) for c in candidates],
    }


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search_cvs(query, top_k=10):
    """Semantic search over the FAISS index. Returns ranked chunks."""
    if faiss_index is None:
        return []

    query_embedding = np.array([create_embedding(query)], dtype="float32")
    distances, indices = faiss_index.search(query_embedding, top_k)

    results = []
    for distance, index in zip(distances[0], indices[0]):
        if index == -1:
            continue
        chunk = chunks[index]
        results.append({
            "candidate_id": chunk["candidate_id"],
            "candidate_name": chunk["candidate_name"],
            "file_name": chunk["file_name"],
            "section": chunk["section"],
            "content": chunk["content"],
            "distance": float(distance),
        })
    return results


# ---------------------------------------------------------------------------
# Candidate details
# ---------------------------------------------------------------------------

def get_candidate_detail(candidate_id):
    """Full view of one candidate: metadata, chunks and raw CV text."""
    for candidate in candidates:
        if candidate["candidate_id"] == candidate_id:
            candidate_chunks = [
                {"section": c["section"], "content": c["content"]}
                for c in chunks
                if c["candidate_id"] == candidate_id
            ]
            return {
                "candidate_id": candidate["candidate_id"],
                "file_name": candidate["file_name"],
                "metadata": candidate["metadata"],
                "chunks": candidate_chunks,
                "full_text": candidate["full_text"],
            }
    return None


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def filter_candidates(min_years=0, skills=None):
    """Filter candidates by minimum years of experience and required skills."""
    if not candidates:
        return []

    skills = [s.strip().lower() for s in (skills or []) if s.strip()]

    results = []
    for candidate in candidates:
        metadata = candidate["metadata"]
        years = metadata.get("years_of_experience", 0)

        if years < min_years:
            continue

        candidate_skills = [s.lower() for s in metadata.get("skills", [])]
        if not all(skill in candidate_skills for skill in skills):
            continue

        results.append(summarize_candidate(candidate))
    return results


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

def rank_candidates(job_description):
    """Rank all candidates against a job description (score, summary, strengths)."""
    if not candidates:
        return []

    ranked_candidates = []

    for candidate in candidates:
        candidate_id = candidate["candidate_id"]

        # Gather all chunks for this candidate as context
        candidate_chunks = [
            chunk for chunk in chunks
            if chunk["candidate_id"] == candidate_id
        ]
        candidate_context = ""
        for chunk in candidate_chunks:
            candidate_context += f"""
Section: {chunk["section"]}

{chunk["content"]}

----------------------
"""

        prompt = f"""
You are an HR candidate screening system.

Compare this candidate against the job description.

Return ONLY valid JSON.

Use exactly this format:

{{
    "score": 0,
    "summary": "",
    "strengths": [],
    "missing_requirements": []
}}

Score from 0 to 100.

Do not invent experience or skills.

JOB DESCRIPTION:

{job_description}


CANDIDATE:

Name:
{candidate["metadata"].get("candidate_name", "")}

Job Title:
{candidate["metadata"].get("job_title", "")}

Years of Experience:
{candidate["metadata"].get("years_of_experience", 0)}

Skills:
{candidate["metadata"].get("skills", [])}


CV INFORMATION:

{candidate_context}
"""

        try:
            response = client.chat.completions.create(
                model=LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
            )
            result = clean_json_response(response.choices[0].message.content)

            ranked_candidates.append({
                "candidate_id": candidate_id,
                "name": candidate["metadata"].get("candidate_name", "Unknown"),
                "file_name": candidate["file_name"],
                "score": result.get("score", 0),
                "summary": result.get("summary", ""),
                "strengths": result.get("strengths", []),
                "missing_requirements": result.get("missing_requirements", []),
            })

        except Exception as e:
            print(f"Ranking error: {e}")

    ranked_candidates.sort(key=lambda x: x["score"], reverse=True)
    last_ranking_results = ranked_candidates
    return ranked_candidates


# ---------------------------------------------------------------------------
# RAG Chat
# ---------------------------------------------------------------------------

def chat_with_cvs(question):
    """Answer an HR question using FAISS retrieval + GPT-4o-mini generation."""
    if not candidates:
        return "Please upload CVs first."

    results = search_cvs(query=question, top_k=10)
    if not results:
        return "No relevant information was found."

    context = ""
    for i, result in enumerate(results):
        context += f"""

Result {i + 1}

Candidate:
{result["candidate_name"]}

File:
{result["file_name"]}

Section:
{result["section"]}

Content:
{result["content"]}

----------------------------
"""

    prompt = f"""
You are an AI assistant for an HR team.

Answer the HR question using ONLY the information
provided in the CV context.

Do not invent information.

If the answer is not available in the CVs,
say that clearly.

When mentioning a candidate,
mention their name.

HR Question:

{question}


CV Context:

{context}
"""

    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

def get_dashboard_stats():
    """Summary statistics shown on the dashboard page."""
    years = [
        c["metadata"].get("years_of_experience", 0)
        for c in candidates
    ]
    return {
        "total_candidates": len(candidates),
        "shortlisted": sum(
            1 for c in candidates if c.get("status") == "Shortlisted"
        ),
        "average_years": round(sum(years) / len(years), 1) if years else 0,
        "processed_cvs": len(candidates),
    }


def get_last_ranking():
    """The most recent ranking run, used for comparison and CSV export."""
    return last_ranking_results
