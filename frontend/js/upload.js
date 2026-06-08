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

async function handleFiles(files) {
    for (const file of files) {
        // v0.038.0: check by extension, not MIME type. .xlsx files have
        // MIME type 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        // (or 'application/octet-stream' on some systems), so the old
        // `file.type === 'application/pdf'` check silently dropped them.
        const name = (file.name || '').toLowerCase();
        if (!name.endsWith('.pdf') && !name.endsWith('.xlsx')) continue;
        const formData = new FormData();
        formData.append('file', file);
        const resp = await fetch('/upload', { method: 'POST', body: formData });
        const data = await resp.json();
        if (!resp.ok) {
            showBriefPopup(`Upload failed: ${data.error || resp.statusText}`);
            continue;
        }
        data.backendIndex = data.file_index;
        // Check for duplicates
        try {
            const dupResp = await fetch(`/check-duplicate?filename=${encodeURIComponent(file.name)}`);
            const dup = await dupResp.json();
            if (dup.exists || dup.in_database) {
                data.duplicate = true;
            }
        } catch (e) { /* ignore check errors */ }
        uploadedFiles.push(data);
        renderFileList();
    }
    updateStepClickability();
}

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

function moveFile(index, direction) {
    const newIndex = index + direction;
    if (newIndex < 0 || newIndex >= uploadedFiles.length) return;
    const item = uploadedFiles.splice(index, 1)[0];
    uploadedFiles.splice(newIndex, 0, item);
    renderFileList();
}

async function removeFile(index) {
    const file = uploadedFiles[index];
    if (!file) return;
    // Remove from backend if it has a backend index
    if (file.backendIndex !== undefined) {
        try { await fetch('/clear', { method: 'POST' }); } catch (e) { /* ignore */ }
    }
    uploadedFiles.splice(index, 1);
    renderFileList();
    updateStepClickability();
    if (uploadedFiles.length === 0) {
        goToStep(1);
    }
}

async function clearFiles() {
    await fetch('/clear', { method: 'POST' });
    uploadedFiles = [];
    renderFileList();
    updateStepClickability();
    goToStep(1);
}

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

// ─── Process All Pages (streaming progress) ──────────────────
async function processAll() {
    if (!isConnected) { showBriefPopup('Please connect to AI server first.'); return; }
    if (uploadedFiles.length === 0) return;

    let fileIdx = uploadedFiles.findIndex(f => f.status === 'pending');
    if (fileIdx === -1) { showBriefPopup('No pending files to process.'); return; }

    const file = uploadedFiles[fileIdx];
    const backendIdx = file.backendIndex;
    currentFileIndex = backendIdx;

    // Mark as processing
    uploadedFiles[fileIdx].status = 'processing';
    uploadedFiles[fileIdx].progress = 'Starting...';
    renderFileList();
    goToStep(3);

    // Show inline progress area (replaces the old #processingModal overlay)
    document.getElementById('inlineProgress').classList.remove('hidden');
    processing = true;
    abortController = new AbortController();
    currentFilePercent = 0;
    updateInlineProgress(file.filename, 'Starting...', 0);
    updateOverallProgress();

    try {
        const resp = await fetch('/process-stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file_index: backendIdx }),
            signal: abortController.signal
        });

        // Safety net (v0.038.0): if the backend returns a non-OK status
        // (e.g. 400 with a JSON error body), surface the error instead of
        // silently dropping the body. The SSE parser below only reads lines
        // starting with 'data: ', so without this check a JSON error body
        // would be dropped and the user would see a stuck spinner.
        if (!resp.ok) {
            let errMsg = `HTTP ${resp.status}`;
            try {
                const errBody = await resp.json();
                errMsg = errBody.error || errBody.detail || errMsg;
            } catch (e) { /* body wasn't JSON, keep status code */ }
            throw new Error(errMsg);
        }

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop(); // Keep incomplete line in buffer

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                const jsonStr = line.slice(6);
                try {
                    const msg = JSON.parse(jsonStr);
                    if (msg.type === 'progress') {
                        currentFilePercent = msg.percent;
                        updateInlineProgress(file.filename, msg.message, msg.percent);
                        updateOverallProgress();
                        uploadedFiles[fileIdx].progress = `Page ${msg.page}/${msg.total}`;
                        renderFileList();
                    } else if (msg.type === 'page_done') {
                        currentFilePercent = msg.percent;
                        updateInlineProgress(file.filename, `Found ${msg.items_found} item(s) on page ${msg.page}`, msg.percent);
                        updateOverallProgress();
                        uploadedFiles[fileIdx].progress = `Page ${msg.page}/${msg.total} ✓`;
                        renderFileList();
                    } else if (msg.type === 'page_error') {
                        updateInlineProgress(file.filename, `Page ${msg.page}: ${msg.error}`, currentFilePercent);
                    } else if (msg.type === 'done') {
                        currentFilePercent = 100;
                        updateInlineProgress(file.filename, 'Done', 100);
                        updateOverallProgress();
                        extractedData = msg.data;
                        uploadedFiles[fileIdx].status = 'done';
                        uploadedFiles[fileIdx].progress = '';
                        renderFileList();

                        // Hide inline progress (review takes over)
                        document.getElementById('inlineProgress').classList.add('hidden');

                        const pagesResp = await fetch(`/next-file?file_index=${backendIdx}`);
                        const pagesData = await pagesResp.json();
                        reviewPages = pagesData.pages;
                        reviewCurrentPage = 0;

                        showReview(file.filename);
                    }
                } catch (e) { /* skip parse errors */ }
            }
        }
    } catch (err) {
        if (err.name !== 'AbortError') {
            showBriefPopup('Processing failed: ' + err.message);
            uploadedFiles[fileIdx].status = 'error';
            uploadedFiles[fileIdx].progress = '';
            renderFileList();
            document.getElementById('inlineProgress').classList.add('hidden');
        }
    } finally {
        processing = false;
        abortController = null;
    }
}

