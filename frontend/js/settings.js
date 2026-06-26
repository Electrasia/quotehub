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
            showBriefPopup('Settings saved');
        }
    } catch (e) {
        showBriefPopup('Failed to save settings: ' + e.message);
    }
}

// ─── Backup / Restore ────────────────────────────────────────
function openBackupRestore() {
    showSettings();
}

// ─── Export ─────────────────────────────────────────────────

function showExportModal() {
    // Reset form
    document.getElementById('exportPassword').value = '';
    document.getElementById('exportPasswordConfirm').value = '';
    document.getElementById('exportError').classList.add('hidden');
    document.getElementById('exportStrengthBar').classList.add('hidden');
    document.getElementById('exportStrengthLabel').classList.add('hidden');
    document.getElementById('exportSubmitBtn').disabled = true;
    document.getElementById('exportModalBody').classList.remove('hidden');
    document.getElementById('exportProgressBody').classList.add('hidden');
    document.getElementById('exportModal').classList.add('active');
}

function togglePassword(fieldId, toggleEl) {
    const field = document.getElementById(fieldId);
    if (field.type === 'password') {
        field.type = 'text';
        toggleEl.textContent = '🙈';
    } else {
        field.type = 'password';
        toggleEl.textContent = '👁';
    }
}

function calcPasswordStrength(pw) {
    let score = 0;
    if (pw.length >= 12) score += 25;
    if (pw.length >= 16) score += 10;
    if (/[A-Z]/.test(pw)) score += 15;
    if (/[a-z]/.test(pw)) score += 15;
    if (/[0-9]/.test(pw)) score += 15;
    if (/[^A-Za-z0-9]/.test(pw)) score += 20;
    return Math.min(score, 100);
}

function strengthLabel(score) {
    if (score < 40) return { label: 'Weak', color: '#e74c3c' };
    if (score < 70) return { label: 'Fair', color: '#f39c12' };
    return { label: 'Strong', color: '#27ae60' };
}

function validateExportPassword(pw) {
    if (pw.length < 12) return 'Password must be at least 12 characters';
    if (!/[A-Z]/.test(pw)) return 'Must include an uppercase letter';
    if (!/[a-z]/.test(pw)) return 'Must include a lowercase letter';
    if (!/[0-9]/.test(pw)) return 'Must include a digit';
    if (!/[^A-Za-z0-9]/.test(pw)) return 'Must include a special character';
    return null;
}

function updateExportButton() {
    const pw = document.getElementById('exportPassword').value;
    const confirm = document.getElementById('exportPasswordConfirm').value;
    const errorEl = document.getElementById('exportError');
    const barEl = document.getElementById('exportStrengthBar');
    const fillEl = document.getElementById('exportStrengthFill');
    const labelEl = document.getElementById('exportStrengthLabel');

    // Strength
    if (pw.length > 0) {
        const score = calcPasswordStrength(pw);
        const info = strengthLabel(score);
        barEl.classList.remove('hidden');
        fillEl.style.width = score + '%';
        fillEl.style.background = info.color;
        labelEl.classList.remove('hidden');
        labelEl.textContent = info.label;
        labelEl.style.color = info.color;
    } else {
        barEl.classList.add('hidden');
        labelEl.classList.add('hidden');
    }

    // Validation
    errorEl.classList.add('hidden');
    if (!pw || !confirm) {
        document.getElementById('exportSubmitBtn').disabled = true;
        return;
    }
    if (pw !== confirm) {
        document.getElementById('exportSubmitBtn').disabled = true;
        return;
    }
    const err = validateExportPassword(pw);
    if (err) {
        document.getElementById('exportSubmitBtn').disabled = true;
        return;
    }
    document.getElementById('exportSubmitBtn').disabled = false;
}

