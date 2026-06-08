/**
 * frontend/js/review.js — Review screen with PDF viewer and editable table.
 *
 * Provides the side-by-side review experience: PDF image viewer with
 * zoom/pan controls on the left, editable items table on the right.
 * Also handles brand suggestion, bulk-apply, find & replace, and saving.
 *
 * Depends on globals from:
 *   app.js   — extractedData, reviewPages, reviewCurrentPage, currentFileIndex,
 *              uploadedFiles, processing, popupDurationSec
 *   utils.js — escapeHtml, showBriefPopup, showConfirmPopup, closeModal
 *   upload.js — renderFileList, updateStepClickability
 */

// ─── Review PDF zoom state ──────────────────────────────────
let reviewZoom = 1.0;        // 1.0 = 100% = natural pixel size
let reviewAutoFit = true;    // true when a new file is loaded (fit-width on first image load)

// ─── Review (PDF + Data side by side) ────────────────────────

/**
 * Show the review screen for a processed file.
 *
 * Populates the supplier, document type, and items table from
 * extractedData, then displays the PDF viewer.
 *
 * @param {string} filename — The filename being reviewed
 */
function showReview(filename) {
    document.getElementById('reviewFilename').textContent = filename;
    document.getElementById('supplierName').value = extractedData.supplier || '';
    document.getElementById('reviewDate').value = extractedData.date || '';
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

/**
 * Show/hide the document type warning and toggle the save button.
 */
function updateDocumentTypeWarning() {
    const val = document.getElementById('documentType').value;
    const warn = document.getElementById('docTypeWarning');
    warn.style.display = (val === 'unknown') ? 'inline' : 'none';
    const saveBtn = document.querySelector('button[onclick="confirmSave()"]');
    if (saveBtn) saveBtn.disabled = (val === 'unknown');
}

// ─── Review PDF viewer ───────────────────────────────────────

/**
 * Update the PDF preview image to the current page.
 *
 * Applies zoom (auto-fit on first load, then manual zoom state).
 */
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

/**
 * Open the original PDF in a new browser window.
 */
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

// ─── Items table ─────────────────────────────────────────────

/**
 * Update the item count display.
 */
function updateItemCount() {
    const count = document.querySelectorAll('#itemsTable tr').length;
    document.getElementById('itemCount').textContent = count;
}

/**
 * Renumber row numbers after add/remove/reorder.
 */
function renumberRows() {
    document.querySelectorAll('#itemsTable tr').forEach((tr, i) => {
        const numCell = tr.querySelector('.row-number');
        if (numCell) numCell.textContent = i + 1;
    });
}

/**
 * Add a new row to the items table.
 *
 * @param {Object} [item={}] — Item data to populate the row (brand, model, etc.)
 */
function addRow(item = {}) {
    const tbody = document.getElementById('itemsTable');
    const tr = document.createElement('tr');
    const rowNumber = tbody.querySelectorAll('tr').length + 1;
    // Use item-level currency, fallback to document-level
    const currency = item.currency || extractedData?.currency || '';
    // Use item-level date, fallback to document-level
    const date = item.date || extractedData?.date || '';
    tr.innerHTML = `
        <td class="row-number">${rowNumber}</td>
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
        <td><input type="text" value="${escapeHtml(item.description || '')}" placeholder="Description"></td>
        <td><input type="text" value="${escapeHtml(date)}" placeholder="YYYY-MM-DD" style="width:100px"></td>
        <td><input type="text" value="${escapeHtml(currency)}" placeholder="Currency" style="width:70px"></td>
        <td><input type="text" class="price-input text-right" value="${escapeHtml(item.unit_price || item.price || '')}" placeholder="0.00"></td>
        <td><input type="text" class="text-right" value="${escapeHtml(item.quantity || '')}" placeholder="0"></td>
        <td><input type="text" class="price-input text-right" value="${escapeHtml(item.total || '')}" placeholder="0.00"></td>
        <td><button class="btn btn-sm btn-danger" onclick="this.closest('tr').remove(); updateItemCount(); renumberRows(); updateBulkCount(1, 'bulkCount')">✕</button></td>
    `;
    tbody.appendChild(tr);
    updateItemCount();
    renumberRows();
    updateBulkCount(1, 'bulkCount');
}

// ─── Brand Suggestion (per-row) ────────────────────────────

/**
 * Debounced handler for model input — triggers brand suggestion fetch.
 *
 * @param {HTMLInputElement} input — The model input element
 */
function onModelInput(input) {
    // Per-input debounce (each input owns its own timer, not a global)
    if (input._brandDebounceTimer) clearTimeout(input._brandDebounceTimer);
    input._brandDebounceTimer = setTimeout(() => fetchBrandSuggestion(input), 300);
}

/**
 * Fetch brand suggestion from the server for a given model.
 *
 * @param {HTMLInputElement} modelInput — The model input element
 */
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

// ─── Bulk operations ─────────────────────────────────────────

/**
 * Update the count of empty rows for a given column.
 *
 * @param {number} columnIndex — The column index to check (0-based)
 * @param {string} countSpanId — The ID of the count display element
 */
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

// ─── Find & Replace ─────────────────────────────────────────

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

// ─── Save / Back ─────────────────────────────────────────────

/**
 * Collect all edited data from the review table.
 *
 * @returns {Object} Data object with supplier, document_type, and items array
 */
function getEditedData() {
    const rows = document.querySelectorAll('#itemsTable tr');
    const items = [];
    rows.forEach(row => {
        const inputs = row.querySelectorAll('input');
        // Column order: #(rowNum), Brand, Model, Description, Date, Currency, UnitPrice, TotalQty, TotalPrice
        items.push({
            brand: inputs[1].value,
            model: inputs[2].value,
            description: inputs[3].value,
            date: inputs[4].value,
            currency: inputs[5].value,
            unit_price: inputs[6].value,
            quantity: inputs[7].value,
            total: inputs[8].value
        });
    });
    return {
        supplier: document.getElementById('supplierName').value,
        date: document.getElementById('reviewDate').value,
        document_type: document.getElementById('documentType').value,
        items: items
    };
}

/**
 * Save the edited data to the backend.
 */
async function confirmSave() {
    const data = getEditedData();
    const resp = await fetch('/confirm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file_id: currentFileIndex, data: data })
    });
    const result = await resp.json();
    if (result.status === 'saved') {
        // Update frontend status
        const fileIdx = uploadedFiles.findIndex(f => f.file_id === currentFileIndex);
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

// ─── Init ────────────────────────────────────────────────────
// Setup mouse controls for the review PDF viewer on load.
setupReviewMouseControls();

// Keep the Brand bulk-apply count in sync when the user edits cells directly
document.getElementById('itemsTable').addEventListener('input', () => {
    updateBulkCount(0, 'bulkCount');
});
