// ─── AI Connection ───────────────────────────────────────────
function updateConnectionUI() {
    const btn = document.getElementById('connectBtn');
    const dot = document.getElementById('statusDot');
    const text = document.getElementById('statusText');
    if (isConnected) {
        btn.className = 'btn btn-danger btn-sm ai-admin-master';
        btn.textContent = 'Disconnect';
        dot.className = 'status-dot green';
        text.className = 'status-text connected';
        text.textContent = 'CONNECTED';
        document.getElementById('processAllBtn').disabled = false;
    } else {
        btn.className = 'btn btn-primary btn-sm ai-admin-master';
        btn.textContent = 'Connect to AI Server';
        dot.className = 'status-dot red';
        text.className = 'status-text disconnected';
        text.textContent = 'DISCONNECTED';
        document.getElementById('processAllBtn').disabled = true;
    }
    btn.disabled = false;
}

async function toggleConnection() {
    if (isConnected) {
        isConnected = false;
        updateConnectionUI();
        return;
    }
    const btn = document.getElementById('connectBtn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner" style="width:14px;height:14px;border-width:2px"></span> Connecting...';
    try {
        const response = await fetch('/ai/connect', { method: 'POST' });
        const data = await response.json();
        if (data.status === 'connected') {
            isConnected = true;
            updateConnectionUI();
        } else {
            throw new Error(data.error || 'Connection failed');
        }
    } catch (err) {
        document.getElementById('connectError').textContent = err.message || 'Unable to connect to AI server';
        document.getElementById('connectFailedModal').classList.add('active');
        updateConnectionUI();
    }
}

function retryConnect() {
    closeModal('connectFailedModal');
    toggleConnection();
}

// ─── Settings ────────────────────────────────────────────────
async function openSettings() {
    showSettings();
}

async function saveSettings() {
    const endpoint = document.getElementById('settingsEndpoint').value.trim();
    const model = document.getElementById('settingsModel').value.trim();
    const externalUrl = document.getElementById('settingsExternalUrl').value.trim();
    const timeoutRaw       = parseInt(document.getElementById('settingsTimeout').value);
    const retriesRaw       = parseInt(document.getElementById('settingsRetries').value);
    const popupDurationRaw = parseInt(document.getElementById('settingsPopupDuration').value);
    const ocrEnabled = document.getElementById('settingsOcrEnabled').checked;
    const ocrLlmFallback = document.getElementById('settingsOcrLlmFallback').checked;
    const extractionMode = document.getElementById('settingsExtractionMode').value;

    // Numeric safety: Number.isFinite() rejects NaN/Infinity, but allows 0 to pass through
    const timeout           = Number.isFinite(timeoutRaw)         ? timeoutRaw         : 120;
    const retries           = Number.isFinite(retriesRaw)         ? retriesRaw         : 3;
    const popupDuration     = Number.isFinite(popupDurationRaw)   ? popupDurationRaw   : 3;

    if (!endpoint) { showBriefPopup('AI endpoint URL is required'); return; }
    if (!model) { showBriefPopup('Model name is required'); return; }

    try {
        const resp = await fetch('/config', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                ai_endpoint: endpoint,
                model: model,
                external_url: externalUrl,
                timeout: timeout,
                max_retries: retries,
                popup_duration: popupDuration,
                ocr_enabled: ocrEnabled,
                ocr_fallback_to_llm: ocrLlmFallback,
                extraction_mode: extractionMode,
            })
        });
        const result = await resp.json();
        if (result.status === 'saved') {
            popupDurationSec = popupDuration;
            updateExtractionModeBadge(extractionMode);
            showBriefPopup('Settings saved!');
        }
    } catch (e) {
        showBriefPopup('Failed to save settings: ' + e.message);
    }
}

// ─── Backup / Restore ────────────────────────────────────────
function openBackupRestore() {
    showSettings();
}

async function exportDatabase() {
    const btn = document.getElementById('exportBtn');
    const prog = document.getElementById('exportProgress');
    btn.disabled = true;
    prog.classList.remove('hidden');
    prog.textContent = 'Preparing backup...';
    try {
        const resp = await fetch('/export');
        prog.textContent = 'Creating archive with PDFs...';
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `quodb_backup_${new Date().toISOString().slice(0, 10)}.zip`;
        a.click();
        URL.revokeObjectURL(url);
        prog.textContent = 'Done!';
        setTimeout(() => { prog.classList.add('hidden'); }, 2000);
    } catch (e) {
        showBriefPopup('Export failed: ' + e.message);
        prog.classList.add('hidden');
    } finally {
        btn.disabled = false;
    }
}

