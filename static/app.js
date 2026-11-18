/* ==========================================================================
   HR CV Screening - frontend logic
   Talks to the FastAPI backend (main.py) via fetch().
   ========================================================================== */

const STATUSES = ["New", "Under Review", "Shortlisted", "Rejected"];

const api = {
  // Dashboard statistics
  dashboard: () => fetch("/api/dashboard"),
  // Process uploaded CVs through the pipeline
  processCvs: (files) => {
    const form = new FormData();
    for (const file of files) form.append("files", file);
    return fetch("/api/candidates/process", { method: "POST", body: form });
  },
  // List candidates, optionally filtered by min_years and skills
  listCandidates: (minYears = 0, skills = "") => {
    const params = new URLSearchParams({ min_years: minYears, skills });
    return fetch(`/api/candidates?${params}`);
  },
  // Full detail of one candidate
  candidateDetail: (id) => fetch(`/api/candidates/${id}`),
  // Change the workflow status of a candidate
  updateStatus: (id, status) =>
    fetch(`/api/candidates/${id}/status`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    }),
  // Delete candidates (and rebuild the FAISS index on the server)
  deleteCandidates: (ids) =>
    fetch("/api/candidates/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ candidate_ids: ids }),
    }),
  // Most recent ranking run (used by comparison + CSV export)
  latestRanking: () => fetch("/api/ranking/latest"),
  // Rank candidates against a job description
  rankCandidates: (jobDescription) =>
    fetch("/api/rank", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_description: jobDescription }),
    }),
  // Ask a question about the CVs (RAG chat)
  chat: (question) =>
    fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    }),
};

const $ = (selector) => document.querySelector(selector);
const esc = (value) =>
  String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------

document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    const panel = $(`#tab-${btn.dataset.tab}`);
    panel.classList.add("active");
    // Refresh candidate list each time the Candidates tab is opened
    if (btn.dataset.tab === "candidates") loadCandidates();
    if (btn.dataset.tab === "dashboard") loadDashboard();
    if (btn.dataset.tab === "shortlist") loadShortlist();
  });
});

// ---------------------------------------------------------------------------
// 0) Dashboard
// ---------------------------------------------------------------------------

async function loadDashboard() {
  try {
    const response = await api.dashboard();
    const stats = await response.json();
    if (!response.ok) throw new Error(stats.detail || "Failed to load dashboard.");

    const values = $("#dashboard-stats").querySelectorAll(".stat-value");
    values[0].textContent = stats.total_candidates;
    values[1].textContent = stats.shortlisted;
    values[2].textContent = stats.average_years;
    values[3].textContent = stats.processed_cvs;
  } catch (error) {
    $("#dashboard-stats").innerHTML = `<p class="status error">Error: ${esc(error.message)}</p>`;
  }
}

// Load dashboard on initial page load
loadDashboard();

// ---------------------------------------------------------------------------
// Helpers: status display + candidate cards
// ---------------------------------------------------------------------------

function setStatus(element, message, isError = false) {
  element.textContent = message;
  element.classList.toggle("error", isError);
  element.classList.remove("hidden");
}

function skillsChips(skills) {
  return (skills || [])
    .map((skill) => `<span class="chip">${esc(skill)}</span>`)
    .join("");
}

function statusSelect(candidateId, currentStatus) {
  return `
    <select class="status-select" data-status-id="${candidateId}">
      ${STATUSES.map(
        (s) => `<option value="${s}" ${s === currentStatus ? "selected" : ""}>${s}</option>`
      ).join("")}
    </select>`;
}

// Track which candidates are selected for comparison (persists across re-renders)
const selectedIds = new Set();

function candidateCard(c) {
  const checked = selectedIds.has(c.candidate_id) ? "checked" : "";
  return `
    <div class="candidate-card ${checked ? "selected" : ""}" data-card-id="${c.candidate_id}">
      <input type="checkbox" class="compare-check" data-compare-id="${c.candidate_id}" ${checked} title="Select for comparison" />
      <h3>${esc(c.name)}</h3>
      <div class="job-title">${esc(c.job_title || "No job title")}</div>
      <div class="meta">Experience: ${c.years_of_experience} years</div>
      <div class="meta">File: ${esc(c.file_name)}</div>
      <div>${skillsChips(c.skills)}</div>
      <div class="status-select-wrap">Status: ${statusSelect(c.candidate_id, c.status)}</div>
      <button class="btn" data-detail-id="${c.candidate_id}">View details</button>
    </div>`;
}

