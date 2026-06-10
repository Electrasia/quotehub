// ─── Navigation ──────────────────────────────────────────────
let currentStep = 1;

function updateExtractionModeBadge(mode) {
    const badge = document.getElementById('extractionModeBadge');
    const icon = document.getElementById('extractionModeIcon');
    const text = document.getElementById('extractionModeText');
    if (!badge || !icon || !text) return;
    
    // Remove all mode classes
    badge.className = 'extraction-mode-badge';
    
    const modes = {
        'llm_first':    { icon: '🤖', text: 'LLM First (AI)', cls: '' },
        'local_first':  { icon: '⚡', text: 'Local First', cls: 'mode-local_first' },
        'llm_only':     { icon: '🤖', text: 'LLM Only', cls: 'mode-llm_only' },
        'local_only':   { icon: '⚡', text: 'Local Only', cls: 'mode-local_only' }
    };
    
    const m = modes[mode] || modes['llm_first'];
    icon.textContent = m.icon;
    text.textContent = m.text;
    if (m.cls) badge.classList.add(m.cls);
}

async function loadExtractionModeBadge() {
    try {
        const resp = await fetch('/config');
        const cfg = await resp.json();
        updateExtractionModeBadge(cfg.extraction_mode || 'llm_first');
    } catch (e) { /* ignore */ }
}

function goToStep(step) {
    // Prevent navigating to step 2 (queue) when there are no files
    if (step === 2 && uploadedFiles.length === 0) {
        step = 1;
    }
    currentStep = step;
    // Update step indicators
    for (let i = 1; i <= 4; i++) {
        const stepEl = document.getElementById('step' + i);
        stepEl.classList.remove('active', 'completed');
        if (i < step) stepEl.classList.add('completed');
        else if (i === step) stepEl.classList.add('active');
        // Update connector
        if (i < 4) {
            const connector = document.getElementById('connector' + i);
            if (i < step) connector.classList.add('completed');
            else connector.classList.remove('completed');
        }
    }
    // Update step circles for completed steps (show checkmark)
    for (let i = 1; i <= 4; i++) {
        const circle = document.querySelector('#step' + i + ' .step-circle');
        if (i < step) circle.textContent = '\u2713';
        else circle.textContent = i;
    }
    // Show/hide step panels
    for (let i = 1; i <= 4; i++) {
        const panel = document.getElementById('stepPanel' + i);
        if (i === step) panel.classList.remove('hidden');
        else panel.classList.add('hidden');
    }
}

function showProcessView() {
    document.getElementById('processView').classList.remove('hidden');
    document.getElementById('searchView').classList.add('hidden');
    document.getElementById('settingsView').classList.add('hidden');
    document.getElementById('helpView').classList.add('hidden');
    document.getElementById('navProcess').classList.add('active');
    document.getElementById('navSearch').classList.remove('active');
    document.getElementById('navSettings').classList.remove('active');
    document.getElementById('navHelp').classList.remove('active');
}

// ─── Nav guard (v0.038.0): intercept leaving Process page ────
// Returns true if the user is currently on the Process view AND there is
// work in progress (active SSE stream, or pending files in the queue) that
// would be lost by navigating away.
function isOnProcessView() {
    const processView = document.getElementById('processView');
    return processView && !processView.classList.contains('hidden');
}

function hasProcessWorkInProgress() {
    if (processing) return true;                       // active stream
    if (isOnProcessView() && uploadedFiles.some(f => f.status === 'pending')) return true; // queue not drained
    return false;
}

function showConfirmNavDialog() {
    const titleEl   = document.getElementById('confirmNavTitle');
    const messageEl = document.getElementById('confirmNavMessage');
    const stopBtn   = document.getElementById('confirmNavStopBtn');
    if (processing) {
        const n = uploadedFiles.filter(f => f.status === 'processing').length;
        titleEl.textContent = '⚠ Files are still processing';
        messageEl.innerHTML = `<strong>${n}</strong> file${n > 1 ? 's are' : ' is'} still being processed.<br>Stop processing and leave this page?`;
        stopBtn.textContent = 'Stop & Leave';
        stopBtn.classList.remove('btn-secondary');
        stopBtn.classList.add('btn-danger');
    } else {
        const n = uploadedFiles.filter(f => f.status === 'pending').length;
        titleEl.textContent = '⚠ Files are waiting to be processed';
        messageEl.innerHTML = `<strong>${n}</strong> file${n > 1 ? 's are' : ' is'} waiting in the queue.<br>Leave this page? Files will stay in the queue for later processing.`;
        stopBtn.textContent = 'Leave';
        stopBtn.classList.remove('btn-danger');
        stopBtn.classList.add('btn-secondary');
    }
    document.getElementById('confirmNavModal').classList.add('active');
}