// ─── Inline progress helpers (v0.038.0 UI cleanup) ───────────
// Update the per-file progress row: filename + status text + bar width.
function updateInlineProgress(filename, statusText, percent) {
    const label = document.getElementById('perFileLabel');
    const fill  = document.getElementById('perFileProgressFill');
    if (label) label.textContent = `${filename} — ${statusText}`;
    if (fill)  fill.style.width = `${Math.max(0, Math.min(100, percent))}%`;
}

// Compute weighted overall progress:
//   - N = total files in queue
//   - each completed file contributes 1/N
//   - currently processing file contributes (currentFilePercent/100) * (1/N)
//   - pending files contribute 0
function updateOverallProgress() {
    const N = uploadedFiles.length;
    if (N === 0) {
        document.getElementById('overallProgressLabel').textContent = 'Overall progress';
        document.getElementById('overallProgressPct').textContent = '0%';
        document.getElementById('overallProgressFill').style.width = '0%';
        return;
    }
    const completed = uploadedFiles.filter(f => f.status === 'done' || f.status === 'saved').length;
    const currentFraction = (currentFilePercent / 100) / N;
    const overall = Math.min(100, Math.round((completed / N + currentFraction) * 100));
    const processingIdx = uploadedFiles.findIndex(f => f.status === 'processing');
    const currentNumber = processingIdx >= 0 ? processingIdx + 1 : Math.min(completed + 1, N);
    document.getElementById('overallProgressLabel').textContent =
        `Processing ${currentNumber} of ${N} files · Overall ${overall}%`;
    document.getElementById('overallProgressPct').textContent = `${overall}%`;
    document.getElementById('overallProgressFill').style.width = `${overall}%`;
}

function cancelProcessing() {
    if (abortController) {
        abortController.abort();
        abortController = null;
    }
    processing = false;
    currentFilePercent = 0;
    // Reset current file status to pending
    if (currentFileIndex >= 0) {
        const fileIdx = uploadedFiles.findIndex(f => f.backendIndex === currentFileIndex);
        if (fileIdx !== -1) {
            uploadedFiles[fileIdx].status = 'pending';
            uploadedFiles[fileIdx].progress = '';
            renderFileList();
        }
    }
    // Hide inline progress area (v0.038.0: replaces old modal)
    const inlineProgress = document.getElementById('inlineProgress');
    if (inlineProgress) inlineProgress.classList.add('hidden');
    updateOverallProgress();
    // Return to file list (step 3 has no cancel/progress, so don't leave user stuck there)
    const hasPending = uploadedFiles.some(f => f.status === 'pending');
    goToStep(hasPending ? 2 : 1);
}

// ─── Review (PDF + Data side by side) ────────────────────────
function showReview(filename) {
    document.getElementById('reviewFilename').textContent = filename;
    document.getElementById('supplierName').value = extractedData.supplier || '';
    document.getElementById('documentType').value = extractedData.document_type || 'unknown';
    updateDocumentTypeWarning();
    const tbody = document.getElementById('itemsTable');
    tbody.innerHTML = '';
    (extractedData.items || []).forEach(item => addRow(item));
    updateItemCount();
    updateBulkCount(0, 'bulkCount');
    reviewAutoFit = true;
    updateReviewPdf();
    goToStep(4);
}

