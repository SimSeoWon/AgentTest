"""웹 UI HTML 상수. http_server.py에서 import하여 사용."""

WEB_UI_HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AgentWatch Context Server</title>
<style>
  :root { --bg: #0d1117; --surface: #161b22; --border: #30363d; --text: #e6edf3; --dim: #8b949e; --accent: #58a6ff; --green: #3fb950; --orange: #d29922; --red: #f85149; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; background: var(--bg); color: var(--text); line-height: 1.5; }
  .container { max-width: 1200px; margin: 0 auto; padding: 24px; }
  h1 { font-size: 1.5rem; margin-bottom: 8px; }
  h1 span { color: var(--accent); }
  .subtitle { color: var(--dim); margin-bottom: 24px; font-size: 0.9rem; }

  /* 상태 카드 */
  .status-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 24px; }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 16px; }
  .card .label { color: var(--dim); font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; }
  .card .value { font-size: 1.5rem; font-weight: 600; margin-top: 4px; }
  .card .value.ok { color: var(--green); }
  .card .value.warn { color: var(--orange); }

  /* 검색 */
  .search-box { display: flex; gap: 8px; margin-bottom: 16px; }
  .search-box input { flex: 1; background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 10px 14px; color: var(--text); font-size: 0.95rem; outline: none; }
  .search-box input:focus { border-color: var(--accent); }
  .search-box button { background: var(--accent); color: #fff; border: none; border-radius: 6px; padding: 10px 20px; cursor: pointer; font-weight: 600; font-size: 0.95rem; white-space: nowrap; }
  .search-box button:hover { opacity: 0.9; }
  .search-opts { display: flex; gap: 16px; margin-bottom: 20px; align-items: center; flex-wrap: wrap; }
  .search-opts label { color: var(--dim); font-size: 0.85rem; }
  .search-opts input, .search-opts select { background: var(--surface); border: 1px solid var(--border); border-radius: 4px; padding: 4px 8px; color: var(--text); font-size: 0.85rem; }

  /* 탭 */
  .tabs { display: flex; gap: 0; margin-bottom: 20px; border-bottom: 1px solid var(--border); }
  .tab { padding: 8px 20px; cursor: pointer; color: var(--dim); border-bottom: 2px solid transparent; font-size: 0.9rem; }
  .tab:hover { color: var(--text); }
  .tab.active { color: var(--accent); border-bottom-color: var(--accent); }
  .tab-content { display: none; }
  .tab-content.active { display: block; }

  /* 결과 */
  .result { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 16px; margin-bottom: 12px; }
  .result-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
  .result-file { color: var(--accent); font-weight: 600; font-size: 0.95rem; }
  .result-score { color: var(--dim); font-size: 0.8rem; }
  .result-category { color: var(--orange); font-size: 0.8rem; margin-bottom: 6px; }
  .result-tags { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }
  .tag { background: #1f6feb22; color: var(--accent); border: 1px solid #1f6feb44; border-radius: 12px; padding: 2px 10px; font-size: 0.75rem; cursor: pointer; }
  .tag:hover { background: #1f6feb44; }
  .result-body { color: var(--dim); font-size: 0.85rem; white-space: pre-wrap; max-height: 200px; overflow-y: auto; }

  /* 문서 목록 */
  .doc-list { display: grid; gap: 8px; }
  .doc-item { background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 12px 16px; display: flex; justify-content: space-between; align-items: center; cursor: pointer; }
  .doc-item:hover { border-color: var(--accent); }
  .doc-name { color: var(--accent); font-size: 0.9rem; }
  .doc-tags { display: flex; gap: 4px; flex-wrap: wrap; }

  /* 태그 클라우드 */
  .tag-cloud { display: flex; flex-wrap: wrap; gap: 8px; }
  .tag-cloud .tag { font-size: 0.85rem; padding: 4px 12px; }
  .tag-count { color: var(--dim); font-size: 0.75rem; margin-left: 4px; }

  .empty { color: var(--dim); text-align: center; padding: 40px; }
  .loading { color: var(--dim); text-align: center; padding: 20px; }
</style>
</head>
<body>
<div class="container">
  <h1><span>AgentWatch</span> Context Server</h1>
  <p class="subtitle" id="projectRoot">Loading...</p>

  <div class="status-row" id="statusCards">
    <div class="card"><div class="label">Status</div><div class="value" id="sStatus">-</div></div>
    <div class="card"><div class="label">Documents</div><div class="value" id="sDocs">-</div></div>
    <div class="card"><div class="label">Tags</div><div class="value" id="sTags">-</div></div>
    <div class="card"><div class="label">Updating</div><div class="value" id="sUpdating">-</div></div>
  </div>

  <div class="tabs">
    <div class="tab active" data-tab="search">Search</div>
    <div class="tab" data-tab="docs">Documents</div>
    <div class="tab" data-tab="tags">Tags</div>
  </div>

  <!-- Search Tab -->
  <div class="tab-content active" id="tab-search">
    <div class="search-box">
      <input type="text" id="searchQuery" placeholder="Search query..." />
      <button onclick="doSearch()">Search</button>
    </div>
    <div class="search-opts">
      <label>Results: <input type="number" id="searchN" value="5" min="1" max="50" style="width:60px"></label>
      <label>Category: <input type="text" id="searchCat" placeholder="filter..." style="width:120px"></label>
      <label>Tags: <input type="text" id="searchTags" placeholder="comma separated" style="width:160px"></label>
    </div>
    <div id="searchResults"><div class="empty">Enter a query to search the context database.</div></div>
  </div>

  <!-- Documents Tab -->
  <div class="tab-content" id="tab-docs">
    <div class="search-box">
      <input type="text" id="docFilter" placeholder="Filter documents..." oninput="filterDocs()" />
    </div>
    <div id="docList"><div class="loading">Loading documents...</div></div>
  </div>

  <!-- Tags Tab -->
  <div class="tab-content" id="tab-tags">
    <div id="tagCloud"><div class="loading">Loading tags...</div></div>
  </div>
</div>

<script>
const API = '';

// ── Tabs ──
document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById('tab-' + tab.dataset.tab).classList.add('active');
  });
});