async function importDatabase(input) {
    const file = input.files[0];
    if (!file) return;
    const btn = document.getElementById('importBtn');
    const prog = document.getElementById('importProgress');
    const result = document.getElementById('importResult');
    btn.disabled = true;
    prog.classList.remove('hidden');
    result.classList.add('hidden');
    prog.textContent = `Reading ${file.name}...`;
    const formData = new FormData();
    formData.append('file', file);
    try {
        prog.textContent = 'Uploading and importing...';
        const resp = await fetch('/import/upload', { method: 'POST', body: formData });
        const data = await resp.json();
        if (data.status === 'imported') {
            let msg = `✓ Imported ${data.count} quotation(s) successfully.`;
            if (data.pdfs_restored > 0) msg += ` ${data.pdfs_restored} PDF file(s) restored.`;
            result.textContent = msg;
            result.style.color = '#27ae60';
            result.classList.remove('hidden');
            prog.classList.add('hidden');
            // Auto-refresh search if on search page
            if (!document.getElementById('searchView').classList.contains('hidden')) {
                searchQuotations();
            }
        } else {
            result.textContent = '✗ ' + (data.error || 'Import failed');
            result.style.color = '#e74c3c';
            result.classList.remove('hidden');
            prog.classList.add('hidden');
        }
    } catch (e) {
        result.textContent = '✗ Import failed: ' + e.message;
        result.style.color = '#e74c3c';
        result.classList.remove('hidden');
        prog.classList.add('hidden');
    } finally {
        btn.disabled = false;
        input.value = '';
    }
}

// ─── Logs ────────────────────────────────────────────────────
function saveLogs() {
    showSettings();
}

async function downloadLogs() {
    const level = document.getElementById('logLevel').value;
    try {
        const resp = await fetch(`/logs?level=${level}`);
        if (!resp.ok) {
            const text = await resp.text().catch(() => 'Server error');
            showBriefPopup('Failed to get logs: ' + text);
            return;
        }
        const contentType = resp.headers.get('content-type') || '';
        let logs;
        if (contentType.includes('application/json')) {
            const data = await resp.json();
            logs = data.logs || 'No logs available';
        } else {
            logs = await resp.text();
        }
        const blob = new Blob([logs], { type: 'text/plain' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `quodb_logs_${new Date().toISOString().slice(0, 10)}.txt`;
        a.click();
        URL.revokeObjectURL(url);
        showBriefPopup('Logs downloaded!');
    } catch (e) {
        showBriefPopup('Failed to get logs: ' + e.message);
    }
}

// ─── Idle detection (UX improvement; backend is source of truth) ─
let idleTimeoutMs = 15 * 60 * 1000;  // default 15 min; updated from config
let idleTimer = null;

function resetIdleTimer() {
    if (idleTimer) clearTimeout(idleTimer);
    if (idleTimeoutMs <= 0) return;  // disabled
    idleTimer = setTimeout(() => {
        // Only auto-logout if user is currently logged in
        if (typeof currentUser !== 'undefined' && currentUser) {
            showBriefPopup('Session expired due to inactivity');
            // Brief delay so user sees the popup before the login overlay appears
            setTimeout(() => doLogout(), 500);
        }
    }, idleTimeoutMs);
}

function startIdleDetection() {
    ['mousemove', 'click', 'keydown', 'scroll', 'touchstart'].forEach(evt => {
        document.addEventListener(evt, resetIdleTimer, { passive: true });
    });
    resetIdleTimer();
}

function updateIdleTimeoutFromConfig(cfg) {
    if (!cfg) return;
    const minutes = parseInt(cfg.idle_timeout_minutes);
    if (Number.isFinite(minutes)) {
        idleTimeoutMs = minutes * 60 * 1000;
        resetIdleTimer();
    }
}

// Boot idle detection once settings.js loads (defer → DOM ready)
startIdleDetection();

// ─── System Cleanup (master only) ─────────────────────────

function previewCleanup() {
    if (!isMaster()) { showBriefPopup('Only Master can run cleanup'); return; }
    const monthsRaw = parseInt(document.getElementById('cleanupMonths').value);
    const months = Number.isFinite(monthsRaw) && monthsRaw >= 1 ? monthsRaw : 6;

    const btn = document.getElementById('cleanupPreviewBtn');
    btn.disabled = true;
    btn.textContent = 'Calculating…';

    apiFetch('/cleanup/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ months: months })
    })
    .then(r => r.json().then(data => ({ ok: r.ok, data })))
    .then(({ ok, data }) => {
        if (!ok) throw new Error(data.detail || 'Preview failed');
        document.getElementById('cleanupPreviewEntries').textContent = data.entries;
        document.getElementById('cleanupPreviewFiles').textContent = data.files;
        document.getElementById('cleanupPreviewSize').textContent = formatBytes(data.estimated_size);
        document.getElementById('cleanupPreviewCutoff').textContent = data.cutoff_date;
        document.getElementById('cleanupPreviewResult').classList.remove('hidden');
        document.getElementById('cleanupExecuteForm').classList.remove('hidden');
        document.getElementById('cleanupDeleteFiles').checked = false;
        document.getElementById('cleanupConfirmInput').value = '';
        document.getElementById('cleanupExecuteBtn').disabled = true;
    })
    .catch(e => {
        showBriefPopup('Preview failed: ' + e.message);
    })
    .finally(() => {
        btn.disabled = false;
        btn.textContent = 'Preview';
    });
}

function executeCleanup() {
    if (!isMaster()) { showBriefPopup('Only Master can run cleanup'); return; }
    const monthsRaw = parseInt(document.getElementById('cleanupMonths').value);
    const months = Number.isFinite(monthsRaw) && monthsRaw >= 1 ? monthsRaw : 6;
    const deleteFiles = document.getElementById('cleanupDeleteFiles').checked;
    const confirmText = document.getElementById('cleanupConfirmInput').value.trim();

    if (confirmText !== 'DELETE') {
        showBriefPopup('Type DELETE exactly to confirm');
        return;
    }

    const btn = document.getElementById('cleanupExecuteBtn');
    btn.disabled = true;
    btn.textContent = 'Deleting…';

    apiFetch('/cleanup/execute', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ months: months, delete_files: deleteFiles })
    })
    .then(r => r.json().then(data => ({ ok: r.ok, data })))
    .then(({ ok, data }) => {
        if (!ok) throw new Error(data.detail || 'Cleanup failed');
        showBriefPopup(
            `Cleanup complete. Deleted ${data.entries_deleted} entries, ${data.files_deleted} files, freed ${formatBytes(data.bytes_freed)}.`
        );
        hideCleanupForm();
    })
    .catch(e => {
        showBriefPopup('Cleanup failed: ' + e.message);
    })
    .finally(() => {
        btn.disabled = false;
        btn.textContent = 'Delete Permanently';
    });
}

