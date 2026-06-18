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

// ─── Export Password Management ──────────────────────────────

let _exportPasswordMode = 'set'; // 'set' | 'change' | 'forgot' | 'export'

function loadExportPasswordStatus() {
    apiFetch('/export-password/status')
    .then(r => r.json().then(data => ({ ok: r.ok, data })))
    .then(({ ok, data }) => {
        if (!ok) throw new Error('Failed to check password status');
        const isSet = data.password_set;
        const statusEl = document.getElementById('exportPasswordStatus');
        const setBtn = document.getElementById('exportPasswordSetBtn');
        const changeBtn = document.getElementById('exportPasswordChangeBtn');
        const forgotBtn = document.getElementById('exportPasswordForgotBtn');

        if (isSet) {
            statusEl.textContent = '✅ Export password is set.';
            if (isMaster()) {
                setBtn.classList.add('hidden');
                changeBtn.classList.remove('hidden');
                forgotBtn.classList.remove('hidden');
            }
        } else {
            statusEl.textContent = '❌ No export password set. Set one before exporting.';
            if (isMaster()) {
                setBtn.classList.remove('hidden');
                changeBtn.classList.add('hidden');
                forgotBtn.classList.add('hidden');
            }
        }
    })
    .catch(() => {
        document.getElementById('exportPasswordStatus').textContent =
            '⚠ Could not check password status.';
    });
}

function showExportPasswordModal(mode) {
    _exportPasswordMode = mode;
    const titleEl = document.getElementById('exportPasswordModalTitle');
    const descEl = document.getElementById('exportPasswordModalDesc');
    const currentField = document.getElementById('exportPasswordCurrentField');
    const currentLabel = document.getElementById('exportPasswordCurrentLabel');
    const newField = document.getElementById('exportPasswordNew');
    const confirmField = document.getElementById('exportPasswordConfirm');
    const submitBtn = document.getElementById('exportPasswordSubmitBtn');
    const errorEl = document.getElementById('exportPasswordError');
    const strengthEl = document.getElementById('exportPasswordStrength');

    // Reset
    errorEl.classList.add('hidden');
    strengthEl.classList.add('hidden');
    document.getElementById('exportPasswordCurrent').value = '';
    newField.value = '';
    confirmField.value = '';

    if (mode === 'set') {
        titleEl.textContent = 'Set Export Password';
        descEl.textContent = 'Choose a strong export password (min 12 characters, must include uppercase, lowercase, digit, and special character).';
        currentField.classList.add('hidden');
        submitBtn.textContent = 'Set Password';
    } else if (mode === 'change') {
        titleEl.textContent = 'Change Export Password';
        descEl.textContent = 'Enter your current password and choose a new one.';
        currentLabel.textContent = 'Current Export Password';
        currentField.classList.remove('hidden');
        submitBtn.textContent = 'Change Password';
    } else if (mode === 'forgot') {
        titleEl.textContent = 'Reset Export Password';
        descEl.textContent = 'Verify your master login password to reset. WARNING: Existing backups encrypted with the old password become permanently unrecoverable.';
        currentLabel.textContent = 'Master Login Password';
        currentField.classList.remove('hidden');
        submitBtn.textContent = 'Reset Password';
    } else if (mode === 'export') {
        titleEl.textContent = 'Enter Export Password';
        descEl.textContent = 'Enter the export password to create an encrypted backup.';
        currentField.classList.add('hidden');
        newField.value = '';
        confirmField.value = '';
        submitBtn.textContent = 'Download Backup';
    }

    document.getElementById('exportPasswordModal').classList.add('active');
}

