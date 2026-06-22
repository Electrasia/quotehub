// ─── Users Management (master only) ─────────────────────────
let usersCache = [];
let editingUserId = null;

function showUsersError(msg) {
    const e = document.getElementById('usersError');
    e.textContent = msg;
    e.classList.remove('hidden');
}
function clearUsersError() {
    document.getElementById('usersError').classList.add('hidden');
}

async function openUsersModal() {
    if (!isMaster()) return;
    clearUsersError();
    hideAddUserForm();
    hideEditUserForm();
    document.getElementById('usersModal').classList.add('active');
}

async function loadUsersTable() {
    try {
        const r = await apiFetch('/users');
        if (!r.ok) throw new Error('Failed to load users');
        usersCache = await r.json();
        renderUsersTable();
    } catch (e) {
        showUsersError('Failed to load users: ' + e.message);
    }
}

function renderUsersTable() {
    const totalMasterCount = usersCache.filter(u => u.role === 'master').length;
    const container = document.getElementById('usersTable');
    if (usersCache.length === 0) {
        container.innerHTML = '<div style="padding:20px;text-align:center;color:#888">No users found.</div>';
        return;
    }
    const esc = (s) => String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    const rows = usersCache.map(u => {
        const isSelf = currentUser && u.id === currentUser.id;
        // Hard delete (the only delete option now) is blocked if the target
        // is the last master of any status. Self-delete is always blocked.
        const isLastMaster = u.role === 'master' && totalMasterCount <= 1;
        const canDelete = !isSelf && !isLastMaster;
        let deleteTitle = 'Permanently delete user';
        if (isSelf) deleteTitle = 'Cannot delete yourself';
        else if (isLastMaster) deleteTitle = 'Cannot delete the last master';
        const status = u.active
            ? '<span style="color:#27ae60">● active</span>'
            : '<span style="color:#999">○ inactive</span>';
        const lastLogin = u.last_login
            ? new Date(u.last_login + (u.last_login.endsWith('Z') ? '' : 'Z')).toLocaleString()
            : '—';
        const created = u.created_at
            ? new Date(u.created_at + (u.created_at.endsWith('Z') ? '' : 'Z')).toLocaleString()
            : '—';
        const roleClass = `role-tag ${u.role}`;
        return `<tr>
            <td>${u.id}</td>
            <td>${esc(u.username)}</td>
            <td><span class="${roleClass}">${esc(u.role)}</span></td>
            <td>${status}</td>
            <td style="font-size:12px">${esc(lastLogin)}</td>
            <td style="font-size:12px">${esc(created)}</td>
            <td>
                <button class="btn btn-sm btn-primary" onclick="showEditUserForm(${u.id})">Edit</button>
                <button class="btn btn-sm btn-danger" ${canDelete ? '' : 'disabled title="' + deleteTitle + '"'} onclick="confirmDeleteUser(${u.id}, '${esc(u.username).replace(/'/g, "\\'")}')">Delete</button>
            </td>
        </tr>`;
    }).join('');
    container.innerHTML = `
        <table style="width:100%;border-collapse:collapse;font-size:13px">
            <thead>
                <tr style="background:#f8f9fa;text-align:left">
                    <th style="padding:8px">ID</th>
                    <th style="padding:8px">Username</th>
                    <th style="padding:8px">Role</th>
                    <th style="padding:8px">Status</th>
                    <th style="padding:8px">Last Login</th>
                    <th style="padding:8px">Created</th>
                    <th style="padding:8px">Actions</th>
                </tr>
            </thead>
            <tbody>${rows}</tbody>
        </table>`;
}

function showAddUserForm() {
    clearUsersError();
    hideEditUserForm();
    hideHardDeleteForm();
    document.getElementById('usersModal').classList.add('active');
    document.getElementById('addUserForm').classList.remove('hidden');
    document.getElementById('newUsername').value = '';
    document.getElementById('newPassword').value = '';
    document.getElementById('newRole').value = 'admin';
    document.getElementById('newUsername').focus();
}
function hideAddUserForm() {
    document.getElementById('addUserForm').classList.add('hidden');
}

function showEditUserForm(userId) {
    clearUsersError();
    hideAddUserForm();
    hideHardDeleteForm();
    const u = usersCache.find(x => x.id === userId);
    if (!u) return;
    editingUserId = userId;
    document.getElementById('usersModal').classList.add('active');
    document.getElementById('editUserTitle').textContent = `Edit user: ${u.username}`;
    document.getElementById('editRole').value = u.role;
    document.getElementById('editPassword').value = '';
    document.getElementById('editActive').checked = !!u.active;
    document.getElementById('editUserForm').classList.remove('hidden');
}
function hideEditUserForm() {
    document.getElementById('editUserForm').classList.add('hidden');
    editingUserId = null;
}

