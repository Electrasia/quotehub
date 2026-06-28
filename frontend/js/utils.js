/**
 * frontend/js/utils.js — Shared utility functions for QuoteHub.
 *
 * This module contains common helper functions used across the frontend.
 * All functions are attached to the window object for global access.
 *
 * Functions:
 *   escapeHtml: Escape HTML special characters to prevent XSS
 *   formatBytes: Format byte count as human-readable string
 *   showBriefPopup: Show a temporary notification popup
 *   showConfirmPopup: Show a confirmation dialog with callback
 *   openModal: Open a modal by ID
 *   closeModal: Close a modal by ID
 */

// ─── Password Helpers ────────────────────────────────────────

const PASSWORD_RULES_HTML = '<div class="pw-rules">Password must be: at least 12 characters, one uppercase, one lowercase, one digit, one special character. Cannot contain the username, common patterns (e.g. \'password\', \'qwerty\'), or sequential characters (e.g. \'1234\' or \'abcd\').</div>';

/**
 * Extract readable error message from API response detail.
 * Handles: string, {errors: [...]}, or fallback.
 */
function extractPasswordError(detail) {
    if (typeof detail === 'string') return detail;
    if (detail && Array.isArray(detail.errors)) return detail.errors.join('. ');
    return 'Validation failed';
}

// ─── HTML Escaping ──────────────────────────────────────────

/**
 * Escape HTML special characters to prevent XSS attacks.
 *
 * @param {*} s - The value to escape
 * @returns {string} The escaped string
 *
 * @example
 * escapeHtml('<script>alert("xss")</script>')
 * // Returns: '&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;'
 */
function escapeHtml(s) {
    if (s == null) return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// ─── Formatting ─────────────────────────────────────────────

/**
 * Format a byte count as a human-readable string.
 *
 * @param {number} bytes - The number of bytes
 * @returns {string} Formatted string (e.g., "1.5 MB")
 *
 * @example
 * formatBytes(1536)  // Returns: "1.5 KB"
 * formatBytes(1048576)  // Returns: "1 MB"
 */
function formatBytes(bytes) {
    if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
    const k = 1024;
    const units = ['B', 'KB', 'MB', 'GB'];
    const i = Math.min(Math.floor(Math.log(bytes) / Math.log(k)), units.length - 1);
    return Math.round((bytes / Math.pow(k, i)) * 100) / 100 + ' ' + units[i];
}

// ─── Popups ─────────────────────────────────────────────────

/**
 * Show a temporary notification popup.
 *
 * @param {string} message - The message to display
 */
function showBriefPopup(message) {
    console.log(`[popup] ${message}`);
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay active';
    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.style.maxWidth = '350px';
    const p = document.createElement('p');
    p.style.fontSize = '15px';
    p.style.margin = '0';
    p.textContent = message;
    modal.appendChild(p);
    overlay.appendChild(modal);
    document.body.appendChild(overlay);
    setTimeout(() => {
        overlay.classList.remove('active');
        setTimeout(() => overlay.remove(), 300);
    }, (popupDurationSec || 3) * 1000);
}

/**
 * Show a confirmation dialog with a callback.
 *
 * @param {string|object} messageOrOptions - Message string (legacy) or options object
 * @param {Function} [onConfirm] - Callback when user confirms (legacy form)
 * @returns {Promise<boolean>} Resolves true on confirm, false on cancel
 */
function showConfirmPopup(messageOrOptions, onConfirm) {
    let opts;
    if (typeof messageOrOptions === 'string') {
        opts = {
            message: messageOrOptions,
            confirmText: 'Yes, Delete',
            cancelText: 'Cancel',
            danger: true,
            onConfirm: onConfirm || null,
            onCancel: null,
        };
    } else {
        opts = Object.assign({
            confirmText: 'Confirm',
            cancelText: 'Cancel',
            danger: false,
            onConfirm: null,
            onCancel: null,
        }, messageOrOptions);
    }

    return new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay active';

        const modal = document.createElement('div');
        modal.className = 'modal' + (opts.danger ? ' modal-content--danger' : '');
        modal.style.maxWidth = '400px';

        const p = document.createElement('p');
        p.style.fontSize = '15px';
        p.style.margin = '0 0 16px 0';
        p.textContent = opts.message;
        modal.appendChild(p);

        const actions = document.createElement('div');
        actions.className = 'actions';
        actions.style.justifyContent = 'center';

        const btnYes = document.createElement('button');
        btnYes.className = 'btn btn-sm ' + (opts.danger ? 'btn-danger' : 'btn-primary');
        btnYes.textContent = opts.confirmText;

        const btnNo = document.createElement('button');
        btnNo.className = 'btn btn-secondary btn-sm';
        btnNo.textContent = opts.cancelText;

        actions.appendChild(btnYes);
        actions.appendChild(btnNo);
        modal.appendChild(actions);
        overlay.appendChild(modal);
        document.body.appendChild(overlay);

        btnYes.onclick = () => {
            overlay.remove();
            if (opts.onConfirm) opts.onConfirm();
            resolve(true);
        };
        btnNo.onclick = () => {
            overlay.remove();
            if (opts.onCancel) opts.onCancel();
            resolve(false);
        };
    });
}