async function submitExport() {
    const password = document.getElementById('exportPassword').value;
    const errorEl = document.getElementById('exportError');
    const modalBody = document.getElementById('exportModalBody');
    const progressBody = document.getElementById('exportProgressBody');
    const progressFill = document.getElementById('exportProgressFill');
    const progressText = document.getElementById('exportProgressText');

    // Show progress
    modalBody.classList.add('hidden');
    progressBody.classList.remove('hidden');
    progressFill.style.width = '10%';
    progressText.textContent = 'Preparing...';

    try {
        const resp = await apiFetch('/export/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password }),
        });
        if (!resp.ok) {
            const data = await resp.json().catch(() => ({}));
            throw new Error(data.detail || 'Export failed');
        }
        progressFill.style.width = '60%';
        progressText.textContent = 'Downloading...';

        const blob = await resp.blob();
        progressFill.style.width = '90%';
        progressText.textContent = 'Finalizing...';

        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `quodb_export_${new Date().toISOString().slice(0, 10)}.quodb`;
        a.click();
        URL.revokeObjectURL(url);

        closeModal('exportModal');
        showBriefPopup('✅ Backup downloaded.');
    } catch (e) {
        errorEl.textContent = e.message || 'Export failed';
        errorEl.classList.remove('hidden');
        modalBody.classList.remove('hidden');
        progressBody.classList.add('hidden');
    }
}

async function exportDatabase() {
    showExportModal();
}

// ─── Import ──────────────────────────────────────────────────

let _quodbFile = null;

async function importDatabase(input) {
    const file = input.files[0];
    if (!file) return;

    // Reset form
    document.getElementById('quodbImportForm').classList.add('hidden');
    document.getElementById('quodbImportReport').classList.add('hidden');
    document.getElementById('quodbImportError').classList.add('hidden');
    document.getElementById('quodbAttribution').classList.add('hidden');

    _quodbFile = file;
    document.getElementById('quodbImportPassword').value = '';
    document.getElementById('quodbImportDryRun').checked = false;
    document.getElementById('quodbImportForm').classList.remove('hidden');
    document.getElementById('importResult').classList.add('hidden');
    document.getElementById('importProgress').classList.add('hidden');
    document.getElementById('quodbImportBtn').disabled = false;
    document.getElementById('quodbImportBtn').textContent = 'Restore';
}

async function runQuodbImport() {
    const password = document.getElementById('quodbImportPassword').value.trim();
    if (!password) {
        document.getElementById('quodbImportError').textContent = 'Password is required';
        document.getElementById('quodbImportError').classList.remove('hidden');
        return;
    }
    document.getElementById('quodbImportError').classList.add('hidden');

    const dryRun = document.getElementById('quodbImportDryRun').checked;
    const btn = document.getElementById('quodbImportBtn');
    const reportEl = document.getElementById('quodbImportReport');

    btn.disabled = true;
    btn.textContent = dryRun ? 'Analyzing...' : 'Restoring...';
    reportEl.classList.add('hidden');

    const formData = new FormData();
    formData.append('file', _quodbFile);
    formData.append('password', password);
    formData.append('dry_run', String(dryRun));
    formData.append('force_system_id', 'false');

    try {
        const resp = await apiFetch('/import/run', { method: 'POST', body: formData });
        const data = await resp.json();
        if (!resp.ok) {
            const err = data.detail || 'Import failed';
            document.getElementById('quodbImportError').textContent = err;
            document.getElementById('quodbImportError').classList.remove('hidden');
            btn.textContent = dryRun ? 'Preview' : 'Restore';
            btn.disabled = false;
            return;
        }

        // Show attribution
        const attr = data.exportAttribution || {};
        const attrEl = document.getElementById('quodbAttribution');
        if (attr.masterDisplayName) {
            attrEl.textContent = `Created by: ${attr.masterDisplayName} (${attr.masterRole || 'master'}) · ${attr.exportedAtUtc ? new Date(attr.exportedAtUtc).toLocaleDateString() : ''}`;
            attrEl.classList.remove('hidden');
        }

        if (dryRun) {
            renderImportReport(data);
            btn.textContent = 'Restore';
            btn.disabled = false;
        } else {
            // Import applied
            const summary = data.summary || {};
            let msg = '✅ Restore complete.\n';
            msg += `${summary.records_imported || 0} records restored`;
            if (summary.records_skipped_duplicate > 0) msg += ` (${summary.records_skipped_duplicate} duplicates skipped)`;
            msg += '\n';
            msg += `${summary.files_imported || 0} files restored`;
            if (summary.files_skipped_duplicate > 0) msg += ` (${summary.files_skipped_duplicate} existing skipped)`;
            msg += '\n';
            if (summary.file_conflicts > 0) {
                msg += `⚠ ${summary.file_conflicts} file conflict(s) — existing files differ from backup\n`;
            }
            if (summary.warnings && summary.warnings.length > 0) {
                msg += '\nWarnings:\n' + summary.warnings.join('\n');
            }
            reportEl.textContent = msg;
            reportEl.classList.remove('hidden');
            reportEl.style.color = '#27ae60';
            btn.textContent = 'Done';
            btn.disabled = true;
            if (!document.getElementById('searchView').classList.contains('hidden')) {
                searchQuotations();
            }
        }
    } catch (e) {
        document.getElementById('quodbImportError').textContent = e.message || 'Import failed';
        document.getElementById('quodbImportError').classList.remove('hidden');
        btn.textContent = dryRun ? 'Preview' : 'Restore';
        btn.disabled = false;
    }
}