async function submitAddUser() {
    const username = document.getElementById('newUsername').value.trim();
    const password = document.getElementById('newPassword').value;
    const role = document.getElementById('newRole').value;
    if (!username) { showUsersError('Username is required'); return; }
    if (password.length < 12) { showUsersError('Password must be at least 12 characters'); return; }
    const pwErr = validateExportPassword(password);
    if (pwErr) { showUsersError(pwErr); return; }
    try {
        const r = await apiFetch('/users', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password, role }),
        });
        if (!r.ok) {
            const err = await r.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${r.status}`);
        }
        hideAddUserForm();
        closeModal('usersModal');
        await loadUsersTable();
        showBriefPopup('Saved');
    } catch (e) {
        showUsersError(e.message);
    }
}

async function submitEditUser() {
    if (!editingUserId) return;
    const role = document.getElementById('editRole').value;
    const newPassword = document.getElementById('editPassword').value;
    const active = document.getElementById('editActive').checked;
    if (newPassword) {
        if (newPassword.length < 12) {
            showUsersError('New password must be at least 12 characters');
            return;
        }
        const pwErr = validateExportPassword(newPassword);
        if (pwErr) { showUsersError(pwErr); return; }
    }
    try {
        const body = { role, active };
        if (newPassword) body.new_password = newPassword;
        const r = await apiFetch(`/users/${editingUserId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!r.ok) {
            const err = await r.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${r.status}`);
        }
        hideEditUserForm();
        closeModal('usersModal');
        await loadUsersTable();
        showBriefPopup('Saved');
    } catch (e) {
        showUsersError(e.message);
    }
}

// ─── Password strength helpers ──────────────────────────

function _updatePasswordStrength(inputId, barId, fillId, labelId) {
    const pw = document.getElementById(inputId).value;
    const barEl = document.getElementById(barId);
    const fillEl = document.getElementById(fillId);
    const labelEl = document.getElementById(labelId);
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
}

function updateNewUserPassword() {
    _updatePasswordStrength('newPassword', 'newPasswordStrengthBar', 'newPasswordStrengthFill', 'newPasswordStrengthLabel');
}

function updateEditUserPassword() {
    _updatePasswordStrength('editPassword', 'editPasswordStrengthBar', 'editPasswordStrengthFill', 'editPasswordStrengthLabel');
}

// Hard delete state + functions (master only, irreversible)
let hardDeletingUserId = null;

function showHardDeleteForm(userId, username) {
    clearUsersError();
    hideAddUserForm();
    hideEditUserForm();
    const u = usersCache.find(x => x.id === userId);
    if (!u) return;
    hardDeletingUserId = userId;
    document.getElementById('usersModal').classList.add('active');
    document.getElementById('hardDeleteTarget').textContent = username;
    document.getElementById('hardDeleteConfirm').value = '';
    document.getElementById('hardDeleteBtn').disabled = true;
    document.getElementById('hardDeleteForm').classList.remove('hidden');
    // Live-validate the confirm input
    const inp = document.getElementById('hardDeleteConfirm');
    inp.oninput = () => {
        document.getElementById('hardDeleteBtn').disabled =
            inp.value.trim() !== username;
    };
    setTimeout(() => inp.focus(), 50);
}
function hideHardDeleteForm() {
    document.getElementById('hardDeleteForm').classList.add('hidden');
    hardDeletingUserId = null;
}

async function submitHardDelete() {
    if (!hardDeletingUserId) return;
    const btn = document.getElementById('hardDeleteBtn');
    btn.disabled = true;
    btn.textContent = 'Deleting…';
    try {
        const r = await apiFetch(`/users/${hardDeletingUserId}?hard=true`, { method: 'DELETE' });
        if (!r.ok) {
            const err = await r.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${r.status}`);
        }
        hideHardDeleteForm();
        closeModal('usersModal');
        await loadUsersTable();
    } catch (e) {
        showUsersError(e.message);
    } finally {
        btn.disabled = false;
        btn.textContent = 'Delete Permanently';
    }
}

async function confirmDeleteUser(userId, username) {
    clearUsersError();
    // Use the dedicated hard-delete form (with type-to-confirm) instead of a popup
    showHardDeleteForm(userId, username);
}

// ─── Boot (runs last, after all scripts loaded) ─────────────
async function checkAuthAndBoot() {
    try {
        suppressAuthRedirect = true;
        const r = await _nativeFetch('/auth/me', { credentials: 'include' });
        if (r.ok) {
            const data = await r.json();
            currentUser = data;
            renderUserBadge();
            if (data.must_change_password) {
                showChangePassword();
            } else {
                await initApp();
            }
        } else {
            showLogin();
        }
    } catch (e) {
        showLogin();
    } finally {
        suppressAuthRedirect = false;
    }
}

// Boot once the DOM is ready.
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', checkAuthAndBoot);
} else {
    checkAuthAndBoot();
}
