// ─── Global State ────────────────────────────────────────────
let isConnected = false;
let uploadedFiles = [];
let currentFileIndex = -1;
let reviewPages = [];
let reviewCurrentPage = 0;
let extractedData = null;
let processing = false;
let abortController = null;
let currentFilePercent = 0;        // 0-100, current file's per-file bar value (drives overall bar)
let pendingNavAction = null;       // function reference, set when nav is intercepted and user confirms
let currentUser = null;        // {id, username, role, must_change_password}
let appInitialized = false;
let suppressAuthRedirect = false; // set during /auth/* calls to avoid loops
let popupDurationSec = 3;

// ─── Shared Utilities ────────────────────────────────────────

async function loadPopupDuration() {
    try {
        const resp = await fetch('/config');
        const cfg = await resp.json();
        popupDurationSec = cfg.popup_duration || 3;
    } catch (e) { popupDurationSec = 3; }
}

async function loadVersion() {
    try {
        const resp = await fetch('/version');
        const data = await resp.json();
        const label = document.getElementById('versionLabel');
        if (label && data.version) {
            label.textContent = `v${data.version} (${data.commit})`;
        }
    } catch (e) { /* keep default */ }
}

// ─── Init & Boot ─────────────────────────────────────────────

async function initApp() {
    if (appInitialized) return;
    try { await loadPopupDuration(); } catch (e) { /* offline / not auth */ }
    try { await loadVersion();       } catch (e) { /* offline / not auth */ }
    // Default landing: Search tab for everyone. Master can use ?debug=1 to deep-link to Debug.
    if (window.__openDebugOnBoot && isMaster && isMaster()) {
        // Phase 2 of v0.037.0: ?debug=1 deep link, master only.
        showDebug();
    } else {
        showSearch();
    }
    appInitialized = true;
}

// Boot sequence is in users.js (last script loaded) to ensure all
// functions from auth.js, nav.js, etc. are defined before checkAuthAndBoot runs.
