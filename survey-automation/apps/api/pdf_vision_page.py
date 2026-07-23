from __future__ import annotations


def pdf_vision_ops_page_html(shared_css: str, shared_js: str, nav_html: str) -> str:
    extra_style = """
  <style>
    .vision-hidden { display: none !important; }
    .vision-step-list { list-style: none; margin: 12px 0 0; padding: 0; }
    .vision-step-list li { padding: 6px 10px; border-radius: 6px; background: var(--card-bg, rgba(127,127,127,0.08)); margin-bottom: 6px; font-size: 13px; }
    .vision-review-layout { display: grid; grid-template-columns: 1.5fr 1fr; gap: 16px; align-items: start; }
    @media (max-width: 1000px) { .vision-review-layout { grid-template-columns: 1fr; } }
    .vision-page-preview { position: sticky; top: 12px; border: 1px solid rgba(127,127,127,0.25); border-radius: 8px; padding: 10px; }
    .vision-page-preview img { max-width: 100%; display: block; border-radius: 4px; }
    .vision-page-frame { position: relative; display: inline-block; max-width: 100%; }
    .vision-bbox { position: absolute; border: 2px solid #e0562f; background: rgba(224, 86, 47, 0.15); pointer-events: none; }
    .vision-row-active { outline: 2px solid #e0562f; outline-offset: -2px; }
    .vision-detail-toggle { cursor: pointer; color: #4a7fd6; font-size: 12px; }
    .vision-detail-body { display: none; white-space: pre-wrap; font-family: monospace; font-size: 12px; margin-top: 6px; padding: 8px; background: rgba(127,127,127,0.08); border-radius: 6px; max-height: 220px; overflow: auto; }
    .vision-detail-body.open { display: block; }
    .vision-answer-input { width: 100%; box-sizing: border-box; }
  </style>
"""

    body = """
<div class="page">
  <div class="page-header">
    <h1>PDF Vision Operations</h1>
    <p>Upload a Common Data Set PDF, let GPT-4o read each page and rewrite every fillable field as a plain-language question, then review Databricks Genie's answers before exporting a filled PDF.</p>
  </div>

  <div id="visionUploadPanel" class="card">
    <h2 style="margin-top:0">1. Upload PDF</h2>
    <input id="visionFileInput" type="file" accept="application/pdf" />
    <button class="btn" onclick="visionUpload()">Upload PDF</button>
    <div id="visionUploadStatus" class="status-line"></div>
  </div>

  <div id="visionProcessingPanel" class="card vision-hidden">
    <h2 style="margin-top:0">2. Process pages</h2>
    <p id="visionPdfSummary" class="page-meta"></p>
    <label>Survey year <input id="visionSurveyYear" type="number" value="2025" style="width:100px" /></label>
    <button class="btn" onclick="visionStartProcessing()">Start Processing</button>
    <button class="btn btn-secondary" onclick="visionShowReview()">Skip to review</button>
    <ul id="visionStepList" class="vision-step-list"></ul>
    <div id="visionProcessStatus" class="status-line"></div>
  </div>

  <div id="visionReviewPanel" class="card vision-hidden">
    <h2 style="margin-top:0">3. Review dashboard</h2>
    <div class="toolbar">
      <button class="btn btn-secondary" onclick="visionLoadQuestions()">Reload</button>
      <button class="btn" onclick="visionExport()">Export Filled PDF</button>
    </div>
    <div id="visionExportStatus" class="status-line"></div>
    <div class="vision-review-layout">
      <div class="table-wrap">
        <table>
          <thead><tr>
            <th style="width:60px">Page</th><th>Question</th><th style="width:120px">Answer</th>
            <th style="width:80px">Confidence</th><th style="width:70px">SQL</th><th style="width:220px">Status</th>
          </tr></thead>
          <tbody id="visionRows"><tr class="empty-row"><td colspan="6">No questions loaded yet.</td></tr></tbody>
        </table>
      </div>
      <div class="vision-page-preview">
        <div id="visionPageFrame" class="vision-page-frame">
          <span class="page-meta">Click a row to preview its page.</span>
        </div>
      </div>
    </div>
  </div>
</div>
"""

    script = """
let visionPdfId = null, visionJobId = null, visionPollHandle = null, visionQuestions = [];

function visionStatus(id, text, type) {
  const el = document.getElementById(id);
  if (!text) { el.className = 'status-line'; el.textContent = ''; return; }
  el.className = `status-line show ${type || 'info'}`;
  el.textContent = text;
}

async function visionUpload() {
  const input = document.getElementById('visionFileInput');
  const file = input.files && input.files[0];
  if (!file) { visionStatus('visionUploadStatus', 'Choose a PDF file first.', 'err'); return; }
  visionStatus('visionUploadStatus', 'Uploading…', 'info');
  try {
    const formData = new FormData();
    formData.append('file', file);
    const result = await apiFetch('/pdf/upload', { method: 'POST', body: formData });
    visionPdfId = result.pdf_id;
    visionStatus('visionUploadStatus', `Uploaded ${result.file_name} (${result.page_count} pages).`, 'ok');
    document.getElementById('visionPdfSummary').textContent =
      `PDF ${result.pdf_id} · ${result.page_count} pages · status ${result.status}`;
    document.getElementById('visionProcessingPanel').classList.remove('vision-hidden');
  } catch (e) {
    visionStatus('visionUploadStatus', 'Upload failed: ' + e.message, 'err');
  }
}

async function visionStartProcessing() {
  if (!visionPdfId) return;
  const surveyYear = parseInt(document.getElementById('visionSurveyYear').value, 10) || 2025;
  document.getElementById('visionStepList').innerHTML = '';
  visionStatus('visionProcessStatus', 'Starting…', 'info');
  try {
    const job = await apiFetch(`/pdf/${visionPdfId}/process`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ survey_year: surveyYear }),
    });
    visionJobId = job.job_id;
    visionStatus('visionProcessStatus', 'Processing started, polling for progress…', 'info');
    if (visionPollHandle) clearInterval(visionPollHandle);
    visionPollHandle = setInterval(visionPollJob, 2000);
  } catch (e) {
    visionStatus('visionProcessStatus', 'Failed to start: ' + e.message, 'err');
  }
}

async function visionPollJob() {
  if (!visionPdfId || !visionJobId) return;
  try {
    const job = await apiFetch(`/pdf/${visionPdfId}/process/${visionJobId}`);
    document.getElementById('visionStepList').innerHTML = (job.steps || [])
      .map(step => `<li>${escHtml(step.name)}</li>`).join('');
    if (job.status === 'completed') {
      clearInterval(visionPollHandle);
      visionStatus('visionProcessStatus', 'Processing complete.', 'ok');
      visionShowReview();
    } else if (job.status === 'failed') {
      clearInterval(visionPollHandle);
      visionStatus('visionProcessStatus', 'Processing failed: ' + (job.error || 'unknown error'), 'err');
    }
  } catch (e) {
    visionStatus('visionProcessStatus', 'Poll error: ' + e.message, 'err');
  }
}

function visionShowReview() {
  document.getElementById('visionReviewPanel').classList.remove('vision-hidden');
  visionLoadQuestions();
}

async function visionLoadQuestions() {
  if (!visionPdfId) return;
  try {
    visionQuestions = await apiFetch(`/pdf/${visionPdfId}/questions`);
    visionRenderRows();
  } catch (e) {
    visionStatus('visionExportStatus', 'Failed to load questions: ' + e.message, 'err');
  }
}

function visionStatusPill(status) {
  const cls = status === 'APPROVED' ? 'pill-good' : status === 'REJECTED' ? 'pill-bad' : 'pill-neutral';
  return `<span class="pill ${cls}">${escHtml(status || 'PENDING_REVIEW')}</span>`;
}

function visionRenderRows() {
  const tbody = document.getElementById('visionRows');
  if (!visionQuestions.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="6">No questions loaded yet.</td></tr>';
    return;
  }
  tbody.innerHTML = visionQuestions.map(row => `
    <tr id="visionRow-${row.question_id}" onclick="visionSelectRow('${row.question_id}')">
      <td>${row.page_number ?? ''}</td>
      <td>
        <div>${escHtml(row.question)}</div>
        <div class="vision-detail-toggle" onclick="event.stopPropagation(); visionToggleDetail('${row.question_id}')">SQL / explanation</div>
        <div id="visionDetail-${row.question_id}" class="vision-detail-body">${escHtml(row.sql || '(no SQL)')}\\n\\n${escHtml(row.explanation || '')}</div>
      </td>
      <td>
        <input class="vision-answer-input" id="visionAnswer-${row.question_id}" value="${escHtml(row.answer)}" onclick="event.stopPropagation()" />
      </td>
      <td>${row.answer_confidence ?? 0}%</td>
      <td><span class="vision-detail-toggle" onclick="event.stopPropagation(); visionToggleDetail('${row.question_id}')">View</span></td>
      <td onclick="event.stopPropagation()">
        ${visionStatusPill(row.status)}
        <div style="margin-top:6px">
          <button class="btn btn-secondary" onclick="visionApprove('${row.question_id}')">Approve</button>
          <button class="btn btn-secondary" onclick="visionSaveEdit('${row.question_id}')">Save edit</button>
          <button class="btn btn-secondary" onclick="visionReject('${row.question_id}')">Reject</button>
          <button class="btn btn-secondary" onclick="visionRerun('${row.question_id}')">Re-run</button>
        </div>
      </td>
    </tr>
  `).join('');
}

function visionToggleDetail(questionId) {
  document.getElementById(`visionDetail-${questionId}`).classList.toggle('open');
}

async function visionSelectRow(questionId) {
  document.querySelectorAll('.vision-row-active').forEach(el => el.classList.remove('vision-row-active'));
  const rowEl = document.getElementById(`visionRow-${questionId}`);
  if (rowEl) rowEl.classList.add('vision-row-active');

  const row = visionQuestions.find(q => q.question_id === questionId);
  if (!row) return;
  try {
    const pages = await apiFetch(`/pdf/${visionPdfId}/pages`);
    const page = pages.find(p => p.page_number === row.page_number);
    const frame = document.getElementById('visionPageFrame');
    if (!page || !page.image_url) {
      frame.innerHTML = '<span class="page-meta">No page image available.</span>';
      return;
    }
    let overlay = '';
    if (row.bounding_box && row.bounding_box.length === 4) {
      const [x0, y0, x1, y1] = row.bounding_box;
      overlay = `<div class="vision-bbox" style="left:${x0*100}%; top:${y0*100}%; width:${(x1-x0)*100}%; height:${(y1-y0)*100}%;"></div>`;
    }
    frame.innerHTML = `<img src="${page.image_url}" alt="Page ${row.page_number}" />${overlay}`;
  } catch (e) {
    visionStatus('visionExportStatus', 'Failed to load page preview: ' + e.message, 'err');
  }
}

async function visionApprove(questionId) {
  await visionQuestionAction(questionId, `/question/${questionId}/approve`, { method: 'POST' });
}
async function visionReject(questionId) {
  await visionQuestionAction(questionId, `/question/${questionId}/reject`, { method: 'POST' });
}
async function visionRerun(questionId) {
  const surveyYear = parseInt(document.getElementById('visionSurveyYear').value, 10) || 2025;
  await visionQuestionAction(questionId, `/question/${questionId}/rerun`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ survey_year: surveyYear }),
  });
}
async function visionSaveEdit(questionId) {
  const answer = document.getElementById(`visionAnswer-${questionId}`).value;
  await visionQuestionAction(questionId, `/question/${questionId}/edit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ answer }),
  });
}
async function visionQuestionAction(questionId, path, opts) {
  try {
    const updated = await apiFetch(path, opts);
    const index = visionQuestions.findIndex(q => q.question_id === questionId);
    if (index >= 0) visionQuestions[index] = updated;
    visionRenderRows();
  } catch (e) {
    visionStatus('visionExportStatus', 'Action failed: ' + e.message, 'err');
  }
}

async function visionExport() {
  if (!visionPdfId) return;
  visionStatus('visionExportStatus', 'Exporting…', 'info');
  try {
    const result = await apiFetch(`/pdf/${visionPdfId}/export-filled-pdf`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    const link = result.download_url ? ` <a href="${result.download_url}">Download</a>` : '';
    visionStatus('visionExportStatus', `Filled ${result.filled_count} field(s).${link}`, 'ok');
    document.getElementById('visionExportStatus').innerHTML += link;
  } catch (e) {
    visionStatus('visionExportStatus', 'Export failed: ' + e.message, 'err');
  }
}
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>PDF Vision Operations — Survey Automation</title>
  <meta name="description" content="Upload a CDS PDF, extract plain-language questions from page screenshots, resolve answers with Databricks Genie, and review before export." />
{shared_css}
{extra_style}
</head>
<body>
{nav_html}
{body}
<script>
{shared_js}
{script}
</script>
</body>
</html>"""