// ─── Prompt & Alert Popups ────────────────────────────────

/**
 * Show a prompt dialog with an input field.
 *
 * @param {object} options - { message, defaultValue?, placeholder?, confirmText?, cancelText? }
 * @returns {Promise<string|null>} Resolves to entered string or null on cancel
 */
function showPromptPopup(options) {
    const opts = Object.assign({
        defaultValue: '',
        placeholder: '',
        confirmText: 'OK',
        cancelText: 'Cancel',
    }, options);

    return new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay active';

        const modal = document.createElement('div');
        modal.className = 'modal';
        modal.style.maxWidth = '400px';

        const p = document.createElement('p');
        p.style.fontSize = '15px';
        p.style.margin = '0 0 12px 0';
        p.textContent = opts.message;
        modal.appendChild(p);

        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'modal-prompt-input';
        input.value = opts.defaultValue;
        input.placeholder = opts.placeholder;
        input.style.width = '100%';
        input.style.padding = '8px 12px';
        input.style.border = '1px solid #ddd';
        input.style.borderRadius = '6px';
        input.style.fontSize = '14px';
        input.style.marginBottom = '16px';
        input.style.boxSizing = 'border-box';
        modal.appendChild(input);

        const actions = document.createElement('div');
        actions.className = 'actions';
        actions.style.justifyContent = 'center';

        const btnOk = document.createElement('button');
        btnOk.className = 'btn btn-primary btn-sm';
        btnOk.textContent = opts.confirmText;

        const btnCancel = document.createElement('button');
        btnCancel.className = 'btn btn-secondary btn-sm';
        btnCancel.textContent = opts.cancelText;

        actions.appendChild(btnOk);
        actions.appendChild(btnCancel);
        modal.appendChild(actions);
        overlay.appendChild(modal);
        document.body.appendChild(overlay);

        input.focus();

        const submit = () => {
            overlay.remove();
            resolve(input.value || '');
        };
        const cancel = () => {
            overlay.remove();
            resolve(null);
        };

        btnOk.onclick = submit;
        btnCancel.onclick = cancel;
        input.onkeydown = (e) => {
            if (e.key === 'Enter') submit();
            if (e.key === 'Escape') cancel();
        };
    });
}

/**
 * Show an alert dialog that must be acknowledged.
 *
 * @param {object} options - { message, title?, confirmText? }
 * @returns {Promise<void>} Resolves when user clicks Confirm
 */
function showAlertPopup(options) {
    const opts = Object.assign({
        title: '',
        confirmText: 'OK',
    }, options);

    return new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay active';

        const modal = document.createElement('div');
        modal.className = 'modal';
        modal.style.maxWidth = '400px';

        if (opts.title) {
            const h = document.createElement('h4');
            h.style.margin = '0 0 8px 0';
            h.textContent = opts.title;
            modal.appendChild(h);
        }

        const p = document.createElement('p');
        p.style.fontSize = '15px';
        p.style.margin = '0 0 16px 0';
        p.textContent = opts.message;
        modal.appendChild(p);

        const actions = document.createElement('div');
        actions.className = 'actions';
        actions.style.justifyContent = 'center';

        const btnOk = document.createElement('button');
        btnOk.className = 'btn btn-primary btn-sm';
        btnOk.textContent = opts.confirmText;

        actions.appendChild(btnOk);
        modal.appendChild(actions);
        overlay.appendChild(modal);
        document.body.appendChild(overlay);

        btnOk.onclick = () => {
            overlay.remove();
            resolve();
        };
    });
}

// ─── Modal Management ───────────────────────────────────────

/**
 * Close a modal by its ID.
 *
 * @param {string} id - The modal element ID
 */
function closeModal(id) {
    document.getElementById(id).classList.remove('active');
}

/**
 * Open a modal by its ID.
 *
 * @param {string} id - The modal element ID
 */
function openModal(id) {
    const el = document.getElementById(id);
    if (el) el.classList.add('active');
}
