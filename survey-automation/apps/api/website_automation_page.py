from __future__ import annotations


def website_automation_page_html(shared_css: str, shared_js: str, nav_html: str) -> str:
    extra_style = """
  <style>
    .wa-hidden { display: none !important; }
    .wa-dashboard { display: grid; grid-template-columns: repeat(6, 1fr); gap: 10px; margin-bottom: 16px; }
    @media (max-width: 900px) { .wa-dashboard { grid-template-columns: repeat(3, 1fr); } }
    .wa-review-layout { display: grid; grid-template-columns: 1.5fr 1fr; gap: 16px; align-items: start; }
    @media (max-width: 1000px) { .wa-review-layout { grid-template-columns: 1fr; } }
    .wa-page-preview { position: sticky; top: 12px; border: 1px solid rgba(127,127,127,0.25); border-radius: 8px; padding: 10px; }
    .wa-page-preview img { max-width: 100%; display: block; border-radius: 4px; }
    .wa-answer-input { width: 100%; box-sizing: border-box; }
    .wa-match-cell { font-size: 12px; color: var(--muted, #888); }
  </style>
"""

    body = """
<div class="page">
  <div class="page-header">
    <h1>Website Automation</h1>
    <p>Point this at a live survey website, log in manually in your own browser, then scan each page and let approved PDF answers get matched, reviewed, and filled in — one page at a time.</p>
  </div>

  <div id="waSetupPanel" class="card">
    <h2 style="margin-top:0">1. Enter Website URL</h2>
    <input id="waUrlInput" placeholder="https://survey.example.edu" style="width:60%" />
    <button class="btn" onclick="waStart()">Start Session</button>
    <div id="waStartStatus" class="status-line"></div>

    <div id="waConnectBlock" class="wa-hidden" style="margin-top:12px">
      <p class="page-meta">Before connecting: launch Chrome/Edge with remote debugging enabled and log in manually (see the <a href="/website-ops">Web Fill</a> page for the exact launch command), then click Connect.</p>
      <button class="btn" onclick="waConnect()">Launch/Connect Browser</button>
      <div id="waConnectStatus" class="status-line"></div>
    </div>
  </div>

  <div id="waDashboard" class="wa-dashboard wa-hidden">
    <div class="stat"><strong id="waStatPage">—</strong><span>Current page</span></div>
    <div class="stat"><strong id="waStatDetected">—</strong><span>Detected</span></div>
    <div class="stat"><strong id="waStatMatched">—</strong><span>Matched</span></div>
    <div class="stat"><strong id="waStatFilled">—</strong><span>Filled</span></div>
    <div class="stat"><strong id="waStatSkipped">—</strong><span>Skipped</span></div>
    <div class="stat"><strong id="waStatConfidence">—</strong><span>Avg confidence</span></div>
  </div>

  <div id="waWorkPanel" class="card wa-hidden">
    <div class="toolbar">
      <button class="btn" onclick="waScan()">Scan Current Page</button>
      <button class="btn btn-secondary" onclick="waMatch()">Match Questions</button>
      <button class="btn" onclick="waFill()">Fill Answers</button>
      <input id="waNextButtonText" placeholder="Next button text (optional)" style="width:220px" />
      <button class="btn btn-secondary" onclick="waNext()">Next Page</button>
    </div>
    <div id="waWorkStatus" class="status-line"></div>

    <div class="wa-review-layout" style="margin-top:12px">
      <div class="table-wrap">
        <table>
          <thead><tr>
            <th>Website Question</th><th>Stored Question</th><th style="width:130px">Answer</th>
            <th style="width:70px">Match</th><th style="width:260px">Status</th>
          </tr></thead>
          <tbody id="waRows"><tr class="empty-row"><td colspan="5">No questions scanned yet.</td></tr></tbody>
        </table>
      </div>
      <div class="wa-page-preview">
        <div id="waPageFrame"><span class="page-meta">Scan a page to see its screenshot.</span></div>
      </div>
    </div>
  </div>
</div>
"""

    script = """
let waSessionId = null;
let waQuestions = [];

function waStatus(id, text, type) {
  const el = document.getElementById(id);
  if (!text) { el.className = 'status-line'; el.textContent = ''; return; }
  el.className = `status-line show ${type || 'info'}`;
  el.textContent = text;
}

async function waStart() {
  const url = document.getElementById('waUrlInput').value.trim();
  if (!url) { waStatus('waStartStatus', 'Enter a website URL first.', 'err'); return; }
  waStatus('waStartStatus', 'Creating session…', 'info');
  try {
    const result = await apiFetch('/website/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });
    waSessionId = result.session_id;
    waStatus('waStartStatus', `Session ${result.session_id} created.`, 'ok');
    document.getElementById('waConnectBlock').classList.remove('wa-hidden');
  } catch (e) {
    waStatus('waStartStatus', 'Failed to start session: ' + e.message, 'err');
  }
}

async function waConnect() {
  if (!waSessionId) return;
  waStatus('waConnectStatus', 'Connecting to browser…', 'info');
  try {
    await apiFetch(`/website/${waSessionId}/connect`, { method: 'POST' });
    waStatus('waConnectStatus', 'Browser connected.', 'ok');
    document.getElementById('waDashboard').classList.remove('wa-hidden');
    document.getElementById('waWorkPanel').classList.remove('wa-hidden');
    await waRefreshStatus();
  } catch (e) {
    waStatus('waConnectStatus', 'Connect failed: ' + e.message, 'err');
  }
}

async function waRefreshStatus() {
  if (!waSessionId) return;
  try {
    const status = await apiFetch(`/website/${waSessionId}/status`);
    document.getElementById('waStatPage').textContent = status.current_page_number;
    document.getElementById('waStatDetected').textContent = status.questions_detected;
    document.getElementById('waStatMatched').textContent = status.questions_matched;
    document.getElementById('waStatFilled').textContent = status.questions_filled;
    document.getElementById('waStatSkipped').textContent = status.questions_skipped;
    document.getElementById('waStatConfidence').textContent = status.average_confidence + '%';
  } catch {}
}

async function waScan() {
  if (!waSessionId) return;
  waStatus('waWorkStatus', 'Scanning current page…', 'info');
  try {
    waQuestions = await apiFetch(`/website/${waSessionId}/scan`, { method: 'POST' });
    waRenderRows();
    await waLoadCurrentScreenshot();
    await waRefreshStatus();
    waStatus('waWorkStatus', `Detected ${waQuestions.length} question(s).`, 'ok');
  } catch (e) {
    waStatus('waWorkStatus', 'Scan failed: ' + e.message, 'err');
  }
}

async function waMatch() {
  if (!waSessionId) return;
  waStatus('waWorkStatus', 'Matching against approved PDF answers…', 'info');
  try {
    waQuestions = await apiFetch(`/website/${waSessionId}/match`, { method: 'POST' });
    waRenderRows();
    await waRefreshStatus();
    waStatus('waWorkStatus', 'Matching complete — review below before filling.', 'ok');
  } catch (e) {
    waStatus('waWorkStatus', 'Match failed: ' + e.message, 'err');
  }
}

async function waFill() {
  if (!waSessionId) return;
  waStatus('waWorkStatus', 'Filling approved answers…', 'info');
  try {
    waQuestions = await apiFetch(`/website/${waSessionId}/fill`, { method: 'POST' });
    waRenderRows();
    await waRefreshStatus();
    waStatus('waWorkStatus', 'Fill complete.', 'ok');
  } catch (e) {
    waStatus('waWorkStatus', 'Fill failed: ' + e.message, 'err');
  }
}

async function waNext() {
  if (!waSessionId) return;
  const buttonText = document.getElementById('waNextButtonText').value.trim() || null;
  waStatus('waWorkStatus', 'Clicking Next…', 'info');
  try {
    const result = await apiFetch(`/website/${waSessionId}/next`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ button_text: buttonText }),
    });
    if (result.clicked) {
      waStatus('waWorkStatus', `Advanced to page ${result.current_page_number}. Scan again.`, 'ok');
    } else {
      waStatus('waWorkStatus', 'Could not find a Next button — click it manually in the browser, then Scan again.', 'err');
    }
    await waRefreshStatus();
  } catch (e) {
    waStatus('waWorkStatus', 'Next failed: ' + e.message, 'err');
  }
}

async function waLoadCurrentScreenshot() {
  try {
    const pages = await apiFetch(`/website/${waSessionId}/pages`);
    const last = pages[pages.length - 1];
    const frame = document.getElementById('waPageFrame');
    if (last && last.screenshot_url) {
      frame.innerHTML = `<img src="${last.screenshot_url}" alt="page ${last.page_number}" />`;
    } else {
      frame.innerHTML = '<span class="page-meta">No screenshot available.</span>';
    }
  } catch {}
}

function waStatusPill(status) {
  const cls = status === 'FILLED' ? 'pill-good' : status === 'FAILED' ? 'pill-bad'
    : (status === 'APPROVED' || status === 'EDITED' || status === 'MATCHED') ? 'pill-warn' : 'pill-neutral';
  return `<span class="pill ${cls}">${escHtml(status)}</span>`;
}

function waRenderRows() {
  const tbody = document.getElementById('waRows');
  if (!waQuestions.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="5">No questions scanned yet.</td></tr>';
    return;
  }
  tbody.innerHTML = waQuestions.map(row => `
    <tr id="waRow-${row.question_id}">
      <td>${escHtml(row.detected_question)}<div class="wa-match-cell">${escHtml(row.question_type)}</div></td>
      <td>${escHtml(row.matched_question_text || '(no match)')}</td>
      <td><input class="wa-answer-input" id="waAnswer-${row.question_id}" value="${escHtml(row.answer_used)}" /></td>
      <td>${row.confidence}%</td>
      <td>
        ${waStatusPill(row.status)}
        <div style="margin-top:6px">
          <button class="btn btn-secondary" onclick="waOverride('${row.question_id}', 'APPROVED')">Approve</button>
          <button class="btn btn-secondary" onclick="waSaveEdit('${row.question_id}')">Edit</button>
          <button class="btn btn-secondary" onclick="waOverride('${row.question_id}', 'SKIPPED')">Skip</button>
        </div>
      </td>
    </tr>
  `).join('');
}

async function waOverride(questionId, status) {
  try {
    const updated = await apiFetch(`/website/question/${questionId}/override`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    });
    const index = waQuestions.findIndex(q => q.question_id === questionId);
    if (index >= 0) waQuestions[index] = updated;
    waRenderRows();
  } catch (e) {
    waStatus('waWorkStatus', 'Override failed: ' + e.message, 'err');
  }
}

async function waSaveEdit(questionId) {
  const answer = document.getElementById(`waAnswer-${questionId}`).value;
  try {
    const updated = await apiFetch(`/website/question/${questionId}/override`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: 'EDITED', answer_used: answer }),
    });
    const index = waQuestions.findIndex(q => q.question_id === questionId);
    if (index >= 0) waQuestions[index] = updated;
    waRenderRows();
  } catch (e) {
    waStatus('waWorkStatus', 'Edit failed: ' + e.message, 'err');
  }
}
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Website Automation — Survey Automation</title>
  <meta name="description" content="Scan a live survey website, match against approved PDF answers, review, and fill." />
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
