from __future__ import annotations


def fill_preview_js() -> str:
    return """
function formatFillValue(value, ready) {
  if (!value) return '<span class="value-empty">—</span>';
  return `<strong class="${ready ? 'value-ready' : ''}">${escHtml(value)}</strong>`;
}

function flowTags(row) {
  const tags = [];
  if (row.pdf_ready || row.pdf_value) tags.push('<span class="flow-tag flow-tag-pdf">PDF</span>');
  if (row.web_ready || row.web_value) tags.push('<span class="flow-tag flow-tag-web">Web</span>');
  if (row.databricks_view) tags.push('<span class="flow-tag flow-tag-db">DB</span>');
  return tags.length ? tags.join(' ') : '<span class="td-muted">—</span>';
}

function statusPill(status) {
  if (!status) return '<span class="pill pill-neutral">Not scanned</span>';
  const cls = status === 'GENIE_RESOLVED' ? 'pill-good'
    : status === 'GENIE_LOW_CONFIDENCE' ? 'pill-warn' : 'pill-bad';
  const label = status.replace('GENIE_', '').replace('_', ' ');
  return `<span class="pill ${cls}">${escHtml(label)}</span>`;
}

async function fetchFillPreview(scanId) {
  const query = scanId ? `?scan_id=${encodeURIComponent(scanId)}` : '';
  return apiFetch(`/data-points/fill-preview${query}`);
}

function filterFillRows(rows, filter, search) {
  const q = (search || '').trim().toLowerCase();
  return rows.filter(row => {
    const hay = `${row.field_key} ${row.label||''} ${row.intent||''} ${row.section||''} ${row.pdf_value||''} ${row.web_value||''}`.toLowerCase();
    if (q && !hay.includes(q)) return false;
    if (filter === 'pdf_ready') return row.pdf_ready;
    if (filter === 'web_ready') return row.web_ready;
    if (filter === 'both_ready') return row.pdf_ready && row.web_ready;
    if (filter === 'missing') return !row.pdf_ready || !row.web_ready;
    if (filter === 'pdf') return row.pdf_value || row.pdf_status;
    if (filter === 'web') return row.web_value;
    return true;
  });
}
"""


