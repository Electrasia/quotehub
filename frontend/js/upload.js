/**
 * frontend/js/upload.js — File upload and queue management.
 *
 * Handles drag-and-drop, file input, upload to backend, queue
 * rendering (file list with status badges, reorder, remove), and
 * step navigation state.
 *
 * Depends on globals from:
 *   app.js   — uploadedFiles
 *   utils.js — escapeHtml, showBriefPopup
 */

/**
 * Add an error message to the visible error banner in the upload area.
 *
 * @param {string} message — The error message to display
 */
function addUploadError(message) {
    const banner = document.getElementById('uploadErrors');
    if (!banner) return;
    const line = document.createElement('div');
    line.style.cssText = 'color:#c0392b;font-size:13px;padding:4px 0;border-bottom:1px solid #fce4e4';
    line.textContent = message;
    banner.appendChild(line);
    banner.classList.remove('hidden');
}

// ─── File Upload ─────────────────────────────────────────────
const uploadArea = document.getElementById('uploadArea');
const fileInput = document.getElementById('fileInput');

uploadArea.addEventListener('dragover', (e) => { e.preventDefault(); uploadArea.classList.add('dragover'); });
uploadArea.addEventListener('dragleave', () => uploadArea.classList.remove('dragover'));
uploadArea.addEventListener('drop', (e) => {
    e.preventDefault();
    uploadArea.classList.remove('dragover');
    handleFiles(e.dataTransfer.files).then(() => {
        if (uploadedFiles.length > 0) goToStep(2);
    });
});
fileInput.addEventListener('change', (e) => {
    handleFiles(e.target.files).then(() => {
        if (uploadedFiles.length > 0) goToStep(2);
    });
});

/**
 * Upload one or more files to the backend.
 *
 * Checks for duplicates and adds each file to the queue.
 *
 * @param {FileList} files — The files to upload
 */
async function handleFiles(files) {
    // Clear previous upload errors
    const errorBanner = document.getElementById('uploadErrors');
    if (errorBanner) errorBanner.innerHTML = '';

    for (const file of files) {
        console.log(`[upload] Processing: ${file.name}, size: ${file.size} bytes`);
        // v0.038.0: check by extension, not MIME type. .xlsx files have
        // MIME type 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        // (or 'application/octet-stream' on some systems), so the old
        // `file.type === 'application/pdf'` check silently dropped them.
        const name = (file.name || '').toLowerCase();
        if (!name.endsWith('.pdf') && !name.endsWith('.xlsx')) {
            console.log(`[upload] SKIPPED (wrong extension): ${file.name}`);
            addUploadError(`${file.name} — Unsupported file type`);
            continue;
        }
        // Reject empty files client-side
        if (file.size === 0) {
            console.log(`[upload] SKIPPED (empty): ${file.name}`);
            addUploadError(`${file.name} — Empty file`);
            continue;
        }
        const formData = new FormData();
        formData.append('files', file);
        console.log(`[upload] Uploading: ${file.name}`);
        const resp = await fetch('/upload', { method: 'POST', body: formData });
        const data = await resp.json();
        console.log(`[upload] Response:`, JSON.stringify({ uploaded: data.uploaded, errors: data.errors, filesCount: data.files?.length }));
        if (!resp.ok) {
            console.log(`[upload] FAILED (HTTP ${resp.status}): ${file.name}`);
            addUploadError(`${file.name} — Upload failed: ${data.error || resp.statusText}`);
            continue;
        }
        // Show backend validation errors (empty files, wrong type)
        if (data.errors && data.errors.length > 0) {
            for (const err of data.errors) {
                console.log(`[upload] REJECTED by backend: ${err.filename} — ${err.error}`);
                addUploadError(`${err.filename} — ${err.error}`);
            }
        }
        if (!data.files || data.files.length === 0) {
            console.log(`[upload] No valid files in response, skipping.`);
            continue;
        }
        // Extract the actual file entry from the backend response envelope
        const fileEntry = data.files ? data.files[0] : data;
        // Normalize status: backend sends "uploaded", frontend expects "pending"
        fileEntry.status = fileEntry.status === 'uploaded' ? 'pending' : fileEntry.status;
        // Normalize page count: backend sends num_pages, frontend expects pages
        fileEntry.pages = fileEntry.num_pages || fileEntry.pages || 0;
        // Store stable file_id (replaces fragile backendIndex)
        // fileEntry.file_id is already set by the backend
        // Check for duplicates
        try {
            const dupResp = await fetch(`/check-duplicate?filename=${encodeURIComponent(file.name)}`);
            const dup = await dupResp.json();
            if (dup.exists || dup.in_database) {
                fileEntry.duplicate = true;
            }
        } catch (e) { /* ignore check errors */ }
        uploadedFiles.push(fileEntry);
        console.log(`[upload] Added to queue: ${file.name} (${fileEntry.pages} pages)`);
        renderFileList();
    }
    updateStepClickability();
}

// ─── Queue rendering ─────────────────────────────────────────

/**
 * Render the file queue list with status badges and action buttons.
 *
 * Updates both the upload queue view and the processing view.
 */
