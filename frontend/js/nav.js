// ─── Navigation ──────────────────────────────────────────────
let currentStep = 1;

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
    document.getElementById('navProcess').classList.add('active');
    document.getElementById('navSearch').classList.remove('active');
    document.getElementById('navSettings').classList.remove('active');
}

function showUpload() {
    showProcessView();
    goToStep(1);
}

function showSearch() {
    document.getElementById('processView').classList.add('hidden');
    document.getElementById('searchView').classList.remove('hidden');
    document.getElementById('settingsView').classList.add('hidden');
    document.getElementById('navProcess').classList.remove('active');
    document.getElementById('navSearch').classList.add('active');
    document.getElementById('navSettings').classList.remove('active');
    // Load all results
    document.getElementById('searchInput').value = '';
    searchQuotations();
}

async function showSettings() {
    document.getElementById('processView').classList.add('hidden');
    document.getElementById('searchView').classList.add('hidden');
    document.getElementById('settingsView').classList.remove('hidden');
    document.getElementById('navProcess').classList.remove('active');
    document.getElementById('navSearch').classList.remove('active');
    document.getElementById('navSettings').classList.add('active');
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
        // Session settings — numeric safety via Number.isFinite (allows 0, rejects NaN)
        const days = Number.isFinite(cfg.session_max_age) ? Math.round(cfg.session_max_age / 86400) : 14;
        document.getElementById('settingsSessionMaxAgeDays').value = days;
        document.getElementById('settingsIdleTimeout').value = Number.isFinite(cfg.idle_timeout_minutes) ? cfg.idle_timeout_minutes : 60;
        updateIdleTimeoutFromConfig(cfg);
        applyAdminSettingsLock();
    } catch (e) { /* ignore */ }
    // Load users table if master
    if (isMaster()) {
        await loadUsersTable();
    }
}