// ---------------------------------------------------------------------------
// 1) Upload CVs
// ---------------------------------------------------------------------------

const fileInput = $("#file-input");
const dropArea = $("#drop-area");
const fileList = $("#file-list");
const processBtn = $("#process-btn");
const uploadStatus = $("#upload-status");

let selectedFiles = [];

function renderFileList() {
  fileList.innerHTML = "";
  selectedFiles.forEach((file) => {
    const li = document.createElement("li");
    li.textContent = file.name;
    fileList.appendChild(li);
  });
  processBtn.disabled = selectedFiles.length === 0;
}

fileInput.addEventListener("change", () => {
  selectedFiles = Array.from(fileInput.files);
  renderFileList();
});

dropArea.addEventListener("click", () => fileInput.click());

dropArea.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropArea.classList.add("dragover");
});

dropArea.addEventListener("dragleave", () => dropArea.classList.remove("dragover"));

dropArea.addEventListener("drop", (e) => {
  e.preventDefault();
  dropArea.classList.remove("dragover");
  selectedFiles = Array.from(e.dataTransfer.files).filter(
    (file) => file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf")
  );
  renderFileList();
});

processBtn.addEventListener("click", async () => {
  uploadStatus.classList.add("hidden");
  processBtn.disabled = true;
  processBtn.textContent = "Processing...";

  try {
    const response = await api.processCvs(selectedFiles);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Processing failed.");

    setStatus(
      uploadStatus,
      `Done! ${data.processed} CV(s) processed, ${data.total_chunks} chunks, ${data.vector_count} vectors indexed.` +
        (data.errors?.length ? `\nWarnings:\n${data.errors.join("\n")}` : "")
    );

    // Show the processed candidates as cards
    $("#upload-results").classList.remove("hidden");
    $("#result-summary").textContent = `${data.candidates.length} candidate(s) in the system.`;
    $("#result-cards").innerHTML = data.candidates.map(candidateCard).join("");
    bindDetailButtons();
    bindStatusSelects();
    loadDashboard();
  } catch (error) {
    setStatus(uploadStatus, `Error: ${error.message}`, true);
  } finally {
    processBtn.disabled = false;
    processBtn.textContent = "Process CVs";
  }
});

// ---------------------------------------------------------------------------
// 2) Candidates + filter
// ---------------------------------------------------------------------------

async function loadCandidates() {
  const minYears = $("#filter-years").value || 0;
  const skills = $("#filter-skills").value.trim();

  try {
    const response = await api.listCandidates(minYears, skills);
    const candidates = await response.json();
    if (!response.ok) throw new Error(candidates.detail || "Failed to load candidates.");

    $("#candidates-count").textContent = `${candidates.length} candidate(s) found.`;
    $("#candidates-grid").innerHTML =
      candidates.length === 0
        ? '<p class="muted">No candidates yet. Upload CVs in the Upload tab first.</p>'
        : candidates.map(candidateCard).join("");
    bindDetailButtons();
    bindStatusSelects();
  } catch (error) {
    $("#candidates-grid").innerHTML = `<p class="status error">Error: ${esc(error.message)}</p>`;
  }
}

$("#filter-btn").addEventListener("click", loadCandidates);

// ---------------------------------------------------------------------------
// Status change
// ---------------------------------------------------------------------------

async function changeStatus(candidateId, newStatus) {
  try {
    const response = await api.updateStatus(candidateId, newStatus);
    if (!response.ok) {
      const data = await response.json();
      throw new Error(data.detail || "Failed to update status.");
    }
    loadDashboard();
  } catch (error) {
    alert(`Error updating status: ${error.message}`);
  }
}

function bindStatusSelects() {
  document.querySelectorAll("[data-status-id]").forEach((select) => {
    select.addEventListener("change", (event) => {
      changeStatus(Number(select.dataset.statusId), select.value);
    });
  });
}

// ---------------------------------------------------------------------------
// Selection tracking + Compare / Delete
// ---------------------------------------------------------------------------

const compareBtn = $("#compare-btn");
const deleteSelectedBtn = $("#delete-selected-btn");

function updateCompareButton() {
  compareBtn.disabled = selectedIds.size < 2 || selectedIds.size > 4;
  deleteSelectedBtn.classList.toggle("hidden", selectedIds.size === 0);
  $("#compare-note").textContent =
    selectedIds.size > 4
      ? "Too many candidates selected. Please select up to 4."
      : "Select 2 to 4 candidates with the checkboxes to compare.";
}