function hideCleanupForm() {
    document.getElementById('cleanupExecuteForm').classList.add('hidden');
    document.getElementById('cleanupPreviewResult').classList.add('hidden');
    document.getElementById('cleanupConfirmInput').value = '';
    document.getElementById('cleanupExecuteBtn').disabled = true;
}

// Live-validate the DELETE confirmation input
document.addEventListener('DOMContentLoaded', () => {
    const inp = document.getElementById('cleanupConfirmInput');
    if (inp) {
        inp.addEventListener('input', () => {
            document.getElementById('cleanupExecuteBtn').disabled =
                inp.value.trim() !== 'DELETE';
        });
    }
});

// ─── Debug Parse (master only) — v0.037.0 Phase 1 ──────────
let _lastDebugResult = null;
let _currentDebugTab = 'plumber';

async function runDebugParse() {
    if (!isMaster()) { showBriefPopup('Only Master can run debug parse'); return; }
    const idxRaw = parseInt(document.getElementById('debugParseIndex').value);
    if (!Number.isFinite(idxRaw) || idxRaw < 0) {
        showBriefPopup('File index must be 0 or greater');
        return;
    }
    const btn = document.getElementById('debugParseBtn');
    const status = document.getElementById('debugParseStatus');
    btn.disabled = true;
    status.textContent = 'Parsing…';
    status.style.color = '#666';
    try {
        const resp = await apiFetch(`/debug/parse?file_index=${idxRaw}`);
        const data = await resp.json().then(d => ({ ok: resp.ok, data: d }));
        if (!data.ok) throw new Error(data.data.detail || 'Parse failed');
        _lastDebugResult = data.data;
        _currentDebugTab = 'plumber';
        renderDebugParseModal();
        openModal('debugParseModal');
        status.textContent = `Done in ${(data.data.parsers?.pdfplumber?.time_ms || 0) + (data.data.parsers?.pymupdf?.time_ms || 0)}ms`;
        status.style.color = '#27ae60';
    } catch (e) {
        status.textContent = 'Error: ' + e.message;
        status.style.color = '#e74c3c';
        showBriefPopup('Debug parse failed: ' + e.message);
    } finally {
        btn.disabled = false;
    }
}

