// ─── Global State ────────────────────────────────────────────
let isConnected = false;
let uploadedFiles = [];
let currentFileIndex = null;
let reviewPages = [];
let reviewCurrentPage = 0;
let reviewOriginalFilename = null;  // original filename with extension (e.g. "doc.xlsx")
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

// ─── Queue State Restoration ─────────────────────────────────

async function loadQueueState() {
    try {
        const resp = await fetch('/queue');
        const data = await resp.json();
        if (!data.files || data.files.length === 0) return;
        uploadedFiles = data.files.map(f => ({
            file_id: f.file_id,
            filename: f.filename,
            status: f.status === 'uploaded' ? 'pending' : f.status,
            num_pages: f.num_pages || 0,
            pages: Array.isArray(f.pages) ? f.pages.length : (f.num_pages || 0),
            uploaded_by: f.uploaded_by || 'unknown',
            progress: f.progress || '',
        }));
        renderFileList();
        updateStepClickability();
    } catch (e) {
        /* queue restoration is best-effort */
    }
}

// ─── Init & Boot ─────────────────────────────────────────────

async function initApp() {
    if (appInitialized) return;
    try { await loadPopupDuration(); } catch (e) { /* offline / not auth */ }
    try { await loadVersion();       } catch (e) { /* offline / not auth */ }
    try { await loadExtractionModeBadge(); } catch (e) { /* ignore */ }
    try { if (window.Suppliers) window.Suppliers.updateReviewBadge(); } catch (e) { /* ignore */ }
    await loadQueueState();
    showUpload();
    appInitialized = true;
}

// Boot sequence is in users.js (last script loaded) to ensure all
// functions from auth.js, nav.js, etc. are defined before checkAuthAndBoot runs.