document.addEventListener("change", (event) => {
  const checkbox = event.target.closest("[data-compare-id]");
  if (!checkbox) return;

  const id = Number(checkbox.dataset.compareId);
  if (checkbox.checked) selectedIds.add(id);
  else selectedIds.delete(id);

  const card = document.querySelector(`[data-card-id="${id}"]`);
  if (card) card.classList.toggle("selected", checkbox.checked);
  updateCompareButton();
});

compareBtn.addEventListener("click", openComparison);

deleteSelectedBtn.addEventListener("click", async () => {
  const ids = [...selectedIds];
  if (ids.length === 0) return;

  const confirmed = confirm(
    `Are you sure you want to delete ${ids.length} selected candidate(s)? This action cannot be undone.`
  );
  if (!confirmed) return;

  try {
    const response = await api.deleteCandidates(ids);
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Failed to delete candidates.");

    selectedIds.clear();
    updateCompareButton();
    loadDashboard();
    if ($("#tab-candidates").classList.contains("active")) loadCandidates();
    if ($("#tab-shortlist").classList.contains("active")) loadShortlist();
  } catch (error) {
    alert(`Error deleting candidates: ${error.message}`);
  }
});

async function openComparison() {
  const ids = [...selectedIds];
  if (ids.length < 2 || ids.length > 4) return;

  const body = $("#compare-body");
  body.innerHTML = "<p class='muted'>Loading...</p>";
  $("#compare-modal").classList.remove("hidden");

  try {
    const detailsResponses = await Promise.all(ids.map((id) => api.candidateDetail(id)));
    const details = await Promise.all(detailsResponses.map((r) => r.json()));
    const rankingResponse = await api.latestRanking();
    const ranking = rankingResponse.ok ? await rankingResponse.json() : [];

    const scores = {};
    ranking.forEach((r) => (scores[r.candidate_id] = r.score));

    const row = (label, getter) => `
      <tr>
        <td class="row-label">${esc(label)}</td>
        ${details
          .map((d) => {
            const value = getter(d);
            return `<td>${value === "" || value == null ? "<span class='muted'>-</span>" : value}</td>`;
          })
          .join("")}
      </tr>`;

    body.innerHTML = `
      <table class="compare-table">
        <thead>
          <tr>
            <th></th>
            ${details
              .map((d) => `<th>${esc(d.metadata?.candidate_name || "Unknown")}</th>`)
              .join("")}
          </tr>
        </thead>
        <tbody>
          ${row("Status", (d) => esc(d.status || "New"))}
          ${row("Job title", (d) => esc(d.metadata?.job_title || ""))}
          ${row("Years of experience", (d) => d.metadata?.years_of_experience ?? 0)}
          ${row("Match score", (d) => {
            const score = scores[d.candidate_id];
            return score == null ? "-" : `${score}/100`;
          })}
          ${row("Education", (d) => esc(d.metadata?.education || ""))}
          ${row(
            "Skills",
            (d) => (d.metadata?.skills || []).map((s) => `<span class="chip">${esc(s)}</span>`).join("")
          )}
          ${row("File", (d) => esc(d.file_name))}
        </tbody>
      </table>`;
  } catch (error) {
    body.innerHTML = `<p class="status error">Error: ${esc(error.message)}</p>`;
  }
}

// ---------------------------------------------------------------------------
// 3) Shortlist
// ---------------------------------------------------------------------------

async function loadShortlist() {
  try {
    const response = await api.listCandidates();
    const candidates = await response.json();
    if (!response.ok) throw new Error(candidates.detail || "Failed to load shortlist.");

    const shortlisted = candidates.filter((c) => c.status === "Shortlisted");
    $("#shortlist-count").textContent = `${shortlisted.length} shortlisted candidate(s).`;
    $("#shortlist-grid").innerHTML =
      shortlisted.length === 0
        ? '<p class="muted">No shortlisted candidates yet.</p>'
        : shortlisted.map(candidateCard).join("");
    bindDetailButtons();
    bindStatusSelects();
  } catch (error) {
    $("#shortlist-grid").innerHTML = `<p class="status error">Error: ${esc(error.message)}</p>`;
  }
}

// ---------------------------------------------------------------------------
// 3) Candidate details modal
// ---------------------------------------------------------------------------

function bindDetailButtons() {
  document.querySelectorAll("[data-detail-id]").forEach((btn) => {
    btn.addEventListener("click", () => openDetail(btn.dataset.detailId));
  });
}

