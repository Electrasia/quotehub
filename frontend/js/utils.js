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
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay active';
    overlay.innerHTML = `
        <div class="modal" style="max-width:350px">
            <p style="font-size:15px;margin:0">${message}</p>
        </div>
    `;
    document.body.appendChild(overlay);
    setTimeout(() => {
        overlay.classList.remove('active');
        setTimeout(() => overlay.remove(), 300);
    }, popupDurationSec * 1000);
}

/**
 * Show a confirmation dialog with a callback.
 *
 * @param {string} message - The confirmation message
 * @param {Function} onConfirm - Callback when user confirms
 */
function showConfirmPopup(message, onConfirm) {
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay active';
    overlay.innerHTML = `
        <div class="modal" style="max-width:400px">
            <p style="font-size:15px;margin:0 0 16px 0">${message}</p>
            <div class="actions" style="justify-content:center">
                <button class="btn btn-danger btn-sm" id="confirmYes">Yes, Delete</button>
                <button class="btn btn-secondary btn-sm" id="confirmNo">Cancel</button>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);
    overlay.querySelector('#confirmYes').onclick = () => {
        overlay.remove();
        onConfirm();
    };
    overlay.querySelector('#confirmNo').onclick = () => {
        overlay.remove();
    };
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