async function submitExportPassword() {
    const mode = _exportPasswordMode;
    const errorEl = document.getElementById('exportPasswordError');
    const strengthEl = document.getElementById('exportPasswordStrength');
    errorEl.classList.add('hidden');
    strengthEl.classList.add('hidden');

    if (mode === 'export') {
        // Export flow — password only, no management
        const password = document.getElementById('exportPasswordNew').value.trim();
        if (!password) {
            errorEl.textContent = 'Password is required';
            errorEl.classList.remove('hidden');
            return;
        }
        closeModal('exportPasswordModal');
        await runEncryptedExport(password);
        return;
    }

    // Password management flow
    const newPassword = document.getElementById('exportPasswordNew').value;
    const confirm = document.getElementById('exportPasswordConfirm').value;

    // Client-side validation
    if (newPassword.length < 12) {
        errorEl.textContent = 'Password must be at least 12 characters';
        errorEl.classList.remove('hidden');
        return;
    }
    if (!/[A-Z]/.test(newPassword)) {
        errorEl.textContent = 'Password must contain an uppercase letter';
        errorEl.classList.remove('hidden');
        return;
    }
    if (!/[a-z]/.test(newPassword)) {
        errorEl.textContent = 'Password must contain a lowercase letter';
        errorEl.classList.remove('hidden');
        return;
    }
    if (!/[0-9]/.test(newPassword)) {
        errorEl.textContent = 'Password must contain a digit';
        errorEl.classList.remove('hidden');
        return;
    }
    if (!/[^A-Za-z0-9]/.test(newPassword)) {
        errorEl.textContent = 'Password must contain a special character';
        errorEl.classList.remove('hidden');
        return;
    }
    if (newPassword !== confirm) {
        errorEl.textContent = 'Passwords do not match';
        errorEl.classList.remove('hidden');
        return;
    }

    const body = { new_password: newPassword };
    if (mode === 'change') {
        body.current_password = document.getElementById('exportPasswordCurrent').value.trim();
        if (!body.current_password) {
            errorEl.textContent = 'Current password is required';
            errorEl.classList.remove('hidden');
            return;
        }
    } else if (mode === 'forgot') {
        body.login_password = document.getElementById('exportPasswordCurrent').value.trim();
        if (!body.login_password) {
            errorEl.textContent = 'Login password is required';
            errorEl.classList.remove('hidden');
            return;
        }
    }

    const submitBtn = document.getElementById('exportPasswordSubmitBtn');
    submitBtn.disabled = true;
    submitBtn.textContent = 'Saving...';

    try {
        const resp = await apiFetch('/export-password', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await resp.json();
        if (!resp.ok) {
            const err = data.detail?.errors?.[0] || data.detail || 'Save failed';
            errorEl.textContent = err;
            errorEl.classList.remove('hidden');
            return;
        }
        closeModal('exportPasswordModal');
        const msg = data.status === 'set' ? 'Export password set' :
                    data.status === 'changed' ? 'Export password changed' :
                    'Export password reset';
        showBriefPopup(`✅ ${msg}.`);
        loadExportPasswordStatus();
    } catch (e) {
        errorEl.textContent = e.message || 'Failed to save password';
        errorEl.classList.remove('hidden');
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Save';
    }
}

// ─── Encrypted Export ───────────────────────────────────────

async function runEncryptedExport(password) {
    const btn = document.getElementById('exportBtn');
    const prog = document.getElementById('exportProgress');
    btn.disabled = true;
    prog.classList.remove('hidden');
    prog.textContent = 'Running integrity check...';
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
        prog.textContent = 'Downloading encrypted package...';
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `quodb_export_${new Date().toISOString().slice(0, 10)}.quodb`;
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

async function exportDatabase() {
    // Check if export password is set
    try {
        const resp = await apiFetch('/export-password/status');
        const data = await resp.json();
        if (!resp.ok || !data.password_set) {
            showBriefPopup('⚠ No export password set. Go to Settings → Backup / Restore to set one.');
            return;
        }
    } catch (e) {
        showBriefPopup('Could not check password status: ' + e.message);
        return;
    }
    // Show password prompt modal
    showExportPasswordModal('export');
}

// ─── Import ──────────────────────────────────────────────────

let _quodbFile = null;

async function importDatabase(input) {
    const file = input.files[0];
    if (!file) return;

    // Reset .quodb import form
    document.getElementById('quodbImportForm').classList.add('hidden');
    document.getElementById('quodbImportReport').classList.add('hidden');
    document.getElementById('quodbImportError').classList.add('hidden');

    // Prepare the .quodb import form
    _quodbFile = file;
    document.getElementById('quodbImportPassword').value = '';
    document.getElementById('quodbImportDryRun').checked = true;
    document.getElementById('quodbImportForm').classList.remove('hidden');
    document.getElementById('importResult').classList.add('hidden');
    document.getElementById('importProgress').classList.add('hidden');
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
    btn.textContent = dryRun ? 'Analyzing...' : 'Importing...';
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
            if (dryRun) {
                btn.textContent = 'Preview';
            } else {
                btn.textContent = 'Import';
                btn.disabled = false;
            }
            return;
        }

        if (dryRun) {
            // Show dry-run report
            renderImportReport(data);
            btn.textContent = 'Import';
            btn.disabled = false;
        } else {
            // Import applied
            let msg = '✅ Import complete.\n';
            const summary = data.summary || {};
            msg += `Records imported: ${summary.records_imported || 0}\n`;
            msg += `Records skipped (duplicate): ${summary.records_skipped_duplicate || 0}\n`;
            msg += `Files imported: ${summary.files_imported || 0}\n`;
            msg += `Files skipped: ${summary.files_skipped_duplicate || 0}\n`;
            if (summary.file_conflicts > 0) {
                msg += `⚠ File conflicts: ${summary.file_conflicts}\n`;
            }
            if (summary.warnings && summary.warnings.length > 0) {
                msg += '\nWarnings:\n' + summary.warnings.join('\n');
            }
            reportEl.textContent = msg;
            reportEl.classList.remove('hidden');
            reportEl.style.color = '#27ae60';
            btn.textContent = 'Done';
            btn.disabled = true;
            resetQuodbImport();
            if (!document.getElementById('searchView').classList.contains('hidden')) {
                searchQuotations();
            }
        }
    } catch (e) {
        document.getElementById('quodbImportError').textContent = e.message || 'Import failed';
        document.getElementById('quodbImportError').classList.remove('hidden');
        btn.textContent = dryRun ? 'Preview' : 'Import';
        btn.disabled = false;
    }
}

function renderImportReport(data) {
    const reportEl = document.getElementById('quodbImportReport');
    const summary = data.summary || {};
    let msg = '📋 DRY-RUN REPORT — No changes applied\n';
    msg += '═'.repeat(40) + '\n';
    msg += `Status: ${data.status || 'PREFLIGHT'}\n`;
    msg += `System ID match: ${data.systemIdMatch ? '✅ Yes' : '⚠ No'}\n`;
    msg += `Incoming records: ${summary.total_incoming_records || 0}\n`;
    msg += `Incoming files: ${summary.total_incoming_files || 0}\n`;
    msg += '\n';
    msg += `Records to import: ${summary.records_imported || 0}\n`;
    msg += `Records to skip (duplicates): ${summary.records_skipped_duplicate || 0}\n`;
    msg += `Files to import: ${summary.files_imported || 0}\n`;
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
    document.getElementById('quodbImportPassword').value = '';
    document.getElementById('quodbImportBtn').disabled = false;
    document.getElementById('quodbImportBtn').textContent = 'Import';
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