function cancelNavConfirm() {
    document.getElementById('confirmNavModal').classList.remove('active');
    pendingNavAction = null;
}

function confirmNavStop() {
    // Stop active stream if any
    if (processing && typeof cancelProcessing === 'function') {
        cancelProcessing();
    }
    document.getElementById('confirmNavModal').classList.remove('active');
    const action = pendingNavAction;
    pendingNavAction = null;
    if (action) action();
}

function showUpload() {
    showProcessView();
    goToStep(1);
}

// Function declarations (not const) so they attach to window and remain
// callable from inline onclick handlers in index.html.
function showSearch() {
    if (isOnProcessView() && hasProcessWorkInProgress()) {
        pendingNavAction = _doShowSearch;
        showConfirmNavDialog();
        return;
    }
    _doShowSearch();
}
function _doShowSearch() {
    document.getElementById('processView').classList.add('hidden');
    document.getElementById('searchView').classList.remove('hidden');
    document.getElementById('settingsView').classList.add('hidden');
    document.getElementById('helpView').classList.add('hidden');
    document.getElementById('navProcess').classList.remove('active');
    document.getElementById('navSearch').classList.add('active');
    document.getElementById('navSettings').classList.remove('active');
    document.getElementById('navHelp').classList.remove('active');
    // Load all results
    document.getElementById('searchInput').value = '';
    searchQuotations();
}

function showSettings() {
    if (isOnProcessView() && hasProcessWorkInProgress()) {
        pendingNavAction = _doShowSettings;
        showConfirmNavDialog();
        return;
    }
    _doShowSettings();
}
async function _doShowSettings() {
    document.getElementById('processView').classList.add('hidden');
    document.getElementById('searchView').classList.add('hidden');
    document.getElementById('settingsView').classList.remove('hidden');
    document.getElementById('helpView').classList.add('hidden');
    document.getElementById('navProcess').classList.remove('active');
    document.getElementById('navSearch').classList.remove('active');
    document.getElementById('navSettings').classList.add('active');
    document.getElementById('navHelp').classList.remove('active');
    // Load settings data into the page
    try {
        const resp = await fetch('/config');
        const cfg = await resp.json();
        document.getElementById('settingsEndpoint').value = cfg.ai_endpoint || '';
        document.getElementById('settingsModel').value = cfg.model || '';
        document.getElementById('settingsExternalUrl').value = cfg.external_url || '';
        document.getElementById('settingsTimeout').value = cfg.timeout || 120;
        document.getElementById('settingsRetries').value = cfg.max_retries || 2;
        document.getElementById('settingsPopupDuration').value = cfg.popup_duration || 3;
        document.getElementById('settingsOcrEnabled').checked = cfg.ocr_enabled !== false;
        document.getElementById('settingsOcrLlmFallback').checked = cfg.ocr_fallback_to_llm !== false;
        document.getElementById('settingsExtractionMode').value = cfg.extraction_mode || 'llm_first';
        updateExtractionModeBadge(cfg.extraction_mode || 'llm_first');
        updateIdleTimeoutFromConfig(cfg);
        applyAdminSettingsLock();
    } catch (e) { /* ignore */ }
    // Load users table if master
    if (isMaster()) {
        await loadUsersTable();
        loadCleanupStats(); // Load cleanup stats
    }
}

function showHelp() {
    if (isOnProcessView() && hasProcessWorkInProgress()) {
        pendingNavAction = _doShowHelp;
        showConfirmNavDialog();
        return;
    }
    _doShowHelp();
}
function _doShowHelp() {
    document.getElementById('processView').classList.add('hidden');
    document.getElementById('searchView').classList.add('hidden');
    document.getElementById('settingsView').classList.add('hidden');
    document.getElementById('helpView').classList.remove('hidden');
    document.getElementById('navProcess').classList.remove('active');
    document.getElementById('navSearch').classList.remove('active');
    document.getElementById('navSettings').classList.remove('active');
    document.getElementById('navHelp').classList.add('active');
}