function updateDocumentTypeWarning() {
    const val = document.getElementById('documentType').value;
    const warn = document.getElementById('docTypeWarning');
    warn.style.display = (val === 'unknown') ? 'inline' : 'none';
    const saveBtn = document.querySelector('button[onclick="confirmSave()"]');
    if (saveBtn) saveBtn.disabled = (val === 'unknown');
}

function updateReviewPdf() {
    if (reviewPages.length > 0) {
        const img = document.getElementById('reviewPdfImg');
        img.src = reviewPages[reviewCurrentPage];
        const finalize = () => {
            if (reviewAutoFit) {
                reviewFitWidth();
                reviewAutoFit = false;
            } else {
                applyReviewZoom();
            }
            img.onload = null;
        };
        if (img.complete && img.naturalWidth) {
            finalize();
        } else {
            img.onload = finalize;
        }
        document.getElementById('reviewPageInfo').textContent = `Page ${reviewCurrentPage + 1} of ${reviewPages.length}`;
    }
}

function reviewPrevPage() {
    if (reviewCurrentPage > 0) { reviewCurrentPage--; updateReviewPdf(); }
}

function reviewNextPage() {
    if (reviewCurrentPage < reviewPages.length - 1) { reviewCurrentPage++; updateReviewPdf(); }
}

// ─── Review PDF zoom controls ────────────────────────────────
function applyReviewZoom() {
    const img = document.getElementById('reviewPdfImg');
    if (!img.naturalWidth) return;
    img.style.width = (img.naturalWidth * reviewZoom) + 'px';
    img.style.height = 'auto';
    document.getElementById('reviewZoomInfo').textContent = `${Math.round(reviewZoom * 100)}%`;
}

function reviewZoomIn() {
    reviewZoom = Math.min(reviewZoom * 1.25, 5.0);
    applyReviewZoom();
}

function reviewZoomOut() {
    reviewZoom = Math.max(reviewZoom / 1.25, 0.1);
    applyReviewZoom();
}

function reviewZoomReset() {
    reviewZoom = 1.0;
    applyReviewZoom();
}

function reviewFitWidth() {
    const img = document.getElementById('reviewPdfImg');
    if (!img.complete || !img.naturalWidth) {
        img.onload = () => reviewFitWidth();
        return;
    }
    const container = document.getElementById('reviewPdfScroll');
    const containerWidth = container.clientWidth;
    reviewZoom = containerWidth / img.naturalWidth;
    applyReviewZoom();
}

function reviewOpenNewWindow() {
    if (!reviewPages || !reviewPages[0]) {
        showBriefPopup('No PDF loaded.');
        return;
    }
    // reviewPages[0] is like /images/{stem}/page_1.png — extract {stem} and reconstruct filename
    const match = reviewPages[0].match(/\/images\/([^/]+)\/page_\d+\.png/);
    if (!match) {
        showBriefPopup('Cannot determine PDF filename from page URL.');
        return;
    }
    const filename = `${match[1]}.pdf`;
    window.open(`/archive/${encodeURIComponent(filename)}`, '_blank');
}

// ─── Review PDF mouse controls (wheel zoom + drag pan) ──────
function setupReviewMouseControls() {
    const img = document.getElementById('reviewPdfImg');
    const container = document.getElementById('reviewPdfScroll');
    if (!img || !container) return;

    let isPanning = false;
    let startX = 0, startY = 0, scrollLeft = 0, scrollTop = 0;

    // Mouse wheel: zoom only when CTRL is pressed; normal scroll otherwise
    img.addEventListener('wheel', (e) => {
        if (!e.ctrlKey) return;  // allow normal scroll
        e.preventDefault();
        if (e.deltaY < 0) reviewZoomIn();
        else reviewZoomOut();
    }, { passive: false });

    // Click + drag = pan
    img.addEventListener('mousedown', (e) => {
        isPanning = true;
        startX = e.clientX;
        startY = e.clientY;
        scrollLeft = container.scrollLeft;
        scrollTop = container.scrollTop;
        img.style.cursor = 'grabbing';
        img.style.userSelect = 'none';
        e.preventDefault();
    });

    const onMove = (e) => {
        if (!isPanning) return;
        container.scrollLeft = scrollLeft - (e.clientX - startX);
        container.scrollTop = scrollTop - (e.clientY - startY);
    };
    const onUp = () => {
        if (!isPanning) return;
        isPanning = false;
        img.style.cursor = 'grab';
        img.style.userSelect = '';
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);

    img.style.cursor = 'grab';
}

setupReviewMouseControls();