async function openDetail(candidateId) {
  const body = $("#detail-body");
  body.innerHTML = "<p class='muted'>Loading...</p>";
  $("#detail-modal").classList.remove("hidden");

  try {
    const response = await api.candidateDetail(candidateId);
    const detail = await response.json();
    if (!response.ok) throw new Error(detail.detail || "Failed to load candidate.");

    const meta = detail.metadata || {};
    body.innerHTML = `
      <h2>${esc(meta.candidate_name || "Unknown")}</h2>
      <p class="muted">${esc(meta.job_title || "No job title")} &middot; ${meta.years_of_experience} years &middot; ${esc(detail.file_name)}</p>
      <div>${skillsChips(meta.skills)}</div>

      <div class="detail-section">
        <h4>Education</h4>
        ${esc(meta.education || "Not provided")}
      </div>

      ${(detail.chunks || [])
        .map(
          (chunk) => `
            <div class="detail-section">
              <h4>${esc(chunk.section)}</h4>
              ${esc(chunk.content)}
            </div>`
        )
        .join("")}

      <details>
        <summary>Raw CV text</summary>
        <div class="detail-section" style="margin-top:8px">${esc(detail.full_text)}</div>
      </details>
    `;
  } catch (error) {
    body.innerHTML = `<p class="status error">Error: ${esc(error.message)}</p>`;
  }
}

document.querySelectorAll("[data-close-modal]").forEach((el) => {
  el.addEventListener("click", () => $("#detail-modal").classList.add("hidden"));
});

document.querySelectorAll("[data-close-modal-compare]").forEach((el) => {
  el.addEventListener("click", () => $("#compare-modal").classList.add("hidden"));
});

// ---------------------------------------------------------------------------
// 4) Rank candidates
// ---------------------------------------------------------------------------

$("#rank-btn").addEventListener("click", async () => {
  const jobDescription = $("#job-description").value.trim();
  const status = $("#rank-status");
  const resultsEl = $("#rank-results");
  if (!jobDescription) {
    setStatus(status, "Please enter a job description.", true);
    return;
  }

  status.classList.add("hidden");
  $("#rank-btn").disabled = true;
  $("#rank-btn").textContent = "Ranking...";
  resultsEl.innerHTML = "";

  try {
    const response = await api.rankCandidates(jobDescription);
    const ranked = await response.json();
    if (!response.ok) throw new Error(ranked.detail || "Ranking failed.");

    if (ranked.length === 0) {
      resultsEl.innerHTML = '<p class="muted">No candidates to rank. Upload CVs first.</p>';
      return;
    }

    resultsEl.innerHTML = ranked
      .map((c, index) => {
        // Hide the "Missing Requirements" section when empty
        const missing = (c.missing_requirements || []).length
          ? `<div class="block-title">Missing Requirements</div>
             <ul>${c.missing_requirements.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>`
          : "";

        return `
          <div class="rank-item">
            <div class="rank-header">
              <h3>#${index + 1} &middot; ${esc(c.name)}</h3>
              <span class="score-badge">${c.score}/100</span>
            </div>
            <div class="file">${esc(c.file_name)}</div>
            <p><strong>Summary:</strong> ${esc(c.summary)}</p>
            <div class="block-title">Strengths</div>
            <ul>${(c.strengths || []).map((s) => `<li>${esc(s)}</li>`).join("")}</ul>
            ${missing}
          </div>`;
      })
      .join("");
  } catch (error) {
    setStatus(status, `Error: ${error.message}`, true);
  } finally {
    $("#rank-btn").disabled = false;
    $("#rank-btn").textContent = "Rank Candidates";
  }
});

// ---------------------------------------------------------------------------
// 5) Chat with CVs (RAG)
// ---------------------------------------------------------------------------

function addMessage(role, text) {
  const log = $("#chat-log");
  const msg = document.createElement("div");
  msg.className = `msg ${role}`;
  msg.textContent = text;
  log.appendChild(msg);
  log.scrollTop = log.scrollHeight;
}

async function sendChat() {
  const input = $("#chat-input");
  const question = input.value.trim();
  if (!question) return;

  const status = $("#chat-status");
  status.classList.add("hidden");

  addMessage("user", question);
  input.value = "";
  $("#chat-send").disabled = true;

  try {
    const response = await api.chat(question);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Chat request failed.");
    addMessage("assistant", data.answer);
  } catch (error) {
    addMessage("assistant", `Error: ${error.message}`);
  } finally {
    $("#chat-send").disabled = false;
    input.focus();
  }
}

$("#chat-send").addEventListener("click", sendChat);
$("#chat-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") sendChat();
});