def data_points_page_html(shared_css: str, shared_js: str, nav_html: str) -> str:
    body = """
<div class="page">
  <div class="page-header">
    <h1>Master Data Points</h1>
    <p>One catalog for both workflows. Each row shows the value that will be written in the <strong>PDF flow</strong> (AcroForm field) and the <strong>Web flow</strong> (live form field).</p>
  </div>
  <div class="callout">
    <strong>How to read this page</strong>
    <ul class="callout-list">
      <li><span class="flow-tag flow-tag-pdf">PDF</span> value exported into the fillable PDF.</li>
      <li><span class="flow-tag flow-tag-web">Web</span> value Skyvern types into the live website form.</li>
      <li><span class="flow-tag flow-tag-db">DB</span> master data point has a Databricks binding.</li>
    </ul>
    <div class="callout-links">
      <a href="/pdf-ops" class="btn btn-secondary">PDF flow</a>
      <a href="/website-ops" class="btn btn-secondary">Web flow</a>
    </div>
  </div>
  <div class="stat-row" style="margin-bottom:16px">
    <div class="stat"><strong id="statTotal">—</strong><span>Catalog fields</span></div>
    <div class="stat"><strong id="statPdfReady">—</strong><span>PDF ready</span></div>
    <div class="stat"><strong id="statWebReady">—</strong><span>Web ready</span></div>
    <div class="stat"><strong id="statBothReady">—</strong><span>Both ready</span></div>
  </div>
  <div class="card toolbar-card">
    <div class="toolbar">
      <input id="dpSearch" placeholder="Search field, label, section, value…" oninput="renderTable()" />
      <select id="dpScanSelect" onchange="reloadPreview()"><option value="">Latest PDF scan</option></select>
      <button class="btn btn-secondary" onclick="reloadPreview()">Reload</button>
    </div>
    <div class="chip-row">
      <button class="chip active" data-filter="all" onclick="setFilter('all',this)">All</button>
      <button class="chip" data-filter="both_ready" onclick="setFilter('both_ready',this)">Both ready</button>
      <button class="chip" data-filter="pdf_ready" onclick="setFilter('pdf_ready',this)">PDF ready</button>
      <button class="chip" data-filter="web_ready" onclick="setFilter('web_ready',this)">Web ready</button>
      <button class="chip" data-filter="missing" onclick="setFilter('missing',this)">Missing value</button>
    </div>
  </div>
  <div class="table-wrap catalog-table">
    <table>
      <thead><tr>
        <th style="width:160px">Field</th><th>Meaning</th>
        <th style="width:130px">PDF fill value</th><th style="width:130px">Web fill value</th>
        <th style="width:100px">Status</th><th style="width:90px">Flows</th>
      </tr></thead>
      <tbody id="dpRows"><tr class="empty-row"><td colspan="6">Loading catalog…</td></tr></tbody>
    </table>
  </div>
  <div id="dpStatus" class="status-line" style="margin-top:10px"></div>
  <div id="dpMeta" class="page-meta"></div>
</div>
"""
    script = """
let preview = null, currentFilter = 'all';
const $ = id => document.getElementById(id);
function statusEl(id, text, type='ok') {
  const el = $(id);
  if (!text) { el.className='status-line'; el.textContent=''; return; }
  el.className = `status-line show ${type}`; el.textContent = text;
}
function setFilter(f, btn) {
  currentFilter = f;
  document.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
  btn.classList.add('active'); renderTable();
}
function updateStats() {
  if (!preview) return;
  $('statTotal').textContent = String(preview.total_fields);
  $('statPdfReady').textContent = String(preview.pdf_ready_count);
  $('statWebReady').textContent = String(preview.web_ready_count);
  $('statBothReady').textContent = String(preview.both_ready_count);
  $('dpMeta').textContent = `Scan ${preview.scan_id||'none'} · Web payload ${preview.web_payload_path}`;
}
function renderTable() {
  if (!preview) return;
  const rows = filterFillRows(preview.rows, currentFilter, $('dpSearch').value);
  if (!rows.length) { $('dpRows').innerHTML = '<tr class="empty-row"><td colspan="6">No fields match this filter.</td></tr>'; return; }
  $('dpRows').innerHTML = rows.map(row => {
    const meaning = row.label || row.intent || '—';
    const sub = row.section ? `<div class="td-muted">${escHtml(row.section)}</div>` : '';
    return `<tr>
      <td class="td-mono">${escHtml(row.field_key)}</td>
      <td><div>${escHtml(meaning)}</div>${sub}</td>
      <td>${formatFillValue(row.pdf_value, row.pdf_ready)}${row.pdf_confidence ? `<div class="td-muted">${row.pdf_confidence}%</div>` : ''}</td>
      <td>${formatFillValue(row.web_value, row.web_ready)}</td>
      <td>${statusPill(row.pdf_status)}</td>
      <td>${flowTags(row)}</td>
    </tr>`;
  }).join('');
}
async function loadScans(selectedScanId) {
  try {
    const scans = await apiFetch('/pdf-scans');
    $('dpScanSelect').innerHTML = '<option value="">Latest PDF scan</option>' + scans.map(s =>
      `<option value="${escHtml(s.scan_id)}">${escHtml(s.survey_id)} · ${s.candidate_count} fields</option>`).join('');
    if (selectedScanId) $('dpScanSelect').value = selectedScanId;
  } catch {}
}
async function reloadPreview() {
  statusEl('dpStatus','Loading catalog…','info');
  try {
    const scanId = $('dpScanSelect').value || null;
    preview = await fetchFillPreview(scanId);
    await loadScans(preview.scan_id || '');
    updateStats(); renderTable();
    statusEl('dpStatus', `${preview.total_fields} field(s) · ${preview.both_ready_count} ready for both flows.`, 'ok');
  } catch(e) { statusEl('dpStatus', 'Error: ' + e.message, 'err'); }
}
reloadPreview();
"""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Data Points Catalog — Survey Automation</title>
{shared_css}
</head>
<body>
{nav_html}
{body}
<script>
{shared_js}
{fill_preview_js()}
{script}
</script>
</body>
</html>"""
