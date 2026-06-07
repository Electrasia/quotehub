// ─── Debug Workspace (Phase 2 of v0.037.0) ───────────────────
// Master-only side-by-side comparison of pdfplumber vs PyMuPDF
// output, with a format-for-LLM preview. Read-only — does not
// call the LLM, does not save anything. All HTML elements are
// declared in index.html (#debugView).

let _debugFiles = [];
let _debugResult = null;   // last /debug/parse response
let _debugPageIdx = 0;     // 0-based page index

// ─── File list ───────────────────────────────────────────────

async function loadDebugFiles() {
    if (!isMaster || !isMaster()) {
        showBriefPopup('Only Master can access the Debug Workspace');
        return;
    }
    const list = document.getElementById('debugFileList');
    list.innerHTML = '<p style="color:#999;margin:8px 0">Loading…</p>';
    try {
        const resp = await apiFetch('/debug/files');
        const data = await resp.json().then(d => ({ ok: resp.ok, data: d }));
        if (!data.ok) throw new Error(data.data.detail || 'Failed to load files');
        _debugFiles = data.data.files || [];
        renderDebugFileList();
    } catch (e) {
        list.innerHTML = `<p style="color:#e74c3c;margin:8px 0">Error: ${escapeHtml(e.message)}</p>`;
    }
}

function renderDebugFileList() {
    const list = document.getElementById('debugFileList');
    if (!_debugFiles.length) {
        list.innerHTML = '<p style="color:#999;margin:8px 0">No uploaded files. Upload a PDF first.</p>';
        return;
    }
    const fmt = (b) => {
        if (!Number.isFinite(b)) return '—';
        const k = 1024, u = ['B','KB','MB','GB'];
        const i = Math.min(Math.floor(Math.log(b)/Math.log(k)), u.length-1);
        return Math.round((b/Math.pow(k,i))*100)/100 + ' ' + u[i];
    };
    const activeIdx = _debugResult ? _debugResult.file_index : -1;
    const html = _debugFiles.map(f => {
        const isActive = f.file_index === activeIdx;
        return `
            <div onclick="selectDebugFile(${f.file_index})"
                 style="cursor:pointer;padding:8px;border-radius:6px;margin-bottom:4px;
                        border:1px solid ${isActive ? '#3498db' : '#eee'};
                        background:${isActive ? '#ebf3fb' : '#fff'};
                        transition:background 0.1s"
                 onmouseover="if(this.style.background!=='rgb(235, 243, 251)')this.style.background='#f5f5f5'"
                 onmouseout="if(this.style.background!=='rgb(235, 243, 251)')this.style.background='#fff'">
                <div style="font-weight:500;font-size:13px;word-break:break-all">
                    #${f.file_index} · ${escapeHtml(f.filename)}
                </div>
                <div style="font-size:11px;color:#888;margin-top:2px">
                    ${f.num_pages} page${f.num_pages === 1 ? '' : 's'} ·
                    ${fmt(f.file_size)} ·
                    <span style="color:${f.status === 'pending' ? '#f39c12' : '#27ae60'}">${escapeHtml(f.status)}</span>
                </div>
            </div>
        `;
    }).join('');
    list.innerHTML = html;
}

// ─── File selection + parse ──────────────────────────────────

async function selectDebugFile(idx) {
    if (!isMaster || !isMaster()) return;
    const meta = document.getElementById('debugFileMeta');
    meta.innerHTML = '<p style="color:#666;margin:0">Parsing…</p>';
    document.getElementById('debugPageNav').classList.add('hidden');
    _debugPageIdx = 0;
    // Reset per-document extraction state
    _debugExtractResult = null;
    document.getElementById('debugModelSource').value = 'auto';
    document.getElementById('debugLlmFallback').checked = false;
    document.getElementById('debugExtractStatus').innerHTML = '';
    document.getElementById('debugExtractMetadata').innerHTML = '';
    document.getElementById('debugExtractWarnings').innerHTML = '';
    document.getElementById('debugExtractItems').innerHTML = '';
    try {
        const resp = await apiFetch(`/debug/parse?file_index=${idx}`);
        const data = await resp.json().then(d => ({ ok: resp.ok, data: d }));
        if (!data.ok) throw new Error(data.data.detail || 'Parse failed');
        _debugResult = data.data;
        _debugPageIdx = 0;
        renderDebugFileList();   // highlight the active item
        renderDebugComparison();
    } catch (e) {
        meta.innerHTML = `<p style="color:#e74c3c;margin:0">Error: ${escapeHtml(e.message)}</p>`;
    }
}

// ─── Page navigation ─────────────────────────────────────────

function debugPrevPage() {
    if (!_debugResult) return;
    if (_debugPageIdx > 0) {
        _debugPageIdx--;
        renderDebugComparison();
    }
}

function debugNextPage() {
    if (!_debugResult) return;
    const total = _debugResult.num_pages || 0;
    if (_debugPageIdx < total - 1) {
        _debugPageIdx++;
        renderDebugComparison();
    }
}