function renderImportReport(data) {
    const reportEl = document.getElementById('quodbImportReport');
    const summary = data.summary || {};
    let msg = '📋 Preview — No changes applied yet\n';
    msg += '═'.repeat(40) + '\n';
    msg += `Records to restore: ${summary.records_imported || 0}\n`;
    msg += `Records to skip (duplicates): ${summary.records_skipped_duplicate || 0}\n`;
    msg += `Files to restore: ${summary.files_imported || 0}\n`;
    msg += `Files to skip (identical): ${summary.files_skipped_duplicate || 0}\n`;
    if (summary.file_conflicts > 0) {
        msg += `⚠ File conflicts: ${summary.file_conflicts}\n`;
    }
    if (summary.errors && summary.errors.length > 0) {
        msg += '\nErrors:\n' + summary.errors.join('\n');
    }
    if (summary.warnings && summary.warnings.length > 0) {
        msg += '\nWarnings:\n' + summary.warnings.join('\n');
    }
    reportEl.textContent = msg;
    reportEl.style.color = '#333';
    reportEl.classList.remove('hidden');
}

function resetQuodbImport() {
    _quodbFile = null;
    document.getElementById('quodbImportForm').classList.add('hidden');
    document.getElementById('quodbImportReport').classList.add('hidden');
    document.getElementById('quodbImportError').classList.add('hidden');
    document.getElementById('quodbAttribution').classList.add('hidden');
    document.getElementById('quodbImportPassword').value = '';
    document.getElementById('quodbImportBtn').disabled = false;
    document.getElementById('quodbImportBtn').textContent = 'Restore';
    document.getElementById('importBtn').disabled = false;
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

// ═══════════════════════════════════════════════════════════════
// ─── Auto-backup ─────────────────────────────────────────────
// ═══════════════════════════════════════════════════════════════

async function refreshAutoBackupStatus() {
    try {
        const resp = await apiFetch('/auto-backup/status');
        if (!resp.ok) return;
        const data = await resp.json();

        const section = document.getElementById('autoBackupSection');
        if (!section) return;

        if (!data.active) {
            section.classList.add('hidden');
            return;
        }

        // Show the section (for master — the parent card handles role visibility)
        section.classList.remove('hidden');

        // Last backup
        const lastEl = document.getElementById('autoBackupLast');
        if (data.lastBackup.date) {
            lastEl.textContent = data.lastBackup.date;
        } else {
            lastEl.textContent = 'Never';
        }

        // Status badge
        const statusEl = document.getElementById('autoBackupStatus');
        const statusLine = document.getElementById('autoBackupStatusLine');
        if (data.lastBackup.status === 'SUCCESS') {
            statusEl.textContent = '✅';
            statusEl.className = '';
            statusLine.style.display = 'none';
        } else if (data.lastBackup.status === 'FAILED') {
            statusEl.textContent = '❌';
            statusEl.className = '';
            statusLine.style.display = '';
        } else {
            statusEl.textContent = '—';
            statusEl.className = '';
            statusLine.style.display = 'none';
        }

        // Next scheduled
        const nextEl = document.getElementById('autoBackupNext');
        if (data.nextScheduled) {
            const dt = new Date(data.nextScheduled);
            nextEl.textContent = dt.toLocaleString();
        } else {
            nextEl.textContent = '—';
        }
    } catch (e) {
        // Auto-backup disabled or not available — hide section
        const section = document.getElementById('autoBackupSection');
        if (section) section.classList.add('hidden');
    }
}

let _autoRestoreSelectedFile = null;

async function showAutoRestoreModal() {
    if (!isMaster()) { showBriefPopup('Only Master can restore backups'); return; }
    _autoRestoreSelectedFile = null;
    document.getElementById('autoRestoreBody').querySelectorAll('.hidden').forEach(el => el.classList.add('hidden'));
    document.getElementById('autoRestoreLoading').classList.remove('hidden');
    document.getElementById('autoRestoreList').classList.add('hidden');
    document.getElementById('autoRestoreReport').classList.add('hidden');
    document.getElementById('autoRestoreError').classList.add('hidden');
    document.getElementById('autoRestoreConfirmBtn').classList.add('hidden');
    openModal('autoRestoreModal');

    try {
        const resp = await apiFetch('/auto-backup/list');
        if (!resp.ok) throw new Error('Failed to load backups');
        const data = await resp.json();
        renderAutoRestoreList(data);
    } catch (e) {
        document.getElementById('autoRestoreLoading').textContent = 'Error loading backups: ' + e.message;
        return;
    }
    document.getElementById('autoRestoreLoading').classList.add('hidden');
    document.getElementById('autoRestoreList').classList.remove('hidden');
}

function renderAutoRestoreList(data) {
    const container = document.getElementById('autoRestoreList');
    // Clear existing content using textContent-free method
    container.innerHTML = '';

    // Helper to build a single backup entry row using DOM APIs
    // (avoids innerHTML string interpolation — prevents XSS from filenames/paths)
    function _addBackupEntry(containerEl, f, iconClass, iconLabel) {
        const row = document.createElement('div');
        row.style.cssText = 'padding:6px 8px;cursor:pointer;border-radius:4px;font-size:13px';
        row.addEventListener('mouseenter', () => { row.style.background = iconClass; });
        row.addEventListener('mouseleave', () => { row.style.background = ''; });
        row.addEventListener('click', () => autoRestoreSelect(f.path));

        const label = f.name.replace(/\.quodb$/, '').replace(/_/g, ' ');
        const size = (f.sizeBytes / 1048576).toFixed(1);
        row.textContent = `${iconLabel} ${label} (${size} MB)`;

        containerEl.appendChild(row);
    }

    // Daily + weekly (recent backups)
    const recent = [...(data.daily || []), ...(data.weekly || [])];
    recent.sort((a, b) => new Date(b.modifiedUtc) - new Date(a.modifiedUtc));

    if (recent.length > 0) {
        const heading = document.createElement('h4');
        heading.style.cssText = 'margin:8px 0 6px;font-size:13px';
        heading.textContent = 'Recent automatic backups';
        container.appendChild(heading);

        const list = document.createElement('div');
        list.style.marginBottom = '12px';
        for (const f of recent.slice(0, 10)) {
            _addBackupEntry(list, f, '#e8f5e9', '📅');
        }
        container.appendChild(list);
    }

    // Events
    if (data.events && data.events.length > 0) {
        const heading = document.createElement('h4');
        heading.style.cssText = 'margin:8px 0 6px;font-size:13px';
        heading.textContent = 'Before recent events';
        container.appendChild(heading);

        const list = document.createElement('div');
        list.style.marginBottom = '12px';
        for (const f of data.events.slice(0, 20)) {
            _addBackupEntry(list, f, '#fff3e0', '🔶');
        }
        container.appendChild(list);
    }

    if (!recent.length && (!data.events || !data.events.length)) {
        const msg = document.createElement('p');
        msg.style.cssText = 'font-size:13px;color:#999';
        msg.textContent = 'No automatic backups found.';
        container.appendChild(msg);
    }
}

async function autoRestoreSelect(filePath) {
    _autoRestoreSelectedFile = filePath;
    document.getElementById('autoRestoreList').classList.add('hidden');
    document.getElementById('autoRestoreReport').classList.add('hidden');
    document.getElementById('autoRestoreError').classList.add('hidden');
    document.getElementById('autoRestoreConfirmBtn').classList.add('hidden');
    document.getElementById('autoRestoreLoading').classList.remove('hidden');
    document.getElementById('autoRestoreLoading').textContent = 'Analyzing backup…';

    try {
        const formData = new FormData();
        formData.append('filename', filePath);
        formData.append('dry_run', 'true');
        formData.append('force_system_id', 'false');

        const resp = await apiFetch('/auto-backup/restore', {
            method: 'POST',
            body: formData,
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || 'Restore check failed');

        renderAutoRestoreReport(data);
        document.getElementById('autoRestoreConfirmBtn').classList.remove('hidden');
    } catch (e) {
        document.getElementById('autoRestoreError').textContent = e.message;
        document.getElementById('autoRestoreError').classList.remove('hidden');
    }
    document.getElementById('autoRestoreLoading').classList.add('hidden');
}

function renderAutoRestoreReport(data) {
    const container = document.getElementById('autoRestoreReport');
    container.classList.remove('hidden');

    if (data.status === 'FAILED') {
        container.innerHTML = `<div style="padding:12px;background:#fde8e8;border-radius:6px;font-size:13px;color:#c0392b">
            <strong>Restore blocked:</strong> ${data.detail || 'Unknown error'}
        </div>`;
        document.getElementById('autoRestoreConfirmBtn').classList.add('hidden');
        return;
    }

    const summary = data.summary || {};
    const warnings = data.warnings || [];
    const fileConflicts = summary.file_conflicts || 0;

    let html = `<div style="padding:12px;background:#f0f7ff;border-radius:6px;font-size:13px">
        <strong>Restore preview from:</strong> ${_autoRestoreSelectedFile}<br>`;

    if (data.status === 'PREFLIGHT') {
        html += `<div style="margin-top:8px">
            <span style="color:#27ae60">✅ ${summary.records_imported || 0} records will be restored</span><br>
            <span style="color:#999">${summary.records_skipped_duplicate || 0} duplicates will be skipped</span><br>
            <span style="color:#999">${summary.files_imported || 0} files will be restored</span><br>`;
        if (summary.files_skipped_duplicate > 0) {
            html += `<span style="color:#999">${summary.files_skipped_duplicate} existing files will be kept</span><br>`;
        }
        if (fileConflicts > 0) {
            html += `<span style="color:#e67e22">⚠ ${fileConflicts} file conflict(s) — existing files differ from backup</span><br>`;
        }
        html += '</div>';
    } else if (data.status === 'SUCCESS') {
        html += `<div style="margin-top:8px;color:#27ae60">
            ✅ Restore complete. ${summary.records_imported || 0} records restored, ${summary.files_imported || 0} files restored.
        </div>`;
    }

    if (warnings.length > 0) {
        html += '<div style="margin-top:8px;padding:8px;background:#fef9e7;border-radius:4px">';
        for (const w of warnings) {
            html += `<div style="font-size:12px;color:#856404">⚠ ${w}</div>`;
        }
        html += '</div>';
    }

    html += '</div>';
    container.innerHTML = html;
}

async function autoRestoreConfirm() {
    if (!_autoRestoreSelectedFile) return;

    document.getElementById('autoRestoreConfirmBtn').disabled = true;
    document.getElementById('autoRestoreConfirmBtn').textContent = 'Restoring…';

    try {
        const formData = new FormData();
        formData.append('filename', _autoRestoreSelectedFile);
        formData.append('dry_run', 'false');
        formData.append('force_system_id', 'false');

        const resp = await apiFetch('/auto-backup/restore', {
            method: 'POST',
            body: formData,
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || 'Restore failed');

        renderAutoRestoreReport(data);
        document.getElementById('autoRestoreConfirmBtn').classList.add('hidden');
        showBriefPopup('✅ Restore complete');
        refreshAutoBackupStatus();
    } catch (e) {
        document.getElementById('autoRestoreError').textContent = e.message;
        document.getElementById('autoRestoreError').classList.remove('hidden');
    } finally {
        document.getElementById('autoRestoreConfirmBtn').disabled = false;
        document.getElementById('autoRestoreConfirmBtn').textContent = 'Confirm & Restore';
    }
}