function renderFileList() {
    const section = document.getElementById('fileListSection');
    const list = document.getElementById('fileList');
    if (uploadedFiles.length === 0) {
        section.classList.add('hidden');
        // Also clear processing view's file list
        const procSection = document.getElementById('processingFileListSection');
        if (procSection) {
            procSection.classList.add('hidden');
        }
        return;
    }
    section.classList.remove('hidden');
    const pendingCount = uploadedFiles.filter(f => f.status === 'pending').length;
    const doneCount = uploadedFiles.filter(f => f.status === 'done' || f.status === 'saved').length;
    const html = uploadedFiles.map((f, i) => {
        let statusHtml = '';
        if (f.status === 'pending') statusHtml = '<span class="file-status pending">Pending</span>';
        else if (f.status === 'processing') statusHtml = `<span class="file-status processing">${f.progress || 'Processing...'}</span>`;
        else if (f.status === 'done') statusHtml = '<span class="file-status done">✓ Ready to review</span>';
        else if (f.status === 'saved') statusHtml = '<span class="file-status done">✓ Saved</span>';
        else if (f.status === 'error') statusHtml = '<span class="file-status error">✗ Error</span>';
        else if (f.status === 'skipped') statusHtml = '<span class="file-status" style="color:#999">Skipped</span>';
        const dupBadge = f.duplicate ? '<span class="file-status" style="background:#fef9e7;color:#e67e22;margin-right:6px">⚠ Duplicate</span>' : '';
        const canMove = f.status === 'pending';
        const moveHtml = canMove ? `
            <span style="display:inline-flex;gap:2px;margin-right:8px">
                <button class="btn btn-sm btn-secondary" onclick="moveFile(${i}, -1)" ${i === 0 ? 'disabled' : ''} title="Move up" style="padding:2px 6px;font-size:11px">▲</button>
                <button class="btn btn-sm btn-secondary" onclick="moveFile(${i}, 1)" ${i === uploadedFiles.length - 1 ? 'disabled' : ''} title="Move down" style="padding:2px 6px;font-size:11px">▼</button>
            </span>
        ` : '';
        const canRemove = f.status === 'pending' || f.status === 'error' || f.status === 'skipped';
        const removeHtml = canRemove
            ? `<button class="btn btn-sm btn-danger" onclick="removeFile(${i})" title="Remove" style="padding:2px 6px;font-size:11px">✕</button>`
            : '';
        return `
            <div class="file-item">
                <span class="file-name">${moveHtml}${dupBadge}${escapeHtml(f.filename)} (${escapeHtml(String(f.pages))} page${f.pages !== 1 ? 's' : ''})</span>
                <span style="display:flex;align-items:center;gap:8px">${statusHtml} ${removeHtml}</span>
            </div>
        `;
    }).join('');
    list.innerHTML = html;
    // Also render to processing view (step 3) so per-file status is visible during processing
    const procSection = document.getElementById('processingFileListSection');
    const procList = document.getElementById('processingFileList');
    if (procSection && procList) {
        procSection.classList.remove('hidden');
        procList.innerHTML = html;
    }
    const summary = document.getElementById('queueSummary');
    if (summary) {
        if (pendingCount > 0) {
            summary.textContent = `${doneCount} of ${uploadedFiles.length} done — ${pendingCount} pending`;
            summary.classList.remove('hidden');
        } else {
            summary.classList.add('hidden');
        }
    }
}

/**
 * Move a file up or down in the queue.
 *
 * @param {number} index — Current index of the file
 * @param {number} direction — -1 to move up, +1 to move down
 */
function moveFile(index, direction) {
    const newIndex = index + direction;
    if (newIndex < 0 || newIndex >= uploadedFiles.length) return;
    const item = uploadedFiles.splice(index, 1)[0];
    uploadedFiles.splice(newIndex, 0, item);
    renderFileList();
}

/**
 * Remove a file from the queue.
 *
 * @param {number} index — Index of the file to remove
 */
async function removeFile(index) {
    const file = uploadedFiles[index];
    if (!file) return;
    // Remove from backend by stable file_id (not /clear which wipes everything)
    if (file.file_id) {
        try {
            await fetch('/remove-file', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ file_id: file.file_id })
            });
        } catch (e) { /* ignore */ }
    }
    uploadedFiles.splice(index, 1);
    renderFileList();
    updateStepClickability();
    if (uploadedFiles.length === 0) {
        goToStep(1);
    }
}

/**
 * Clear all files from the queue.
 */
async function clearFiles() {
    await fetch('/clear', { method: 'POST' });
    uploadedFiles = [];
    fileInput.value = '';
    const errorBanner = document.getElementById('uploadErrors');
    if (errorBanner) { errorBanner.innerHTML = ''; errorBanner.classList.add('hidden'); }
    renderFileList();
    updateStepClickability();
    goToStep(1);
}

// ─── Step navigation state ───────────────────────────────────

/**
 * Enable/disable the step 2 (queue) button based on whether files exist.
 */
function updateStepClickability() {
    const hasFiles = uploadedFiles.length > 0;
    const step2 = document.getElementById('step2');
    if (hasFiles) {
        step2.classList.add('clickable');
        step2.onclick = () => goToStep(2);
    } else {
        step2.classList.remove('clickable');
        step2.onclick = null;
    }
}
