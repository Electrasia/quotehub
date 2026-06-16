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
    const extractionEnabled = document.getElementById('settingsExtractionEnabled').checked;
    const ocrEnabled = document.getElementById('settingsOcrEnabled').checked;
    const ocrLlmFallback = document.getElementById('settingsOcrLlmFallback').checked;
    const maxUploadSizeRaw = parseInt(document.getElementById('settingsMaxUploadSizeMb').value);
    const maxUploadSizeMb = Number.isFinite(maxUploadSizeRaw) ? maxUploadSizeRaw : 5;

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
                extraction_enabled: extractionEnabled,
                ocr_enabled: ocrEnabled,
                ocr_fallback_to_llm: ocrLlmFallback,
                max_upload_size_mb: maxUploadSizeMb,
            })
        });
        const result = await resp.json();
        if (!resp.ok) {
            const errMsgs = result.detail?.errors || [result.detail || 'Unknown error'];
            showBriefPopup('Validation error: ' + errMsgs.join('; '));
            return;
        }
        if (result.status === 'saved') {
            popupDurationSec = popupDuration;
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
            if (data.skipped > 0) {
                msg += ` ⚠ ${data.skipped} entry/entries skipped (no items).`;
                result.style.color = '#e67e22';
            } else if (data.warning) {
                msg += ` ⚠ ${data.warning}`;
                result.style.color = '#e67e22';
            } else {
                result.style.color = '#27ae60';
            }
            result.textContent = msg;
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
    const filter = document.getElementById('logLevel').value;
    
    // Map filter to level and category parameters
    let level = 'all';
    let category = 'all';
    
    if (filter === 'errors') {
        level = 'errors';
    } else if (filter !== 'all') {
        category = filter;
    }
    
    try {
        const resp = await fetch(`/logs?level=${level}&category=${category}`);
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

function loadCleanupStats() {
    if (!isMaster()) return;
    
    apiFetch('/cleanup/stats')
    .then(r => r.json().then(data => ({ ok: r.ok, data })))
    .then(({ ok, data }) => {
        if (!ok) throw new Error('Failed to load stats');
        
        document.getElementById('cleanupStatEntries').textContent = data.total_entries.toLocaleString();
        document.getElementById('cleanupStatPDFs').textContent = data.pdf_files.toLocaleString();
        document.getElementById('cleanupStatImages').textContent = data.image_dirs.toLocaleString();
        document.getElementById('cleanupStatSize').textContent = formatBytes(data.total_size);
        
        // Breakdown by type
        const breakdown = document.getElementById('cleanupStatBreakdown');
        if (data.by_type && Object.keys(data.by_type).length > 0) {
            const parts = Object.entries(data.by_type).map(([type, count]) => `${type}: ${count}`);
            breakdown.textContent = `By type: ${parts.join(' | ')}`;
        } else {
            breakdown.textContent = '';
        }
        
        document.getElementById('cleanupStats').classList.remove('hidden');
    })
    .catch(() => {
        // Silently ignore errors for stats
    });
}

function previewCleanup() {
    if (!isMaster()) { showBriefPopup('Only Master can run cleanup'); return; }
    const monthsRaw = parseInt(document.getElementById('cleanupMonths').value);
    const months = Number.isFinite(monthsRaw) && monthsRaw >= 1 ? monthsRaw : 6;
    const docType = document.getElementById('cleanupDocType').value || 'ALL';

    const btn = document.getElementById('cleanupPreviewBtn');
    btn.disabled = true;
    btn.textContent = 'Calculating…';

    apiFetch('/cleanup/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ months: months, document_type: docType })
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
    const docType = document.getElementById('cleanupDocType').value || 'ALL';

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
        body: JSON.stringify({ months: months, delete_files: deleteFiles, document_type: docType })
    })
    .then(r => r.json().then(data => ({ ok: r.ok, data })))
    .then(({ ok, data }) => {
        if (!ok || data.success === false) throw new Error(data.detail || 'Cleanup failed');
        showBriefPopup(
            `Cleanup complete. Deleted ${data.entries_deleted} entries, ${data.files_deleted} files, freed ${formatBytes(data.bytes_freed)}.`
        );
        hideCleanupForm();
        loadCleanupStats(); // Refresh stats after cleanup
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
    loadCleanupStats(); // Refresh stats
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
