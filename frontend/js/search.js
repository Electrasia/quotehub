// ─── Search ──────────────────────────────────────────────────
let searchSelectedIds = new Set();
let sortField = '';
let sortDir = 'asc'; // asc or desc
let lastSearchItems = [];
let searchDebounceTimer = null;

function escapeHtml(s) {
    if (s == null) return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function debounceSearch() {
    clearTimeout(searchDebounceTimer);
    searchDebounceTimer = setTimeout(() => searchQuotations(), 300);
}

function toggleSelectAll(cb) {
    document.querySelectorAll('.search-cb').forEach(c => { c.checked = cb.checked; });
    updateSelection();
}

function updateSelection() {
    searchSelectedIds.clear();
    document.querySelectorAll('.search-cb:checked').forEach(c => {
        searchSelectedIds.add(c.dataset.id);
    });
    const count = searchSelectedIds.size;
    const bar = document.getElementById('searchActions');
    if (count > 0) {
        bar.classList.remove('hidden');
        document.getElementById('searchSelectedCount').textContent = count;
    } else {
        bar.classList.add('hidden');
    }
}

function sortBy(field) {
    if (sortField === field) {
        sortDir = sortDir === 'asc' ? 'desc' : 'asc';
    } else {
        sortField = field;
        sortDir = 'asc';
    }
    renderSearchResults();
}

function sortItems(items) {
    if (!sortField) return items;
    const sorted = [...items];
    sorted.sort((a, b) => {
        let va = (a[sortField] || '').toString().toLowerCase();
        let vb = (b[sortField] || '').toString().toLowerCase();
        // For numeric-like fields (unit_price), parse as number
        if (sortField === 'unit_price' || sortField === 'price') {
            va = parseFloat((va || '0').replace(/,/g, '')) || 0;
            vb = parseFloat((vb || '0').replace(/,/g, '')) || 0;
            return sortDir === 'asc' ? va - vb : vb - va;
        }
        // For date fields, try date parsing
        if (sortField === 'date' || sortField === '_date') {
            const da = Date.parse(va) || 0;
            const db = Date.parse(vb) || 0;
            return sortDir === 'asc' ? da - db : db - da;
        }
        // Text comparison
        if (va < vb) return sortDir === 'asc' ? -1 : 1;
        if (va > vb) return sortDir === 'asc' ? 1 : -1;
        return 0;
    });
    return sorted;
}

function renderSearchResults() {
    const allItems = lastSearchItems;
    const container = document.getElementById('searchResults');
    const sorted = sortItems(allItems);

    const sortClass = (field) => {
        if (sortField !== field) return 'sortable';
        return `sortable sort-${sortDir}`;
    };

    const tableHeader = `
        <tr>
            <th style="width:40px"><input type="checkbox" id="selectAllCb" onchange="toggleSelectAll(this)"></th>
            <th class="${sortClass('brand')}" onclick="sortBy('brand')">Brand</th>
            <th class="${sortClass('model')}" onclick="sortBy('model')">Model</th>
            <th class="${sortClass('description')}" onclick="sortBy('description')">Description</th>
            <th class="${sortClass('unit_price')}" style="text-align:right" onclick="sortBy('unit_price')">Unit Price</th>
            <th class="${sortClass('date')}" style="text-align:right" onclick="sortBy('date')">Date</th>
            <th class="${sortClass('supplier')}" onclick="sortBy('supplier')">Supplier</th>
            <th class="${sortClass('currency')}" onclick="sortBy('currency')">Currency</th>
            <th class="${sortClass('_document_type')}" onclick="sortBy('_document_type')" style="text-align:center">Type</th>
        </tr>`;

    const renderItem = (item) => {
        const fn = escapeHtml(item._filename || '');
        const id = escapeHtml(String(item._id ?? ''));
        const cells = [
            { html: `<input type="checkbox" class="search-cb" data-id="${id}" data-file="${fn}" data-filename="${fn}" onchange="updateSelection()">` },
            { text: item.brand },
            { text: item.model },
            { html: escapeHtml(item.description || ''), style: 'word-wrap:break-word;max-width:300px' },
            { text: item.unit_price || item.price, className: 'text-right nowrap-cell' },
            { text: item.date, className: 'text-right nowrap-cell' },
            { html: escapeHtml(item.supplier || item._supplier || ''), style: 'word-wrap:break-word;max-width:150px' },
            { text: item.currency, className: 'nowrap-cell' },
        ];
        return `<tr data-filename="${fn}" style="cursor:pointer" title="Double-click to view PDF">` +
            cells.map(c => `<td${c.className ? ` class="${c.className}"` : ''}${c.style ? ` style="${c.style}"` : ''}>${c.html !== undefined ? c.html : escapeHtml(c.text || '')}</td>`).join('') +
            `<td style="text-align:center">${escapeHtml(item._document_type || '-')}</td>` +
            `</tr>`;
    };

    container.innerHTML = `
        <div style="background:#fff;border-radius:12px;overflow:hidden">
            <table>
                <thead>${tableHeader}</thead>
                <tbody>${sorted.map(item => renderItem(item)).join('')}</tbody>
            </table>
        </div>
    `;
    container.querySelectorAll('tr[data-filename]').forEach(tr => {
        tr.ondblclick = () => viewPdf(tr.dataset.filename);
    });
}

async function searchQuotations() {
    const q = document.getElementById('searchInput').value;
    sortField = '';
    sortDir = 'asc';
    const resp = await fetch(`/search?q=${encodeURIComponent(q)}`);
    const results = await resp.json();
    const container = document.getElementById('searchResults');
    searchSelectedIds.clear();
    document.getElementById('searchActions').classList.add('hidden');

    if (results.length === 0) {
        container.innerHTML = '<p style="padding:20px;text-align:center;color:#666">No results found</p>';
        lastSearchItems = [];
        return;
    }

    let allItems = [];
    results.forEach(r => {
        (r.items || []).forEach(item => {
            allItems.push({ ...item, _id: r.id, _filename: r.filename, _supplier: r.supplier || '', _date: r.quotation_date || '', _document_type: r.document_type || '' });
        });
    });

    lastSearchItems = allItems;
    renderSearchResults();
}

// ─── PDF Viewer ──────────────────────────────────────────────
function viewPdf(filename) {
    document.getElementById('pdfViewerFrame').src = `/archive/${encodeURIComponent(filename)}`;
    document.getElementById('pdfViewerModal').classList.add('active');
}

function closePdfViewer() {
    document.getElementById('pdfViewerFrame').src = '';
    document.getElementById('pdfViewerModal').classList.remove('active');
}

// ─── Edit Selected (from search) ─────────────────────────────
let editQuotationId = null;

async function editSelected() {
    const ids = [...searchSelectedIds].map(Number);
    if (ids.length === 0) return;
    // For now, edit the first selected quotation
    editQuotationId = ids[0];
    // Fetch the quotation data
    const resp = await fetch(`/search?q=`);
    const results = await resp.json();
    const quotation = results.find(r => r.id === editQuotationId);
    if (!quotation) { showBriefPopup('Quotation not found.'); return; }

    document.getElementById('editSupplier').value = quotation.supplier || '';
    document.getElementById('editDocumentType').value = quotation.document_type || 'unknown';
    updateEditDocumentTypeWarning();
    const tbody = document.getElementById('editItemsTable');
    tbody.innerHTML = '';
    (quotation.items || []).forEach(item => editAddRow(item));
    document.getElementById('editModal').classList.add('active');
}

function updateEditDocumentTypeWarning() {
    const val = document.getElementById('editDocumentType').value;
    const warn = document.getElementById('editDocTypeWarning');
    warn.style.display = (val === 'unknown') ? 'inline' : 'none';
    const saveBtn = document.querySelector('button[onclick="saveEdit()"]');
    if (saveBtn) saveBtn.disabled = (val === 'unknown');
}

function editFindAndReplace() {
    const find = document.getElementById('editFindInput').value;
    const replace = document.getElementById('editReplaceInput').value;
    const caseSensitive = document.getElementById('editFindCaseSensitive').checked;
    if (!find) { showBriefPopup('Enter a search term.'); return; }

    let count = 0;
    const rows = document.querySelectorAll('#editItemsTable tr');
    rows.forEach(row => {
        row.querySelectorAll('input, textarea').forEach(el => {
            let val = el.value;
            if (!caseSensitive) {
                let offset = 0;
                while (offset < val.length) {
                    const idx = val.toLowerCase().indexOf(find.toLowerCase(), offset);
                    if (idx === -1) break;
                    el.value = el.value.substring(0, idx) + replace + el.value.substring(idx + find.length);
                    val = el.value;
                    offset = idx + replace.length;
                    count++;
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

function editAddRow(item = {}) {
    const tbody = document.getElementById('editItemsTable');
    const tr = document.createElement('tr');
    const supplierVal = item.supplier || document.getElementById('editSupplier').value || '';
    tr.innerHTML = `
        <td><input type="text" value="${escapeHtml(item.brand || '')}" placeholder="Brand"></td>
        <td><input type="text" value="${escapeHtml(item.model || '')}" placeholder="Model"></td>
        <td><textarea placeholder="Description" rows="2" style="width:100%;resize:vertical">${escapeHtml(item.description || '')}</textarea></td>
        <td><input type="text" class="price-input" value="${escapeHtml(item.unit_price || item.price || '')}" placeholder="0.00"></td>
        <td><input type="text" class="text-right" value="${escapeHtml(item.date || '')}" placeholder="YYYY-MM-DD"></td>
        <td><input type="text" value="${escapeHtml(supplierVal)}" placeholder="Supplier"></td>
        <td><input type="text" value="${escapeHtml(item.currency || '')}" placeholder="Currency"></td>
        <td><button class="btn btn-sm btn-danger" onclick="this.closest('tr').remove()">✕</button></td>
    `;
    tbody.appendChild(tr);
}

async function saveEdit() {
    const rows = document.querySelectorAll('#editItemsTable tr');
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
    const data = {
        supplier: document.getElementById('editSupplier').value,
        document_type: document.getElementById('editDocumentType').value,
        items: items
    };
    try {
        const resp = await fetch('/update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: editQuotationId, data: data })
        });
        const result = await resp.json();
        if (result.status === 'updated') {
            closeModal('editModal');
            searchQuotations();
        }
    } catch (e) {
        showBriefPopup('Update failed: ' + e.message);
    }
}

// ─── Delete Selected ─────────────────────────────────────────
async function deleteSelected() {
    const ids = [...searchSelectedIds].map(Number);
    if (ids.length === 0) return;
    showConfirmPopup(`Delete ${ids.length} quotation(s)? This will also remove the archived PDFs.`, async () => {
        try {
            const resp = await fetch('/delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ids: ids })
            });
            const result = await resp.json();
            if (result.status === 'deleted') {
                searchQuotations();
            }
        } catch (e) {
            showBriefPopup('Delete failed: ' + e.message);
        }
    });
}

// Close PDF viewer on Escape
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closePdfViewer();
});