function updateItemCount() {
    const count = document.querySelectorAll('#itemsTable tr').length;
    document.getElementById('itemCount').textContent = count;
}

function findAndReplace() {
    const find = document.getElementById('findInput').value;
    const replace = document.getElementById('replaceInput').value;
    const caseSensitive = document.getElementById('findCaseSensitive').checked;
    if (!find) { showBriefPopup('Enter a search term.'); return; }

    let count = 0;
    const rows = document.querySelectorAll('#itemsTable tr');
    rows.forEach(row => {
        row.querySelectorAll('input, textarea').forEach(el => {
            let val = el.value;
            let searchVal = find;
            let replaceVal = replace;
            if (!caseSensitive) {
                const idx = val.toLowerCase().indexOf(searchVal.toLowerCase());
                if (idx !== -1) {
                    // Rebuild with correct case from original
                    el.value = val.substring(0, idx) + replaceVal + val.substring(idx + searchVal.length);
                    count++;
                    // Continue replacing remaining occurrences
                    let remaining = el.value;
                    let offset = idx + replaceVal.length;
                    while (offset < remaining.length) {
                        const nextIdx = remaining.toLowerCase().indexOf(searchVal.toLowerCase(), offset);
                        if (nextIdx === -1) break;
                        el.value = el.value.substring(0, nextIdx) + replaceVal + el.value.substring(nextIdx + searchVal.length);
                        remaining = el.value;
                        offset = nextIdx + replaceVal.length;
                        count++;
                    }
                }
            } else {
                let offset = 0;
                while (offset < val.length) {
                    const idx = val.indexOf(find, offset);
                    if (idx === -1) break;
                    el.value = el.value.substring(0, idx) + replace + el.value.substring(idx + find.length);
                    val = el.value;
                    offset = idx + replace.length;
                    count++;
                }
            }
        });
    });
    showBriefPopup(count > 0 ? `Replaced ${count} occurrence(s).` : 'No matches found.');
}

function addRow(item = {}) {
    const tbody = document.getElementById('itemsTable');
    const tr = document.createElement('tr');
    const supplierVal = item.supplier || document.getElementById('supplierName').value || '';
    tr.innerHTML = `
        <td>
            <div style="display:flex;align-items:center;gap:4px">
                <input type="text" value="${escapeHtml(item.brand || '')}" placeholder="Brand" style="flex:1;min-width:0"
                       oninput="hideBrandSuggestion(this)">
                <span class="brand-suggestion" style="display:none;font-size:11px;color:#2980b9;background:#ebf5fb;padding:2px 6px;border-radius:3px;white-space:nowrap;cursor:pointer;opacity:0.9"
                      title="Click to apply suggestion" onclick="applyBrandSuggestion(this)"></span>
            </div>
        </td>
        <td><input type="text" value="${escapeHtml(item.model || '')}" placeholder="Model"
                   oninput="onModelInput(this)"></td>
        <td><textarea placeholder="Description" rows="2" style="width:100%;resize:vertical">${escapeHtml(item.description || '')}</textarea></td>
        <td><input type="text" class="price-input" value="${escapeHtml(item.unit_price || item.price || '')}" placeholder="0.00"></td>
        <td><input type="text" class="text-right" value="${escapeHtml(item.date || '')}" placeholder="YYYY-MM-DD"></td>
        <td><input type="text" value="${escapeHtml(supplierVal)}" placeholder="Supplier"></td>
        <td><input type="text" value="${escapeHtml(item.currency || '')}" placeholder="Currency"></td>
        <td><button class="btn btn-sm btn-danger" onclick="this.closest('tr').remove(); updateItemCount(); updateBulkCount(0, 'bulkCount')">✕</button></td>
    `;
    tbody.appendChild(tr);
    updateItemCount();
    updateBulkCount(0, 'bulkCount');
}

// ─── Brand Suggestion (per-row) ────────────────────────────
function onModelInput(input) {
    // Per-input debounce (each input owns its own timer, not a global)
    if (input._brandDebounceTimer) clearTimeout(input._brandDebounceTimer);
    input._brandDebounceTimer = setTimeout(() => fetchBrandSuggestion(input), 300);
}