function renderDebugParseModal() {
    const r = _lastDebugResult;
    if (!r) return;
    // Summary
    const pp = r.parsers?.pdfplumber || {};
    const pm = r.parsers?.pymupdf || {};
    const fmt = (b) => {
        if (!Number.isFinite(b)) return '—';
        const k = 1024, u = ['B','KB','MB','GB'];
        const i = Math.min(Math.floor(Math.log(b)/Math.log(k)), u.length-1);
        return Math.round((b/Math.pow(k,i))*100)/100 + ' ' + u[i];
    };
    // Show which pdfplumber table strategy won per page
    let strategiesLine = '';
    if (pp.table_strategies_used && pp.table_strategies_used.length) {
        const s = pp.table_strategies_used
            .map(x => `p${x.page}:${x.strategy}(${x.rich_rows} rich rows)`)
            .join('  ');
        strategiesLine = `<div style="font-size:11px;color:#888;margin-top:4px">
            table strategies picked: ${escapeHtml(s)}
        </div>`;
    }
    document.getElementById('debugParseSummary').innerHTML = `
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
                    ? `<span style="color:#e74c3c">unavailable: ${pm.error || ''}</span>`
                    : `<span style="color:#27ae60">${pm.time_ms}ms</span>, ${pm.total_text_chars} chars`}
            </span>
        </div>
        ${strategiesLine}
    `;
    renderDebugTab();
}

function renderDebugTab() {
    if (!_lastDebugResult) return;
    const r = _lastDebugResult;
    const content = document.getElementById('debugParseContent');
    const tab = _currentDebugTab;
    // Update active tab style
    ['plumber','plumberTables','mupdf','raw'].forEach(t => {
        const el = document.getElementById('debugTab' + (t === 'plumber' ? 'Plumber' : t === 'plumberTables' ? 'PlumberTables' : t === 'mupdf' ? 'Mupdf' : 'Raw'));
        if (el) {
            el.style.background = t === tab ? '#3498db' : '';
            el.style.color = t === tab ? '#fff' : '';
        }
    });
    if (tab === 'raw') {
        content.textContent = JSON.stringify(r, null, 2);
        return;
    }
    let out = '';
    if (tab === 'plumber' || tab === 'plumberTables') {
        const p = r.parsers?.pdfplumber;
        if (!p || p.available === false) {
            content.textContent = 'pdfplumber unavailable: ' + (p?.error || '?');
            return;
        }
        if (tab === 'plumberTables') {
            const lines = [];
            p.pages.forEach(pg => {
                lines.push(`─── Page ${pg.page} ───`);
                if (!pg.tables || pg.tables.length === 0) {
                    lines.push('  (no tables detected)');
                } else {
                    pg.tables.forEach(t => {
                        lines.push(`  Table ${t.table_index}: ${t.row_count} rows (showing ${t.shown_rows})`);
                        t.rows.forEach((row, i) => {
                            const cells = Array.isArray(row) ? row.map(c => (c || '').toString().replace(/\s+/g, ' ').trim()) : ['(malformed)'];
                            lines.push(`    [${i}] ${cells.join(' | ')}`);
                        });
                    });
                }
                lines.push('');
            });
            content.textContent = lines.join('\n');
            return;
        }
        // tab === 'plumber' (text)
        p.pages.forEach(pg => {
            out += `─── Page ${pg.page} (${pg.text_chars} chars) ───\n${pg.text}\n\n`;
        });
        content.textContent = out || '(empty)';
        return;
    }
    if (tab === 'mupdf') {
        const p = r.parsers?.pymupdf;
        if (!p || p.available === false) {
            content.textContent = 'pymupdf unavailable: ' + (p?.error || '?');
            return;
        }
        p.pages.forEach(pg => {
            const bc = pg.block_count != null ? `, ${pg.block_count} blocks` : '';
            out += `─── Page ${pg.page} (${pg.text_chars} chars${bc}) ───\n${pg.text}\n\n`;
        });
        content.textContent = out || '(empty)';
        return;
    }
}

function switchDebugTab(tab) {
    _currentDebugTab = tab;
    renderDebugTab();
}

function copyDebugJson() {
    if (!_lastDebugResult) return;
    const text = JSON.stringify(_lastDebugResult, null, 2);
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(
            () => showBriefPopup('JSON copied to clipboard'),
            () => fallbackCopy(text)
        );
    } else {
        fallbackCopy(text);
    }
}

function fallbackCopy(text) {
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); showBriefPopup('JSON copied'); }
    catch (e) { showBriefPopup('Copy failed'); }
    document.body.removeChild(ta);
}
