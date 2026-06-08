// ─── Auth ────────────────────────────────────────────────────
// Wrap window.fetch so every request sends cookies and a 401
// (other than /auth/* and /version) re-shows the login overlay.
const _nativeFetch = window.fetch.bind(window);
window.fetch = async function(input, init) {
    init = init || {};
    init.credentials = 'include';
    const url = typeof input === 'string' ? input : input.url;
    const resp = await _nativeFetch(input, init);
    if (resp.status === 401
        && !suppressAuthRedirect
        && !url.includes('/auth/')
        && !url.includes('/version')) {
        showLogin();
    }
    return resp;
};

async function apiFetch(url, opts = {}) {
    opts.credentials = 'include';
    const r = await _nativeFetch(url, opts);
    if (r.status === 401) {
        const data = await r.clone().json().catch(() => ({}));
        if (data.detail && data.detail !== 'Not authenticated') {
            throw new Error(data.detail);
        }
        throw new Error('Not authenticated');
    }
    return r;
}

function showLogin() {
    document.getElementById('loginOverlay').classList.remove('hidden');
    document.getElementById('userBadge').classList.add('hidden');
    currentUser = null;
    setTimeout(() => {
        const u = document.getElementById('loginUsername');
        if (u) u.focus();
    }, 50);
}

function hideLogin() {
    document.getElementById('loginOverlay').classList.add('hidden');
    document.getElementById('loginError').classList.add('hidden');
}

function showLoginError(msg) {
    const e = document.getElementById('loginError');
    e.textContent = msg;
    e.classList.remove('hidden');
}

function showChangePassword(subtitle) {
    document.getElementById('changePasswordSubtitle').textContent = subtitle || 'You must change your password before continuing.';
    document.getElementById('changePasswordModal').classList.remove('hidden');
    setTimeout(() => document.getElementById('cpOld').focus(), 50);
}

function hideChangePassword() {
    document.getElementById('changePasswordModal').classList.add('hidden');
    document.getElementById('changePasswordError').classList.add('hidden');
}

function showChangePasswordError(msg) {
    const e = document.getElementById('changePasswordError');
    e.textContent = msg;
    e.classList.remove('hidden');
}

function renderUserBadge() {
    if (!currentUser) {
        document.getElementById('userBadge').classList.add('hidden');
        return;
    }
    const tag = document.getElementById('userRoleTag');
    tag.textContent = currentUser.role;
    tag.className = 'role-tag ' + currentUser.role;
    document.getElementById('userName').textContent = currentUser.username;
    document.getElementById('userBadge').classList.remove('hidden');
    applyRoleClass();
}

function applyRoleClass() {
    // Remove all role classes first, then add the current one.
    document.body.classList.remove('role-master', 'role-admin', 'role-user');
    if (currentUser) {
        document.body.classList.add('role-' + currentUser.role);
    }
    // Re-apply admin restrictions to the Settings modal (in case it's open)
    applyAdminSettingsLock();
}

function applyAdminSettingsLock() {
    const isAdmin = currentUser && currentUser.role === 'admin';
    const inputs = [
        'settingsEndpoint', 'settingsModel', 'settingsExternalUrl',
        'settingsTimeout', 'settingsRetries', 'settingsPopupDuration',
        'settingsLlmFallbackEnabled',
        'settingsOcrEnabled', 'settingsOcrLlmFallback',
        'settingsSessionMaxAgeDays', 'settingsIdleTimeout',
    ];
    inputs.forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        el.disabled = isAdmin;
        if (isAdmin) {
            el.title = 'Only Master can change AI settings';
        } else {
            el.removeAttribute('title');
        }
    });
    const saveBtn = document.getElementById('saveSettingsBtn');
    if (saveBtn) {
        saveBtn.disabled = isAdmin;
        if (isAdmin) saveBtn.title = 'Only Master can change AI settings';
        else saveBtn.removeAttribute('title');
    }
    const sessionSaveBtn = document.getElementById('saveSessionSettingsBtn');
    if (sessionSaveBtn) {
        sessionSaveBtn.disabled = isAdmin;
        if (isAdmin) sessionSaveBtn.title = 'Only Master can change Session settings';
        else sessionSaveBtn.removeAttribute('title');
    }
}

async function doLogin() {
    const u = document.getElementById('loginUsername').value.trim();
    const p = document.getElementById('loginPassword').value;
    if (!u || !p) return;
    const btn = document.getElementById('loginSubmitBtn');
    btn.disabled = true;
    btn.textContent = 'Signing in…';
    try {
        suppressAuthRedirect = true;
        const r = await _nativeFetch('/auth/login', {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username: u, password: p }),
        });
        if (r.ok) {
            const data = await r.json();
            currentUser = data;
            renderUserBadge();
            hideLogin();
            document.getElementById('loginPassword').value = '';
            if (data.must_change_password) {
                showChangePassword();
            } else if (!appInitialized) {
                await initApp();
            }
        } else if (r.status === 401) {
            showLoginError('Invalid username or password');
        } else {
            const err = await r.json().catch(() => ({}));
            showLoginError(err.detail || 'Login failed');
        }
    } catch (e) {
        showLoginError('Login failed: ' + e.message);
    } finally {
        suppressAuthRedirect = false;
        btn.disabled = false;
        btn.textContent = 'Sign in';
    }
}

async function doChangePassword() {
    const oldP = document.getElementById('cpOld').value;
    const newP = document.getElementById('cpNew').value;
    const conf = document.getElementById('cpConfirm').value;
    if (newP !== conf) {
        showChangePasswordError('New passwords do not match');
        return;
    }
    if (newP.length < 6) {
        showChangePasswordError('New password must be at least 6 characters');
        return;
    }
    const btn = document.getElementById('changePasswordBtn');
    btn.disabled = true;
    btn.textContent = 'Changing…';
    try {
        suppressAuthRedirect = true;
        const r = await _nativeFetch('/auth/change-password', {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ old_password: oldP, new_password: newP }),
        });
        if (r.ok) {
            hideChangePassword();
            document.getElementById('cpOld').value = '';
            document.getElementById('cpNew').value = '';
            document.getElementById('cpConfirm').value = '';
            if (currentUser) currentUser.must_change_password = false;
            if (!appInitialized) {
                await initApp();
            }
        } else {
            const err = await r.json().catch(() => ({}));
            showChangePasswordError(err.detail || 'Password change failed');
        }
    } catch (e) {
        showChangePasswordError('Change failed: ' + e.message);
    } finally {
        suppressAuthRedirect = false;
        btn.disabled = false;
        btn.textContent = 'Change Password';
    }
}

async function doLogout() {
    try {
        suppressAuthRedirect = true;
        await _nativeFetch('/auth/logout', { method: 'POST', credentials: 'include' });
    } catch (e) { /* ignore */ }
    suppressAuthRedirect = false;
    currentUser = null;
    appInitialized = false;
    renderUserBadge();
    // Reset the app: hide everything, show login
    document.getElementById('processView').classList.add('hidden');
    document.getElementById('searchView').classList.add('hidden');
    document.getElementById('settingsView').classList.add('hidden');
    document.getElementById('fileListSection').classList.add('hidden');
    uploadedFiles = [];
    currentFileIndex = null;
    isConnected = false;
    updateConnectionUI();
    goToStep(1);
    showLogin();
}

// Returns true if the current role is allowed to use admin/master
// features (uploading, processing, deleting, etc.).
function canModify() {
    return currentUser && (currentUser.role === 'admin' || currentUser.role === 'master');
}
function isMaster() {
    return currentUser && currentUser.role === 'master';
}