// ─── Comparison render ───────────────────────────────────────

function renderDebugComparison() {
    const r = _debugResult;
    if (!r) return;

    // Metadata bar
    const pp = r.parsers?.pdfplumber || {};
    const pm = r.parsers?.pymupdf || {};
    const fmt = (b) => {
        if (!Number.isFinite(b)) return '—';
        const k = 1024, u = ['B','KB','MB','GB'];
        const i = Math.min(Math.floor(Math.log(b)/Math.log(k)), u.length-1);
        return Math.round((b/Math.pow(k,i))*100)/100 + ' ' + u[i];
    };
    let strategiesLine = '';
    if (pp.table_strategies_used && pp.table_strategies_used.length) {
        const s = pp.table_strategies_used
            .map(x => `p${x.page}:${x.strategy}(${x.rich_rows} rich rows)`)
            .join('  ');
        strategiesLine = `<div style="font-size:11px;color:#888;margin-top:4px">
            table strategies picked: ${escapeHtml(s)}
        </div>`;
    }
    document.getElementById('debugFileMeta').innerHTML = `
        <div><strong>${escapeHtml(r.upload_filename || r.filename || '—')}</strong>
            <span style="color:#666"> — ${r.num_pages || 0} pages, ${fmt(r.file_size)}</span></div>
        <div style="display:flex;gap:16px;margin-top:8px;flex-wrap:wrap;font-size:12px">
            <span><strong>pdfplumber:</strong>
                ${pp.available === false
                    ? `<span style="color:#e74c3c">unavailable: ${escapeHtml(pp.error || '')}</span>`
                    : `<span style="color:#27ae60">${pp.time_ms}ms</span>, ${pp.total_text_chars} chars,
                        ${pp.total_tables} tables, ${pp.total_table_rows} rows`}
            </span>
            <span><strong>pymupdf:</strong>
                ${pm.available === false
                    ? `<span style="color:#e74c3c">unavailable: ${escapeHtml(pm.error || '')}</span>`
                    : `<span style="color:#27ae60">${pm.time_ms}ms</span>, ${pm.total_text_chars} chars`}
            </span>
        </div>
        ${strategiesLine}
    `;

    // Page nav
    const total = r.num_pages || 0;
    document.getElementById('debugPageNav').classList.remove('hidden');
    document.getElementById('debugPageLabel').textContent =
        `Page ${_debugPageIdx + 1} of ${total}`;

    // Find the current page in each parser
    const pageNo = _debugPageIdx + 1;
    const ppPage = (pp.pages || []).find(p => p.page === pageNo);
    const pmPage = (pm.pages || []).find(p => p.page === pageNo);

    // Column 1: pdfplumber text
    document.getElementById('debugPpChars').textContent =
        ppPage ? `${ppPage.text_chars || 0} chars` : '—';
    document.getElementById('debugPpText').textContent =
        ppPage ? ppPage.text : (pp.available === false
            ? `(pdfplumber unavailable: ${pp.error || ''})`
            : '(no text)');

    // Column 2: pymupdf text
    document.getElementById('debugPmChars').textContent =
        pmPage ? `${pmPage.text_chars || 0} chars` : '—';
    document.getElementById('debugPmText').textContent =
        pmPage ? pmPage.text : (pm.available === false
            ? `(pymupdf unavailable: ${pm.error || ''})`
            : '(no text)');

    // Column 3: format for LLM — split the precomputed string on
    // "=== Page N ===" boundaries and show the current page's slice
    const llmText = r.format_for_llm || '';
    let pageSlice = '';
    if (llmText) {
        const re = new RegExp(`=== Page ${pageNo} ===([\\s\\S]*?)(?==== Page \\d+ ===|$)`);
        const m = llmText.match(re);
        pageSlice = m ? m[1].trim() : '(no LLM preview for this page)';
    } else {
        pageSlice = '(format_for_llm not available)';
    }
    document.getElementById('debugLlmText').textContent = pageSlice;
}

// ─── Re-open the existing debug modal (Phase 1) ──────────────
// The Phase 1 modal shows ALL pages + a JSON tab. Useful for a
// deeper look after you've found something interesting in the
// workspace. Shares _lastDebugResult with settings.js.
function openDebugParseModal() {
    if (!_debugResult) {
        showBriefPopup('Select a file first');
        return;
    }
    // settings.js owns the modal; reuse its renderer.
    if (typeof _lastDebugResult !== 'undefined' && typeof renderDebugParseModal === 'function') {
        _lastDebugResult = _debugResult;
        _currentDebugTab = 'plumber';
        renderDebugParseModal();
        openModal('debugParseModal');
    } else {
        showBriefPopup('Debug modal is not ready');
    }
}

// ─── Init: deep-link via ?debug=1 ────────────────────────────
// If the user lands on the app with ?debug=1 in the URL, jump
// straight to the Debug Workspace (after auth completes).
(function initDebugDeepLink() {
    try {
        const params = new URLSearchParams(window.location.search);
        if (params.get('debug') === '1') {
            window.__openDebugOnBoot = true;
        }
    } catch (e) { /* noop */ }
})();

