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
    const timeout = parseInt(document.getElementById('settingsTimeout').value) || 120;
    const retries = parseInt(document.getElementById('settingsRetries').value) || 2;
    const popupDuration = parseInt(document.getElementById('settingsPopupDuration').value) || 3;

    if (!endpoint) { showBriefPopup('AI endpoint URL is required'); return; }
    if (!model) { showBriefPopup('Model name is required'); return; }

    try {
        const resp = await fetch('/config', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ ai_endpoint: endpoint, model: model, external_url: externalUrl, timeout: timeout, max_retries: retries, popup_duration: popupDuration })
        });
        const result = await resp.json();
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
        const data = await resp.json();
        const blob = new Blob([data.logs || 'No logs available'], { type: 'text/plain' });
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
