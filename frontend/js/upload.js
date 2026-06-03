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
        if (file.type !== 'application/pdf') continue;
        const formData = new FormData();
        formData.append('file', file);
        const resp = await fetch('/upload', { method: 'POST', body: formData });
        const data = await resp.json();
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
        return;
    }
    section.classList.remove('hidden');
    const pendingCount = uploadedFiles.filter(f => f.status === 'pending').length;
    const doneCount = uploadedFiles.filter(f => f.status === 'done' || f.status === 'saved').length;
    list.innerHTML = uploadedFiles.map((f, i) => {
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
                <span class="file-name">${moveHtml}${dupBadge}${f.filename} (${f.pages} page${f.pages !== 1 ? 's' : ''})</span>
                <span style="display:flex;align-items:center;gap:8px">${statusHtml} ${removeHtml}</span>
            </div>
        `;
    }).join('');
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

    document.getElementById('processingModal').classList.add('active');
    processing = true;
    abortController = new AbortController();
    document.getElementById('processingText').textContent = `Starting ${file.filename}...`;
    document.getElementById('processingDetail').textContent = '';
    document.getElementById('progressFill').style.width = '0%';

    try {
        const resp = await fetch('/process-stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file_index: backendIdx }),
            signal: abortController.signal
        });

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
                        document.getElementById('processingText').textContent = msg.message;
                        document.getElementById('progressFill').style.width = msg.percent + '%';
                        uploadedFiles[fileIdx].progress = `Page ${msg.page}/${msg.total}`;
                        renderFileList();
                    } else if (msg.type === 'page_done') {
                        document.getElementById('processingDetail').textContent = `Found ${msg.items_found} item(s) on page ${msg.page}`;
                        document.getElementById('progressFill').style.width = msg.percent + '%';
                        uploadedFiles[fileIdx].progress = `Page ${msg.page}/${msg.total} ✓`;
                        renderFileList();
                    } else if (msg.type === 'page_error') {
                        document.getElementById('processingDetail').textContent = `Page ${msg.page}: ${msg.error}`;
                    } else if (msg.type === 'done') {
                        document.getElementById('progressFill').style.width = '100%';
                        extractedData = msg.data;
                        uploadedFiles[fileIdx].status = 'done';
                        uploadedFiles[fileIdx].progress = '';
                        renderFileList();

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
        }
    } finally {
        processing = false;
        // Only close modal if not already closed by cancelProcessing
        if (document.getElementById('processingModal').classList.contains('active')) {
            document.getElementById('processingModal').classList.remove('active');
        }
    }
}

function cancelProcessing() {
    if (abortController) {
        abortController.abort();
        abortController = null;
    }
    processing = false;
    // Reset current file status to pending
    if (currentFileIndex >= 0) {
        const fileIdx = uploadedFiles.findIndex(f => f.backendIndex === currentFileIndex);
        if (fileIdx !== -1) {
            uploadedFiles[fileIdx].status = 'pending';
            uploadedFiles[fileIdx].progress = '';
            renderFileList();
        }
    }
    document.getElementById('processingModal').classList.remove('active');
}

// ─── Review (PDF + Data side by side) ────────────────────────
function showReview(filename) {
    document.getElementById('reviewFilename').textContent = filename;
    document.getElementById('supplierName').value = extractedData.supplier || '';
    const tbody = document.getElementById('itemsTable');
    tbody.innerHTML = '';
    (extractedData.items || []).forEach(item => addRow(item));
    updateItemCount();
    updateReviewPdf();
    goToStep(4);
}

function updateReviewPdf() {
    if (reviewPages.length > 0) {
        document.getElementById('reviewPdfImg').src = reviewPages[reviewCurrentPage];
        document.getElementById('reviewPageInfo').textContent = `Page ${reviewCurrentPage + 1} of ${reviewPages.length}`;
    }
}

function reviewPrevPage() {
    if (reviewCurrentPage > 0) { reviewCurrentPage--; updateReviewPdf(); }
}

function reviewNextPage() {
    if (reviewCurrentPage < reviewPages.length - 1) { reviewCurrentPage++; updateReviewPdf(); }
}

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
        <td><input type="text" value="${item.brand || ''}" placeholder="Brand"></td>
        <td><input type="text" value="${item.model || ''}" placeholder="Model"></td>
        <td><textarea placeholder="Description" rows="2" style="width:100%;resize:vertical">${item.description || ''}</textarea></td>
        <td><input type="text" class="price-input" value="${item.unit_price || item.price || ''}" placeholder="0.00"></td>
        <td><input type="text" class="text-right" value="${item.date || ''}" placeholder="YYYY-MM-DD"></td>
        <td><input type="text" value="${supplierVal}" placeholder="Supplier"></td>
        <td><input type="text" value="${item.currency || ''}" placeholder="Currency"></td>
        <td><button class="btn btn-sm btn-danger" onclick="this.closest('tr').remove(); updateItemCount()">✕</button></td>
    `;
    tbody.appendChild(tr);
    updateItemCount();
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