// ─── Extraction (Phase 3 of v0.037.0) ─────────────────────────
// Per-document model source choice (auto/model/part_no) and optional
// LLM fallback when local returns 0 items.

let _debugExtractResult = null;

async function runDebugExtract() {
    if (!isMaster || !isMaster()) {
        showBriefPopup('Only Master can run extraction');
        return;
    }
    if (!_debugResult) {
        showBriefPopup('Select a file first');
        return;
    }
    const btn = document.getElementById('debugRunExtractBtn');
    const status = document.getElementById('debugExtractStatus');
    const meta = document.getElementById('debugExtractMetadata');
    const warns = document.getElementById('debugExtractWarnings');
    const itemsEl = document.getElementById('debugExtractItems');
    const modelSource = document.getElementById('debugModelSource').value;
    const useLlm = document.getElementById('debugLlmFallback').checked;

    btn.disabled = true;
    status.textContent = 'Running extraction…';
    meta.innerHTML = '';
    warns.innerHTML = '';
    itemsEl.innerHTML = '';
    try {
        const resp = await apiFetch('/debug/extract', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                file_index: _debugResult.file_index,
                model_source: modelSource,
                use_llm_fallback: useLlm,
            }),
        });
        const data = await resp.json().then(d => ({ ok: resp.ok, data: d }));
        if (!data.ok) throw new Error(data.data.detail || 'Extraction failed');
        _debugExtractResult = data.data;
        renderDebugExtract();
    } catch (e) {
        status.innerHTML = `<span style="color:#e74c3c">Error: ${escapeHtml(e.message)}</span>`;
    } finally {
        btn.disabled = false;
    }
}

function renderDebugExtract() {
    const r = _debugExtractResult;
    if (!r) return;
    const status = document.getElementById('debugExtractStatus');
    const meta = document.getElementById('debugExtractMetadata');
    const warns = document.getElementById('debugExtractWarnings');
    const itemsEl = document.getElementById('debugExtractItems');

    const method = r.extraction_method || 'local';
    const methodLabel = method === 'llm_fallback'
        ? '<span style="color:#9b59b6;font-weight:600">🤖 LLM fallback</span>'
        : '<span style="color:#27ae60;font-weight:600">⚙️ Local rules</span>';
    status.innerHTML = `${methodLabel} · <strong>${r.items.length}</strong> item${r.items.length === 1 ? '' : 's'}`;

    const fields = [
        ['Supplier', r.supplier],
        ['Date', r.date],
        ['Currency', r.currency],
        ['Document type', r.document_type],
    ];
    meta.innerHTML = fields.map(([k, v]) => `
        <div class="settings-card" style="padding:8px 10px;background:#f9f9f9">
            <div style="font-size:11px;color:#888;font-weight:600">${escapeHtml(k)}</div>
            <div style="font-size:13px;color:${v ? '#222' : '#999'};word-break:break-word">${escapeHtml(v || '(not detected)')}</div>
        </div>
    `).join('');

    if (r.extraction_warnings && r.extraction_warnings.length) {
        warns.innerHTML = r.extraction_warnings.map(w =>
            `<div>⚠️ ${escapeHtml(w)}</div>`
        ).join('');
    } else {
        warns.innerHTML = '';
    }

    if (!r.items.length) {
        itemsEl.innerHTML = '<p style="color:#999;margin:8px 0">No items extracted.</p>';
        return;
    }
    itemsEl.innerHTML = renderDebugItemsTable(r.items);
}

function renderDebugItemsTable(items) {
    const headers = ['#', 'Brand', 'Model', 'Description', 'Qty', 'Unit', 'Unit price', 'Total', 'Remark'];
    const rows = items.map((it, i) => {
        const cells = headers.map((h) => {
            const key = h === '#' ? '_idx'
                : h === 'Unit price' ? 'unit_price'
                : h.toLowerCase().replace(/\s/g, '_');
            const v = h === '#' ? (i + 1) : (it[key] || '');
            return `<td style="padding:6px 8px;font-size:12px;vertical-align:top;border-bottom:1px solid #eee">${escapeHtml(String(v))}</td>`;
        }).join('');
        return `<tr>${cells}</tr>`;
    }).join('');
    return `
        <div style="overflow-x:auto;max-height:50vh;overflow-y:auto;border:1px solid #e0e0e0;border-radius:6px">
            <table style="width:100%;border-collapse:collapse;font-size:12px">
                <thead style="position:sticky;top:0;background:#f5f5f5;z-index:1">
                    <tr>${headers.map(h =>
                        `<th style="padding:8px;text-align:left;font-size:11px;color:#666;border-bottom:1px solid #e0e0e0;white-space:nowrap">${escapeHtml(h)}</th>`
                    ).join('')}</tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
        </div>
    `;
}