async function fetchBrandSuggestion(modelInput) {
    const model = modelInput.value.trim();
    const row = modelInput.closest('tr');
    const brandInput = row ? row.querySelector('input[placeholder="Brand"]') : null;
    const badge = row ? row.querySelector('.brand-suggestion') : null;
    if (!badge) return;

    if (!model) {
        badge.style.display = 'none';
        modelInput._lastBrandQueried = '';
        return;
    }
    // Skip if the value hasn't changed since the last successful query
    if (modelInput._lastBrandQueried === model) return;

    try {
        const resp = await fetch(`/items/by-model?model=${encodeURIComponent(model)}`);
        const data = await resp.json();
        modelInput._lastBrandQueried = model;  // remember regardless of result
        if (data.brand && brandInput && !brandInput.value.trim()) {
            badge.textContent = `💡 ${data.brand}`;
            badge.title = `Used ${data.count} time(s). Click to apply.`;
            badge.dataset.brand = data.brand;
            badge.style.display = 'inline';
        } else {
            badge.style.display = 'none';
        }
    } catch (e) {
        // Silently fail (network error, server down, etc.) — user can still type manually
    }
}

function applyBrandSuggestion(badge) {
    const row = badge.closest('tr');
    const brandInput = row ? row.querySelector('input[placeholder="Brand"]') : null;
    if (brandInput && badge.dataset.brand) {
        brandInput.value = badge.dataset.brand;
        badge.style.display = 'none';
        updateBulkCount(0, 'bulkCount');
    }
}

function hideBrandSuggestion(brandInput) {
    const row = brandInput.closest('tr');
    const badge = row ? row.querySelector('.brand-suggestion') : null;
    if (badge) badge.style.display = 'none';
}

function updateBulkCount(columnIndex, countSpanId) {
    const rows = document.querySelectorAll('#itemsTable tr');
    let emptyCount = 0;
    rows.forEach(row => {
        const inputs = row.querySelectorAll('input, textarea');
        const target = inputs[columnIndex];
        if (target && !target.value.trim()) {
            emptyCount++;
        }
    });
    const span = document.getElementById(countSpanId);
    if (span) {
        span.textContent = emptyCount;
        span.title = emptyCount === 0
            ? 'No empty rows in this column'
            : `${emptyCount} empty row(s) in this column`;
    }
}

function bulkApplyColumn(columnIndex, inputId, countSpanId) {
    const value = document.getElementById(inputId).value.trim();
    if (!value) {
        showBriefPopup('Enter a value first.');
        return;
    }
    const rows = document.querySelectorAll('#itemsTable tr');
    let count = 0;
    rows.forEach(row => {
        const inputs = row.querySelectorAll('input, textarea');
        const target = inputs[columnIndex];
        if (target && !target.value.trim()) {
            target.value = value;
            count++;
        }
    });
    showBriefPopup(
        count > 0
            ? `Applied to ${count} empty row(s).`
            : 'No empty rows to fill.'
    );
    if (count > 0) {
        document.getElementById(inputId).value = '';
    }
    if (countSpanId) {
        updateBulkCount(columnIndex, countSpanId);
    }
}

function getEditedData() {
    const rows = document.querySelectorAll('#itemsTable tr');
    const items = [];
    rows.forEach(row => {
        const inputs = row.querySelectorAll('input, textarea');
        items.push({
            brand: inputs[0].value,
            model: inputs[1].value,
            description: inputs[2].value,
            unit_price: inputs[3].value,
            date: inputs[4].value,
            supplier: inputs[5].value,
            currency: inputs[6].value
        });
    });
    return {
        supplier: document.getElementById('supplierName').value,
        document_type: document.getElementById('documentType').value,
        items: items
    };
}

async function confirmSave() {
    const data = getEditedData();
    const resp = await fetch('/confirm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file_index: currentFileIndex, data: data })
    });
    const result = await resp.json();
    if (result.status === 'saved') {
        // Update frontend status
        const fileIdx = uploadedFiles.findIndex(f => f.backendIndex === currentFileIndex);
        if (fileIdx !== -1) {
            uploadedFiles[fileIdx].status = 'saved';
            renderFileList();
        }
        showBriefPopup('Saved successfully!');
        setTimeout(async () => {
            await backToUpload();
            autoProcessNext();
        }, popupDurationSec * 1000);
    } else {
        showBriefPopup('Save failed: ' + result.error);
    }
}

function backToUpload() {
    document.getElementById('searchView').classList.add('hidden');
    extractedData = null;
    reviewPages = [];
    showProcessView();
    const hasPending = uploadedFiles.some(f => f.status === 'pending');
    if (hasPending) {
        goToStep(2);
    } else {
        goToStep(1);
    }
    renderFileList();
}

function autoProcessNext() {
    if (!isConnected) return;
    const nextIdx = uploadedFiles.findIndex(f => f.status === 'pending');
    if (nextIdx !== -1) {
        processAll();
    }
}

// Keep the Brand bulk-apply count in sync when the user edits cells directly
document.getElementById('itemsTable').addEventListener('input', () => {
    updateBulkCount(0, 'bulkCount');
});