// ── Status ──
async function loadStatus() {
  try {
    const [health, status, tags] = await Promise.all([
      fetch(API + '/api/v1/health').then(r => r.json()),
      fetch(API + '/api/v1/index/status').then(r => r.json()),
      fetch(API + '/api/v1/tags').then(r => r.json()),
    ]);
    document.getElementById('projectRoot').textContent = health.project_root || '';
    document.getElementById('sStatus').textContent = health.status || 'unknown';
    document.getElementById('sStatus').className = 'value' + (health.status === 'ok' ? ' ok' : ' warn');
    document.getElementById('sDocs').textContent = status.indexed_documents || 0;
    document.getElementById('sTags').textContent = tags.total_tags || 0;
    document.getElementById('sUpdating').textContent = health.updating ? 'Yes' : 'No';
    document.getElementById('sUpdating').className = 'value' + (health.updating ? ' warn' : ' ok');

    // tags cloud
    renderTagCloud(tags.tags || {});
  } catch(e) {
    document.getElementById('sStatus').textContent = 'Error';
    document.getElementById('sStatus').className = 'value warn';
  }
}

// ── Search ──
document.getElementById('searchQuery').addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(); });

async function doSearch() {
  const query = document.getElementById('searchQuery').value.trim();
  if (!query) return;
  const n = parseInt(document.getElementById('searchN').value) || 10;
  const cat = document.getElementById('searchCat').value.trim();
  const tagsRaw = document.getElementById('searchTags').value.trim();
  const tags = tagsRaw ? tagsRaw.split(',').map(t => t.trim()).filter(Boolean) : null;

  document.getElementById('searchResults').innerHTML = '<div class="loading">Searching...</div>';

  try {
    const body = { query, n_results: n };
    if (cat) body.category_filter = cat;
    if (tags) body.tags = tags;

    const resp = await fetch(API + '/api/v1/search/combined', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
    });
    const data = await resp.json();
    renderResults(data);
  } catch(e) {
    document.getElementById('searchResults').innerHTML = '<div class="empty">Search failed: ' + e.message + '</div>';
  }
}

function renderResults(data) {
  const el = document.getElementById('searchResults');
  const results = data.results || [];
  if (!results.length) { el.innerHTML = '<div class="empty">No results found.</div>'; return; }

  el.innerHTML = results.map((r, i) => {
    const tags = (r.tags || []).map(t => '<span class="tag" onclick="searchByTag(\'' + t + '\')">' + t + '</span>').join('');
    const score = r.similarity != null && r.similarity > 0 ? (r.similarity * 100).toFixed(1) + '%' : (r.source || '');
    const preview = (r.content_preview || r.body || '').substring(0, 400);
    return '<div class="result">' +
      '<div class="result-header"><span class="result-file">' + (r.file || r.id || '?') + '</span><span class="result-score">' + score + '</span></div>' +
      (r.category ? '<div class="result-category">' + r.category + '</div>' : '') +
      (tags ? '<div class="result-tags">' + tags + '</div>' : '') +
      '<div class="result-body">' + escHtml(preview) + '</div>' +
    '</div>';
  }).join('');
}

function searchByTag(tag) {
  document.getElementById('searchTags').value = tag;
  const q = document.getElementById('searchQuery').value.trim();
  if (!q) document.getElementById('searchQuery').value = tag;
  doSearch();
}

// ── Documents ──
let allDocs = [];

async function loadDocs() {
  try {
    const tags = await fetch(API + '/api/v1/tags').then(r => r.json());
    const status = await fetch(API + '/api/v1/index/status').then(r => r.json());

    const resp = await fetch(API + '/api/v1/search/vector', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ query: ' ', n_results: status.indexed_documents || 100 }),
    });
    const data = await resp.json();
    allDocs = (data.results || []).map(r => ({
      file: r.file || r.id || '?',
      tags: r.tags || [],
      category: r.category || '',
      preview: (r.content_preview || r.body || '').substring(0, 200),
    }));
    allDocs.sort((a, b) => a.file.localeCompare(b.file));
    renderDocs(allDocs);
  } catch(e) {
    document.getElementById('docList').innerHTML = '<div class="empty">Failed to load documents.</div>';
  }
}

function renderDocs(docs) {
  const el = document.getElementById('docList');
  if (!docs.length) { el.innerHTML = '<div class="empty">No documents found.</div>'; return; }
  el.innerHTML = docs.map(d => {
    const tags = d.tags.slice(0, 5).map(t => '<span class="tag" onclick="searchByTag(\'' + t + '\')">' + t + '</span>').join('');
    return '<div class="doc-item" onclick="searchDoc(\'' + escAttr(d.file) + '\')">' +
      '<span class="doc-name">' + escHtml(d.file) + '</span>' +
      '<span class="doc-tags">' + tags + '</span>' +
    '</div>';
  }).join('');
}

function filterDocs() {
  const q = document.getElementById('docFilter').value.toLowerCase();
  renderDocs(q ? allDocs.filter(d => d.file.toLowerCase().includes(q) || d.tags.some(t => t.toLowerCase().includes(q))) : allDocs);
}

function searchDoc(file) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  document.querySelector('[data-tab="search"]').classList.add('active');
  document.getElementById('tab-search').classList.add('active');
  document.getElementById('searchQuery').value = file.replace('.md', '');
  doSearch();
}

// ── Tags ──
function renderTagCloud(tags) {
  const el = document.getElementById('tagCloud');
  const entries = Object.entries(tags).sort((a, b) => b[1] - a[1]);
  if (!entries.length) { el.innerHTML = '<div class="empty">No tags found.</div>'; return; }
  el.innerHTML = '<div class="tag-cloud">' + entries.map(([tag, count]) =>
    '<span class="tag" onclick="searchByTag(\'' + tag + '\')">' + escHtml(tag) + '<span class="tag-count">' + count + '</span></span>'
  ).join('') + '</div>';
}

// ── Utils ──
function escHtml(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
function escAttr(s) { return s.replace(/'/g, "\\'").replace(/"/g, '&quot;'); }

// ── Init ──
loadStatus();
loadDocs();
</script>
</body>
</html>
"""
