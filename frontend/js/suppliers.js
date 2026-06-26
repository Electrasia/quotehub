/**
 * frontend/js/suppliers.js — Suppliers management UI module.
 *
 * Exposes window.Suppliers namespace.
 *
 * SECURITY:
 *   - Zero innerHTML with untrusted user data.
 *   - All user data rendered via renderTextSafe() + createElement + appendChild.
 *   - No eval, no Function constructor, no new Function().
 *   - Role-based UI hiding is UX only; server enforces all permissions.
 *   - Confirm dialogs for all destructive actions.
 */

"use strict";

window.Suppliers = (function () {

  // ─── Constants ────────────────────────────────────────────
  const DEBOUNCE_MS = 300;
  const AUTOCOMPLETE_MIN_CHARS = 2;
  const AUTOCOMPLETE_MAX_RESULTS = 20;
  const PER_PAGE = 25;
  const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  const PHONE_PATTERN = /^[\d\s+\-().]*$/;

  // ─── State ────────────────────────────────────────────────
  let currentSupplierId = null;
  let dirty = false;
  let listCache = null;            // { items: [], total: number, page: number }
  let autocompleteControllers = {};  // { brands: AbortController }

  // Original data loaded from server (for save diffing)
  let _originalSupplier = null;
  let _originalContacts = [];
  let _originalAliases = [];
  let _originalBrands = [];

  // Current form data (kept in sync with DOM)
  let _currentContacts = [];
  let _currentAliases = [];
  let _currentBrands = [];

  // Pagination
  let _currentPage = 1;
  let _totalPages = 1;

  // Stale-flag: set when a new supplier is created so the list re-fetches
  // on next Back-to-List navigation.
  let _listStale = false;

  // ─── Shared Safe Render Helper ────────────────────────────

  /**
   * Render text safely — always use this for user-supplied data.
   * Returns a TextNode, safe to append to the DOM.
   * @param {*} text - The value to render
   * @returns {Text} A DOM Text node
   */
  function renderTextSafe(text) {
    return document.createTextNode(text == null ? '' : String(text));
  }

  /**
   * Safely unwrap a sub-resource API response that may be either
   * a bare array or an {"items": [...]} envelope.
   * Returns a bare array (empty if neither shape matches).
   * @param {*} resp - The API response value
   * @returns {Array}
   */
  function _unwrapItems(resp) {
    if (Array.isArray(resp)) return resp;
    if (resp && Array.isArray(resp.items)) return resp.items;
    return [];
  }

  // ─── Error Helpers ────────────────────────────────────────

  /**
   * Show inline error message in a container element.
   * @param {string} containerId - Element ID to show the error in
   * @param {string} message - Error message text
   */
  function _showError(containerId, message) {
    const el = document.getElementById(containerId);
    if (!el) return;
    el.textContent = message;
    el.classList.remove('hidden');
  }

  function _clearError(containerId) {
    const el = document.getElementById(containerId);
    if (!el) return;
    el.textContent = '';
    el.classList.add('hidden');
  }

  /**
   * Show success flash message
   * @param {string} message
   */
  function _showSuccess(message) {
    // Reuse existing showBriefPopup from utils.js
    if (typeof showBriefPopup === 'function') {
      showBriefPopup(message);
    }
  }

  // ─── Dirty Flag ───────────────────────────────────────────

  function _setDirty() {
    if (dirty) return;
    dirty = true;
    _updateSaveButton();
  }

  function _clearDirty() {
    dirty = false;
    _updateSaveButton();
  }

  function _updateSaveButton() {
    const btn = document.getElementById('supplierSaveBtn');
    if (btn) {
      btn.disabled = !dirty || _hasInvalidContactFields();
    }
  }

  function _setFieldError(parentGroup, message) {
    const existing = parentGroup.querySelector('.field-error');
    if (existing) existing.remove();
    if (!message) return;
    const errEl = document.createElement('div');
    errEl.className = 'field-error';
    errEl.appendChild(renderTextSafe(message));
    parentGroup.appendChild(errEl);
  }

  function _hasInvalidContactFields() {
    for (const c of _currentContacts) {
      const email = (c.email || '').trim();
      if (email && !EMAIL_PATTERN.test(email)) return true;
      const phone = (c.phone || '').trim();
      if (phone && !PHONE_PATTERN.test(phone)) return true;
    }
    return false;
  }

  // ─── API Client Wrappers ──────────────────────────────────

  /**
   * Generic fetch wrapper with auth + error handling.
   * Uses apiFetch() from auth.js if available, otherwise fallback.
   * Returns parsed JSON or throws with a user-friendly message.
   */
  async function _api(method, url, body) {
    const opts = { method, credentials: 'include' };
    if (body !== undefined) {
      opts.headers = { 'Content-Type': 'application/json' };
      opts.body = JSON.stringify(body);
    }
    try {
      // Use apiFetch if available (handles 401 redirect)
      const fetcher = (typeof apiFetch === 'function') ? apiFetch : fetch;
      const resp = await fetcher(url, opts);
      if (resp.status === 204) return null; // No content
      const data = await resp.json();
      if (!resp.ok) {
        const msg = data.detail || `HTTP ${resp.status}`;
        const err = new Error(`${method} ${url} → ${resp.status}: ${msg}`);
        err.status = resp.status;
        err.data = data;
        throw err;
      }
      return data;
    } catch (e) {
      if (e.status) throw e; // Already wrapped
      // Network error or other
      if (e.message === 'Not authenticated') {
        if (typeof showLogin === 'function') showLogin();
      }
      throw new Error(`Something went wrong. Please try again. (${method} ${url})`);
    }
  }

  async function _apiGet(url) { return _api('GET', url); }
  async function _apiPost(url, body) { return _api('POST', url, body); }
  async function _apiPut(url, body) { return _api('PUT', url, body); }
  async function _apiDelete(url) { return _api('DELETE', url); }

  // ─── List Panel ───────────────────────────────────────────

  /**
   * Load supplier list and render.
   * Called on first navigation to suppliers view.
   */
  async function loadList() {
    _clearError('suppliersListError');
    const listEl = document.getElementById('suppliersListBody');
    if (listEl) {
      listEl.textContent = '';
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.setAttribute('colspan', '6');
      td.style.textAlign = 'center';
      td.style.padding = '24px';
      td.style.color = '#888';
      td.appendChild(renderTextSafe('Loading...'));
      tr.appendChild(td);
      listEl.appendChild(tr);
    }

    const searchEl = document.getElementById('suppliersSearchInput');
    const statusEl = document.getElementById('suppliersStatusFilter');
    const q = searchEl ? searchEl.value.trim() : '';
    const status = statusEl ? statusEl.value : '';

    let url = `/suppliers?page=${_currentPage}&per_page=${PER_PAGE}`;
    if (q) url += `&q=${encodeURIComponent(q)}`;
    if (status && status !== 'all') url += `&status=${encodeURIComponent(status)}`;
    else if (status === 'all') url += `&status=all`;

    try {
      const data = await _apiGet(url);
      listCache = data;
      _totalPages = Math.ceil(data.total / PER_PAGE) || 1;
      _renderList(data.items || []);
      _renderPagination(data.total);
    } catch (e) {
      listCache = null;
      _showError('suppliersListError', e.message || 'Failed to load suppliers');
      if (listEl) {
        listEl.textContent = '';
      }
    }
  }

  /**
   * Render the supplier list table body.
   * @param {Array} suppliers
   */
  function _renderList(suppliers) {
    const tbody = document.getElementById('suppliersListBody');
    if (!tbody) return;
    tbody.textContent = '';

    if (!suppliers || suppliers.length === 0) {
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.setAttribute('colspan', '6');
      td.style.textAlign = 'center';
      td.style.padding = '24px';
      td.style.color = '#888';
      td.appendChild(renderTextSafe("No suppliers yet. Click '+ New Supplier' to add one."));
      tr.appendChild(td);
      tbody.appendChild(tr);
      return;
    }

    for (const s of suppliers) {
      const tr = document.createElement('tr');
      tr.style.cursor = 'pointer';
      tr.addEventListener('click', () => loadDetail(s.id));

      // Display Name
      const tdName = document.createElement('td');
      tdName.appendChild(renderTextSafe(s.display_name || s.canonical_name));
      tr.appendChild(tdName);

      // Canonical Name
      const tdCanon = document.createElement('td');
      tdCanon.appendChild(renderTextSafe(s.canonical_name));
      tdCanon.style.fontSize = '12px';
      tdCanon.style.color = '#888';
      tr.appendChild(tdCanon);

      // Status badge
      const tdStatus = document.createElement('td');
      const badge = document.createElement('span');
      badge.className = 'supplier-status-badge ' + (s.status || 'active');
      badge.appendChild(renderTextSafe(s.status || 'active'));
      tdStatus.appendChild(badge);
      tr.appendChild(tdStatus);

      // Alias count
      const tdAliases = document.createElement('td');
      tdAliases.style.textAlign = 'center';
      tdAliases.appendChild(renderTextSafe(s.alias_count != null ? s.alias_count : 0));
      tr.appendChild(tdAliases);

      // Contact count
      const tdContacts = document.createElement('td');
      tdContacts.style.textAlign = 'center';
      tdContacts.appendChild(renderTextSafe(s.contact_count != null ? s.contact_count : 0));
      tr.appendChild(tdContacts);

      // Created date
      const tdCreated = document.createElement('td');
      tdCreated.style.fontSize = '12px';
      tdCreated.style.color = '#888';
      if (s.created_at) {
        try {
          const d = new Date(s.created_at + (s.created_at.endsWith('Z') ? '' : 'Z'));
          tdCreated.appendChild(renderTextSafe(d.toLocaleDateString()));
        } catch (_) {
          tdCreated.appendChild(renderTextSafe(s.created_at));
        }
      } else {
        tdCreated.appendChild(renderTextSafe('—'));
      }
      tr.appendChild(tdCreated);

      tbody.appendChild(tr);
    }
  }

  /**
   * Render pagination controls.
   * @param {number} total - Total number of results
   */
  function _renderPagination(total) {
    const container = document.getElementById('suppliersPagination');
    if (!container) return;
    container.textContent = '';

    if (total <= PER_PAGE && _currentPage <= 1) {
      // No pagination needed
      return;
    }

    const inner = document.createElement('div');
    inner.style.display = 'flex';
    inner.style.alignItems = 'center';
    inner.style.gap = '8px';
    inner.style.justifyContent = 'center';
    inner.style.marginTop = '12px';

    // Prev button
    const prevBtn = document.createElement('button');
    prevBtn.className = 'btn btn-sm btn-secondary';
    prevBtn.disabled = _currentPage <= 1;
    prevBtn.appendChild(renderTextSafe('← Prev'));
    prevBtn.addEventListener('click', () => {
      if (_currentPage > 1) {
        _currentPage--;
        loadList();
      }
    });
    inner.appendChild(prevBtn);

    // Page indicator
    const pageInfo = document.createElement('span');
    pageInfo.style.fontSize = '13px';
    pageInfo.style.color = '#666';
    pageInfo.appendChild(renderTextSafe(`Page ${_currentPage} of ${_totalPages}`));
    inner.appendChild(pageInfo);

    // Next button
    const nextBtn = document.createElement('button');
    nextBtn.className = 'btn btn-sm btn-secondary';
    nextBtn.disabled = _currentPage >= _totalPages;
    nextBtn.appendChild(renderTextSafe('Next →'));
    nextBtn.addEventListener('click', () => {
      if (_currentPage < _totalPages) {
        _currentPage++;
        loadList();
      }
    });
    inner.appendChild(nextBtn);

    container.appendChild(inner);
  }

  // ─── Debounced Search ─────────────────────────────────────

  let _searchTimer = null;

  function _onSearchInput() {
    if (_searchTimer) clearTimeout(_searchTimer);
    _searchTimer = setTimeout(() => {
      _currentPage = 1;
      loadList();
    }, DEBOUNCE_MS);
  }

  // ─── Detail Panel ─────────────────────────────────────────

  /**
   * Load a supplier's full detail and render the detail panel.
   * @param {number} id - Supplier ID
   */
  async function loadDetail(id) {
    // Check dirty before navigating away from current detail
    if (currentSupplierId !== null && dirty) {
      const discard = await showConfirmPopup({
        message: 'You have unsaved changes. Discard them and view another supplier?',
        confirmText: 'Discard',
        cancelText: 'Keep editing',
        danger: true,
      });
      if (!discard) return;
    }

    _clearError('supplierDetailError');
    _clearDirty();

    const listPanel = document.getElementById('suppliersListPanel');
    const detailPanel = document.getElementById('supplierDetailPanel');
    if (listPanel) listPanel.classList.add('hidden');
    if (detailPanel) detailPanel.classList.remove('hidden');

    // Show loading state
    const content = document.getElementById('supplierDetailContent');
    if (content) {
      content.textContent = '';
      const loadingDiv = document.createElement('div');
      loadingDiv.style.textAlign = 'center';
      loadingDiv.style.padding = '40px';
      loadingDiv.style.color = '#888';
      loadingDiv.appendChild(renderTextSafe('Loading supplier details...'));
      content.appendChild(loadingDiv);
    }

    // Reset state
    currentSupplierId = id;
    _originalSupplier = null;
    _originalContacts = [];
    _originalAliases = [];
    _originalBrands = [];
    _currentContacts = [];
    _currentAliases = [];
    _currentBrands = [];

    try {
      const [supplier, contacts, aliases, brands] = await Promise.all([
        _apiGet(`/suppliers/${id}`),
        _apiGet(`/suppliers/${id}/contacts`),
        _apiGet(`/suppliers/${id}/aliases`),
        _apiGet(`/suppliers/${id}/brands`),
      ]);

      _originalSupplier = supplier;
      _originalContacts = _unwrapItems(contacts);
      _originalAliases = _unwrapItems(aliases);
      _originalBrands = _unwrapItems(brands);

      // Clone current state
      _currentContacts = _originalContacts.map(c => ({ ...c }));
      _currentAliases = _originalAliases.map(a => ({ ...a }));
      _currentBrands = _originalBrands.map(b => ({ ...b }));

      _renderDetail(supplier);
    } catch (e) {
      if (e.status === 401 && typeof showLogin === 'function') {
        showLogin();
        return;
      }
      _showError('supplierDetailError', e.message || 'Failed to load supplier details');
    }
  }

  /**
   * Render the full detail form for a supplier.
   * @param {Object} supplier
   */
  function _renderDetail(supplier) {
    const content = document.getElementById('supplierDetailContent');
    if (!content) return;
    content.textContent = '';

    // ── Back link ──
    const backLink = document.createElement('a');
    backLink.href = '#';
    backLink.style.cssText = 'display:inline-block;margin-bottom:16px;font-size:13px;color:#3498db;cursor:pointer';
    backLink.appendChild(renderTextSafe('← Back to list'));
    backLink.addEventListener('click', (e) => {
      e.preventDefault();
      _showListPanel();
    });
    content.appendChild(backLink);

    // ── Display Name ──
    // ── Identity Section ──
    const identitySection = document.createElement('div');
    identitySection.className = 'supplier-detail-section';

    const identityTitle = document.createElement('h4');
    identityTitle.className = 'supplier-section-title';
    identityTitle.appendChild(renderTextSafe('Identity'));
    identitySection.appendChild(identityTitle);

    const dnLabel = document.createElement('label');
    dnLabel.className = 'supplier-field-label';
    dnLabel.appendChild(renderTextSafe('Display Name *'));
    identitySection.appendChild(dnLabel);

    const dnInput = document.createElement('input');
    dnInput.type = 'text';
    dnInput.id = 'supplierDisplayName';
    dnInput.className = 'supplier-text-input';
    dnInput.value = supplier.display_name || '';
    dnInput.required = true;
    dnInput.addEventListener('input', () => _setDirty());
    identitySection.appendChild(dnInput);

    // ── Canonical Name (read-only) ──
    const cnLabel = document.createElement('label');
    cnLabel.className = 'supplier-field-label';
    cnLabel.appendChild(renderTextSafe('Supplier Name (matching key)'));
    identitySection.appendChild(cnLabel);

    const cnInput = document.createElement('input');
    cnInput.type = 'text';
    cnInput.className = 'supplier-text-input';
    cnInput.value = supplier.canonical_name || '';
    cnInput.readOnly = true;
    cnInput.style.background = '#f5f5f5';
    cnInput.style.color = '#888';
    identitySection.appendChild(cnInput);

    // ── Status ──
    const stLabel = document.createElement('label');
    stLabel.className = 'supplier-field-label';
    stLabel.appendChild(renderTextSafe('Status'));
    identitySection.appendChild(stLabel);

    const stSelect = document.createElement('select');
    stSelect.id = 'supplierStatus';
    stSelect.className = 'supplier-text-input';

    const statuses = ['active', 'inactive'];
    // Master sees 'review' option
    if (typeof isMaster === 'function' && isMaster()) {
      statuses.push('review');
    }

    for (const s of statuses) {
      const opt = document.createElement('option');
      opt.value = s;
      opt.appendChild(renderTextSafe(s.charAt(0).toUpperCase() + s.slice(1)));
      if (s === (supplier.status || 'active')) opt.selected = true;
      stSelect.appendChild(opt);
    }
    stSelect.addEventListener('change', () => _setDirty());
    identitySection.appendChild(stSelect);

    content.appendChild(identitySection);

    // ── Aliases Section ──
    const aliasesSection = document.createElement('div');
    aliasesSection.className = 'supplier-detail-section';

    const aliasesTitle = document.createElement('h4');
    aliasesTitle.className = 'supplier-section-title';
    aliasesTitle.appendChild(renderTextSafe('Aliases'));
    aliasesSection.appendChild(aliasesTitle);

    const aliasesDesc = document.createElement('p');
    aliasesDesc.className = 'supplier-section-desc';
    aliasesDesc.appendChild(renderTextSafe('Alternative spellings or company names used for this supplier.'));
    aliasesSection.appendChild(aliasesDesc);

    const aliasesContainer = document.createElement('div');
    aliasesContainer.id = 'supplierAliasesContainer';
    aliasesContainer.className = 'tag-chip-container';
    aliasesSection.appendChild(aliasesContainer);

    // Alias input + add button
    const aliasInputRow = document.createElement('div');
    aliasInputRow.style.display = 'flex';
    aliasInputRow.style.gap = '8px';
    aliasInputRow.style.marginTop = '8px';

    const aliasInput = document.createElement('input');
    aliasInput.type = 'text';
    aliasInput.id = 'supplierAliasInput';
    aliasInput.className = 'supplier-text-input';
    aliasInput.style.flex = '1';
    aliasInput.placeholder = 'Add alias...';
    aliasInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        _addAlias();
      }
    });
    aliasInputRow.appendChild(aliasInput);

    if (canModify()) {
      const addAliasBtn = document.createElement('button');
      addAliasBtn.className = 'btn btn-sm btn-secondary';
      addAliasBtn.appendChild(renderTextSafe('Add'));
      addAliasBtn.addEventListener('click', _addAlias);
      aliasInputRow.appendChild(addAliasBtn);
    }
    aliasesSection.appendChild(aliasInputRow);

    // Alias suggestions container
    const suggestionsContainer = document.createElement('div');
    suggestionsContainer.id = 'supplierAliasSuggestions';
    suggestionsContainer.style.cssText = 'margin-top:6px;display:flex;flex-wrap:wrap;gap:4px';
    aliasesSection.appendChild(suggestionsContainer);

    // Load suggestions (non-blocking)
    _loadAliasSuggestions(supplier.id, suggestionsContainer);

    content.appendChild(aliasesSection);

    // ── Contacts Section ──
    const contactsSection = document.createElement('div');
    contactsSection.className = 'supplier-detail-section';

    const contactsTitle = document.createElement('h4');
    contactsTitle.className = 'supplier-section-title';
    contactsTitle.appendChild(renderTextSafe('Contacts'));
    contactsSection.appendChild(contactsTitle);

    const contactsDesc = document.createElement('p');
    contactsDesc.className = 'supplier-section-desc';
    contactsDesc.appendChild(renderTextSafe('People at this supplier you communicate with.'));
    contactsSection.appendChild(contactsDesc);

    const contactsContainer = document.createElement('div');
    contactsContainer.id = 'supplierContactsContainer';
    contactsSection.appendChild(contactsContainer);

    if (canModify()) {
      const addContactBtn = document.createElement('button');
      addContactBtn.className = 'btn btn-sm btn-secondary';
      addContactBtn.appendChild(renderTextSafe('+ Add Contact'));
      addContactBtn.addEventListener('click', () => {
        _currentContacts.push({
          id: null,
          name: '',
          email: '',
          phone: '',
          role: '',
          position: (_currentContacts.length + 1) * 10,
          is_default_rfq_contact: false,
        });
        _renderContacts();
        _setDirty();
      });
      contactsSection.appendChild(addContactBtn);
    }

    content.appendChild(contactsSection);

    // ── Brands Section ──
    const brandsSection = document.createElement('div');
    brandsSection.className = 'supplier-detail-section';

    const brandsTitle = document.createElement('h4');
    brandsTitle.className = 'supplier-section-title';
    brandsTitle.appendChild(renderTextSafe('Brands'));
    brandsSection.appendChild(brandsTitle);

    const brandsDesc = document.createElement('p');
    brandsDesc.className = 'supplier-section-desc';
    brandsDesc.appendChild(renderTextSafe('The brands this supplier represents.'));
    brandsSection.appendChild(brandsDesc);

    const brandsContainer = document.createElement('div');
    brandsContainer.id = 'supplierBrandsContainer';
    brandsContainer.className = 'tag-chip-container';
    brandsSection.appendChild(brandsContainer);

    const brandInputRow = document.createElement('div');
    brandInputRow.style.display = 'flex';
    brandInputRow.style.gap = '8px';
    brandInputRow.style.marginTop = '8px';
    brandInputRow.style.position = 'relative';

    const brandInput = document.createElement('input');
    brandInput.type = 'text';
    brandInput.id = 'supplierBrandInput';
    brandInput.className = 'supplier-text-input';
    brandInput.style.flex = '1';
    brandInput.placeholder = 'Type brand name... (use ; to add multiple)';
    brandInput.autocomplete = 'off';
    brandInput.addEventListener('input', () => _debouncedAutocomplete('brands'));
    brandInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        _addBrandFromInput();
      }
    });
    brandInputRow.appendChild(brandInput);

    const brandDropdown = document.createElement('div');
    brandDropdown.id = 'supplierBrandAutocomplete';
    brandDropdown.className = 'autocomplete-dropdown hidden';
    brandInputRow.appendChild(brandDropdown);

    if (canModify()) {
      const addBtn = document.createElement('button');
      addBtn.className = 'btn btn-sm btn-secondary';
      addBtn.style.whiteSpace = 'nowrap';
      addBtn.appendChild(renderTextSafe('Add'));
      addBtn.addEventListener('click', () => _addBrandFromInput());
      brandInputRow.appendChild(addBtn);

      const scanBtn = document.createElement('button');
      scanBtn.className = 'btn btn-sm btn-secondary';
      scanBtn.style.whiteSpace = 'nowrap';
      scanBtn.appendChild(renderTextSafe('Scan'));
      scanBtn.addEventListener('click', () => _scanBrands());
      brandInputRow.appendChild(scanBtn);
    }

    brandsSection.appendChild(brandInputRow);
    content.appendChild(brandsSection);

    // ── Notes ──
    const notesSection = document.createElement('div');
    notesSection.className = 'supplier-detail-section';

    const notesTitle = document.createElement('h4');
    notesTitle.className = 'supplier-section-title';
    notesTitle.appendChild(renderTextSafe('Notes'));
    notesSection.appendChild(notesTitle);

    const notesDesc = document.createElement('p');
    notesDesc.className = 'supplier-section-desc';
    notesDesc.appendChild(renderTextSafe('Internal notes about this supplier.'));
    notesSection.appendChild(notesDesc);

    const notesTextarea = document.createElement('textarea');
    notesTextarea.id = 'supplierNotes';
    notesTextarea.className = 'supplier-text-input';
    notesTextarea.style.minHeight = '80px';
    notesTextarea.style.resize = 'vertical';
    notesTextarea.value = supplier.notes || '';
    notesTextarea.addEventListener('input', () => _setDirty());
    notesSection.appendChild(notesTextarea);

    content.appendChild(notesSection);

    // ── Save button ──
    const actionsDiv = document.createElement('div');
    actionsDiv.className = 'supplier-detail-actions';

    const saveBtn = document.createElement('button');
    saveBtn.id = 'supplierSaveBtn';
    saveBtn.className = 'btn btn-primary';
    saveBtn.disabled = true;
    saveBtn.appendChild(renderTextSafe('Save'));
    saveBtn.addEventListener('click', _saveSupplier);
    actionsDiv.appendChild(saveBtn);

    // ── Purge button (Master only, hidden for inactive) ──
    const purgeBtn = document.createElement('button');
    purgeBtn.id = 'supplierPurgeBtn';
    purgeBtn.className = 'btn btn-danger';
    purgeBtn.style.marginLeft = '8px';
    purgeBtn.appendChild(renderTextSafe('Purge Supplier'));
    const sStatus = (supplier.status || 'active');
    if (typeof isMaster === 'function' && isMaster() && sStatus !== 'inactive') {
      purgeBtn.classList.remove('hidden');
    } else {
      purgeBtn.classList.add('hidden');
    }
    purgeBtn.addEventListener('click', () => _purgeSupplier(supplier));
    actionsDiv.appendChild(purgeBtn);

    // ── Merge button (Admin/Master only) ──
    if (canModify()) {
      const mergeBtn = document.createElement('button');
      mergeBtn.className = 'btn btn-sm btn-secondary';
      mergeBtn.style.marginLeft = '8px';
      mergeBtn.appendChild(renderTextSafe('Merge into...'));
      mergeBtn.addEventListener('click', () => _mergeSupplier(supplier));
      actionsDiv.appendChild(mergeBtn);
    }

    content.appendChild(actionsDiv);

    // Render sub-components
    _renderAliases();
    _renderContacts();
    _renderBrands();
  }

  // ─── Aliases ──────────────────────────────────────────────

  async function _loadAliasSuggestions(supplierId, container) {
    try {
      const resp = await _apiGet(`/suppliers/${supplierId}/alias-suggestions`);
      const items = resp.items || [];
      if (items.length === 0) return;
      const label = document.createElement('span');
      label.style.cssText = 'font-size:11px;color:#888;width:100%';
      label.appendChild(renderTextSafe('Suggestions from quotations:'));
      container.appendChild(label);
      for (const name of items) {
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'btn btn-sm btn-secondary';
        chip.style.cssText = 'font-size:11px;padding:2px 6px';
        chip.appendChild(renderTextSafe(name));
        chip.addEventListener('click', () => {
          _currentAliases.push({ id: null, alias_name: name });
          _renderAliases();
          _setDirty();
          chip.remove();
        });
        container.appendChild(chip);
      }
    } catch (e) {
      // Silently ignore — suggestions are non-critical
    }
  }

  function _addAlias() {
    const input = document.getElementById('supplierAliasInput');
    if (!input) return;
    const name = input.value.trim();
    if (!name) return;
    _currentAliases.push({ id: null, alias_name: name });
    _renderAliases();
    _setDirty();
    input.value = '';
    input.focus();
  }

  async function _removeAlias(index) {
    const ok = await showConfirmPopup({
      message: 'Remove this alias?',
      confirmText: 'Remove',
      cancelText: 'Cancel',
      danger: true,
    });
    if (!ok) return;
    _currentAliases.splice(index, 1);
    _renderAliases();
    _setDirty();
  }

  function _renderAliases() {
    const container = document.getElementById('supplierAliasesContainer');
    if (!container) return;
    container.textContent = '';

    if (_currentAliases.length === 0) {
      const empty = document.createElement('span');
      empty.style.cssText = 'font-size:13px;color:#999';
      empty.appendChild(renderTextSafe('No aliases. Add alternative spellings or company names.'));
      container.appendChild(empty);
      return;
    }

    for (let i = 0; i < _currentAliases.length; i++) {
      const alias = _currentAliases[i];
      const chip = document.createElement('span');
      chip.className = 'tag-chip';
      chip.appendChild(renderTextSafe(alias.alias_name || alias.alias || alias.name || ''));

      if (canModify()) {
        const removeBtn = document.createElement('button');
        removeBtn.className = 'tag-chip-remove';
        removeBtn.appendChild(renderTextSafe('×'));
        removeBtn.title = 'Remove alias';
        removeBtn.addEventListener('click', () => _removeAlias(i));
        chip.appendChild(removeBtn);
      }

      container.appendChild(chip);
    }
  }

  // ─── Contacts ─────────────────────────────────────────────

  function _renderContacts() {
    const container = document.getElementById('supplierContactsContainer');
    if (!container) return;
    container.textContent = '';

    if (_currentContacts.length === 0) {
      const empty = document.createElement('p');
      empty.style.cssText = 'font-size:13px;color:#999';
      empty.appendChild(renderTextSafe('No contacts yet. Add at least one contact for this supplier.'));
      container.appendChild(empty);
      return;
    }

    // Sort by position ASC
    const sorted = [..._currentContacts].sort((a, b) => (a.position || 0) - (b.position || 0));

    for (let i = 0; i < sorted.length; i++) {
      // Find original index
      const origIndex = _currentContacts.indexOf(sorted[i]);
      if (origIndex === -1) continue;

      const contact = sorted[i];
      const row = document.createElement('div');
      row.className = 'supplier-contact-row';

      // Name
      const nameGroup = document.createElement('div');
      nameGroup.className = 'supplier-contact-name';
      const nameLabel = document.createElement('label');
      nameLabel.className = 'supplier-contact-label';
      nameLabel.appendChild(renderTextSafe('Name'));
      nameGroup.appendChild(nameLabel);
      const nameInput = document.createElement('input');
      nameInput.type = 'text';
      nameInput.className = 'supplier-text-input';
      nameInput.value = contact.name || '';
      nameInput.placeholder = 'Name';
      nameInput.addEventListener('input', () => {
        _currentContacts[origIndex].name = nameInput.value;
        _setDirty();
      });
      nameGroup.appendChild(nameInput);
      row.appendChild(nameGroup);

      // Email
      const emailGroup = document.createElement('div');
      emailGroup.className = 'supplier-contact-email';
      const emailLabel = document.createElement('label');
      emailLabel.className = 'supplier-contact-label';
      emailLabel.appendChild(renderTextSafe('Email'));
      emailGroup.appendChild(emailLabel);
      const emailInput = document.createElement('input');
      emailInput.type = 'email';
      emailInput.className = 'supplier-text-input';
      emailInput.value = contact.email || '';
      emailInput.placeholder = 'Email';
      emailInput.addEventListener('input', () => {
        _currentContacts[origIndex].email = emailInput.value;
        // Clear any previous warning on new input
        const warnEl = emailGroup.querySelector('.email-dup-warning');
        if (warnEl) warnEl.remove();
        _setFieldError(emailGroup, null);
        _setDirty();
        _updateSaveButton();
      });
      emailInput.addEventListener('blur', async () => {
        const val = emailInput.value.trim();
        if (!val) {
          _setFieldError(emailGroup, null);
          return;
        }
        if (!EMAIL_PATTERN.test(val)) {
          _setFieldError(emailGroup, 'Invalid email format');
          return;
        }
        _setFieldError(emailGroup, null);
        try {
          const resp = await _apiGet(`/suppliers/contacts/check-email?email=${encodeURIComponent(val)}&exclude_supplier_id=${currentSupplierId || ''}`);
          const usedBy = resp.used_by || [];
          if (usedBy.length > 0) {
            // Remove any existing warning first
            const existing = emailGroup.querySelector('.email-dup-warning');
            if (existing) existing.remove();
            const warnEl = document.createElement('div');
            warnEl.className = 'email-dup-warning';
            warnEl.appendChild(renderTextSafe('Already used by: ' + usedBy.map(u => u.display_name).join(', ') + '. Continue anyway?'));
            emailGroup.appendChild(warnEl);
          }
        } catch (e) {
          // Silently ignore — non-blocking
        }
      });
      emailGroup.appendChild(emailInput);
      const emailVal = (contact.email || '').trim();
      if (emailVal && !EMAIL_PATTERN.test(emailVal)) {
        _setFieldError(emailGroup, 'Invalid email format');
      }
      row.appendChild(emailGroup);

      // Phone
      const phoneGroup = document.createElement('div');
      phoneGroup.className = 'supplier-contact-phone';
      const phoneLabel = document.createElement('label');
      phoneLabel.className = 'supplier-contact-label';
      phoneLabel.appendChild(renderTextSafe('Phone'));
      phoneGroup.appendChild(phoneLabel);
      const phoneInput = document.createElement('input');
      phoneInput.type = 'text';
      phoneInput.className = 'supplier-text-input';
      phoneInput.value = contact.phone || '';
      phoneInput.placeholder = 'Phone';
      phoneInput.addEventListener('input', () => {
        _currentContacts[origIndex].phone = phoneInput.value;
        _setFieldError(phoneGroup, null);
        _setDirty();
        _updateSaveButton();
      });
      phoneInput.addEventListener('blur', () => {
        const val = phoneInput.value.trim();
        if (!val) {
          _setFieldError(phoneGroup, null);
          return;
        }
        if (!PHONE_PATTERN.test(val)) {
          _setFieldError(phoneGroup, 'Invalid phone format');
        } else {
          _setFieldError(phoneGroup, null);
        }
      });
      phoneGroup.appendChild(phoneInput);
      const phoneVal = (contact.phone || '').trim();
      if (phoneVal && !PHONE_PATTERN.test(phoneVal)) {
        _setFieldError(phoneGroup, 'Invalid phone format');
      }
      row.appendChild(phoneGroup);

      // Role
      const roleGroup = document.createElement('div');
      roleGroup.className = 'supplier-contact-role';
      const roleLabel = document.createElement('label');
      roleLabel.className = 'supplier-contact-label';
      roleLabel.appendChild(renderTextSafe('Role'));
      roleGroup.appendChild(roleLabel);
      const roleInput = document.createElement('input');
      roleInput.type = 'text';
      roleInput.className = 'supplier-text-input';
      roleInput.value = contact.role || '';
      roleInput.placeholder = 'Role';
      roleInput.addEventListener('input', () => {
        _currentContacts[origIndex].role = roleInput.value;
        _setDirty();
      });
      roleGroup.appendChild(roleInput);
      row.appendChild(roleGroup);

      // Reorder buttons (up/down)
      const reorderGroup = document.createElement('div');
      reorderGroup.className = 'supplier-contact-reorder';

      const upBtn = document.createElement('button');
      upBtn.type = 'button';
      upBtn.className = 'btn btn-sm btn-secondary';
      upBtn.appendChild(renderTextSafe('↑'));
      upBtn.title = 'Move up';
      upBtn.disabled = (origIndex === 0);
      upBtn.addEventListener('click', () => {
        if (origIndex <= 0) return;
        const temp = _currentContacts[origIndex];
        _currentContacts[origIndex] = _currentContacts[origIndex - 1];
        _currentContacts[origIndex - 1] = temp;
        _reindexContacts();
        _renderContacts();
        _setDirty();
      });
      reorderGroup.appendChild(upBtn);

      const downBtn = document.createElement('button');
      downBtn.type = 'button';
      downBtn.className = 'btn btn-sm btn-secondary';
      downBtn.appendChild(renderTextSafe('↓'));
      downBtn.title = 'Move down';
      downBtn.disabled = (origIndex >= _currentContacts.length - 1);
      downBtn.addEventListener('click', () => {
        if (origIndex >= _currentContacts.length - 1) return;
        const temp = _currentContacts[origIndex];
        _currentContacts[origIndex] = _currentContacts[origIndex + 1];
        _currentContacts[origIndex + 1] = temp;
        _reindexContacts();
        _renderContacts();
        _setDirty();
      });
      reorderGroup.appendChild(downBtn);
      row.appendChild(reorderGroup);

      // Default RFQ checkbox
      const rfqGroup = document.createElement('div');
      rfqGroup.className = 'supplier-contact-rfq';
      const rfqCb = document.createElement('input');
      rfqCb.type = 'checkbox';
      rfqCb.id = `contactDefaultRfq_${origIndex}`;
      rfqCb.checked = !!contact.is_default_rfq_contact;
      rfqCb.addEventListener('change', () => {
        _currentContacts[origIndex].is_default_rfq_contact = rfqCb.checked;
        _setDirty();
      });
      rfqGroup.appendChild(rfqCb);
      const rfqLabel = document.createElement('label');
      rfqLabel.htmlFor = rfqCb.id;
      rfqLabel.className = 'supplier-contact-label';
      rfqLabel.appendChild(renderTextSafe('RFQ'));
      rfqGroup.appendChild(rfqLabel);
      row.appendChild(rfqGroup);

      // Remove button
      if (canModify()) {
        const removeBtn = document.createElement('button');
        removeBtn.className = 'btn btn-sm btn-danger';
        removeBtn.appendChild(renderTextSafe('Remove'));
        removeBtn.addEventListener('click', async () => {
          const ok = await showConfirmPopup({
            message: 'Remove this contact?',
            confirmText: 'Remove',
            cancelText: 'Cancel',
            danger: true,
          });
          if (!ok) return;
          _currentContacts.splice(origIndex, 1);
          _renderContacts();
          _setDirty();
        });
        row.appendChild(removeBtn);
      }

      container.appendChild(row);
    }
  }

  function _reindexContacts() {
    for (let i = 0; i < _currentContacts.length; i++) {
      _currentContacts[i].position = i * 10;
    }
  }

  // ─── Brands ───────────────────────────────────────────────

  function _renderBrands() {
    const container = document.getElementById('supplierBrandsContainer');
    if (!container) return;
    container.textContent = '';

    if (_currentBrands.length === 0) {
      const empty = document.createElement('span');
      empty.style.cssText = 'font-size:13px;color:#999';
      empty.appendChild(renderTextSafe('No brands yet. Add the brands this supplier represents.'));
      container.appendChild(empty);
      return;
    }

    for (let i = 0; i < _currentBrands.length; i++) {
      const brand = _currentBrands[i];
      const chip = document.createElement('span');
      chip.className = 'tag-chip';
      chip.appendChild(renderTextSafe(brand.name || brand.brand_name || ''));

      if (canModify()) {
        const removeBtn = document.createElement('button');
        removeBtn.className = 'tag-chip-remove';
        removeBtn.appendChild(renderTextSafe('×'));
        removeBtn.title = 'Remove brand';
        removeBtn.addEventListener('click', async () => {
          const ok = await showConfirmPopup({
            message: 'Remove this brand?',
            confirmText: 'Remove',
            cancelText: 'Cancel',
            danger: true,
          });
          if (!ok) return;
          _currentBrands.splice(i, 1);
          _renderBrands();
          _setDirty();
        });
        chip.appendChild(removeBtn);
      }

      container.appendChild(chip);
    }
  }

  function _addBrandFromInput() {
    const input = document.getElementById('supplierBrandInput');
    if (!input) return;
    const raw = input.value.trim();
    if (!raw) return;

    // Split by ; and add each brand
    const names = raw.split(';').map(s => s.trim()).filter(Boolean);
    let added = 0;
    for (const name of names) {
      const exists = _currentBrands.some(b =>
        (b.name || b.brand_name || '').toLowerCase() === name.toLowerCase()
      );
      if (!exists) {
        _currentBrands.push({ id: null, name: name });
        added++;
      }
    }
    if (added > 0) {
      _renderBrands();
      _setDirty();
    }
    input.value = '';
    input.focus();
    _hideAutocomplete('brands');
  }

  async function _scanBrands() {
    if (!currentSupplierId) return;
    const scanBtn = document.querySelector('.supplier-detail-section:last-of-type .btn-sm');
    if (scanBtn) {
      scanBtn.disabled = true;
      scanBtn.textContent = 'Scanning...';
    }
    try {
      const resp = await _apiPost(`/suppliers/${currentSupplierId}/brands/scan`);
      if (resp.added > 0) {
        // Reload brands from server
        const brandsResp = await _apiGet(`/suppliers/${currentSupplierId}/brands`);
        _currentBrands = _unwrapItems(brandsResp).map(b => ({ ...b }));
        _renderBrands();
        _setDirty();
        const linkedMsg = resp.linked > 0 ? ` (${resp.linked} quotation(s) linked)` : '';
        showBriefPopup(`Scan complete: ${resp.added} new brand(s) added.${linkedMsg}`);
      } else {
        const linkedMsg = resp.linked > 0 ? ` (${resp.linked} quotation(s) linked)` : '';
        showBriefPopup(`Scan complete: no new brands found. (${resp.total_found} already linked)${linkedMsg}`);
      }
    } catch (e) {
      console.error('Brand scan failed:', e);
      await showAlertPopup({ message: e.message || 'Unknown error', title: 'Scan failed' });
    } finally {
      if (scanBtn) {
        scanBtn.disabled = false;
        scanBtn.textContent = 'Scan';
      }
    }
  }

  // ─── Autocomplete ─────────────────────────────────────────

  function _debouncedAutocomplete(type) {
    if (autocompleteControllers[type]) {
      autocompleteControllers[type].abort();
    }
    let timerKey = type + 'Timer';
    if (window.Suppliers[timerKey]) {
      clearTimeout(window.Suppliers[timerKey]);
    }
    window.Suppliers[timerKey] = setTimeout(() => {
      _fetchAutocomplete(type);
    }, DEBOUNCE_MS);
  }

  async function _fetchAutocomplete(type) {
    if (type !== 'brands') return;
    const inputId = 'supplierBrandInput';
    const dropdownId = 'supplierBrandAutocomplete';
    const endpoint = '/brands';

    const input = document.getElementById(inputId);
    const dropdown = document.getElementById(dropdownId);
    if (!input || !dropdown) return;

    const q = input.value.trim();
    if (q.length < AUTOCOMPLETE_MIN_CHARS) {
      _hideAutocomplete(type);
      return;
    }

    const controller = new AbortController();
    autocompleteControllers[type] = controller;

    try {
      const url = `${endpoint}?q=${encodeURIComponent(q)}&limit=${AUTOCOMPLETE_MAX_RESULTS}`;
      const resp = await fetch(url, {
        credentials: 'include',
        signal: controller.signal,
      });
      if (resp.status === 401) {
        if (typeof showLogin === 'function') showLogin();
        return;
      }
      if (!resp.ok) return;
      const data = await resp.json();
      const items = Array.isArray(data) ? data : (data.brands || []);
      _renderAutocomplete(dropdownId, items, type);
    } catch (e) {
      // AbortError is expected, ignore
      if (e.name !== 'AbortError') {
        console.warn('Autocomplete error:', e.message);
      }
    }
  }

  function _renderAutocomplete(dropdownId, items, type) {
    const dropdown = document.getElementById(dropdownId);
    if (!dropdown) return;
    dropdown.textContent = '';

    if (!items || items.length === 0) {
      dropdown.classList.add('hidden');
      return;
    }

    dropdown.classList.remove('hidden');

    for (const item of items) {
      const div = document.createElement('div');
      div.className = 'autocomplete-item';
      const name = item.name || item.brand_name || item.product_type_name || '';
      div.appendChild(renderTextSafe(name));
      div.addEventListener('click', () => {
        if (type === 'brands') {
          const exists = _currentBrands.some(b =>
            (b.name || b.brand_name || '').toLowerCase() === name.toLowerCase()
          );
          if (!exists) {
            _currentBrands.push({ id: null, name: name });
            _renderBrands();
            _setDirty();
          }
        }
        document.getElementById(type === 'brands' ? 'supplierBrandInput' : null).value = '';
        _hideAutocomplete(type);
      });
      dropdown.appendChild(div);
    }
  }

  function _hideAutocomplete(type) {
    const id = type === 'brands' ? 'supplierBrandAutocomplete' : null;
    if (!id) return;
    const el = document.getElementById(id);
    if (el) el.classList.add('hidden');
  }

  // Hide dropdown on click outside
  document.addEventListener('click', function (e) {
    const brandDropdown = document.getElementById('supplierBrandAutocomplete');
    const brandInput = document.getElementById('supplierBrandInput');
    if (brandDropdown && !brandDropdown.classList.contains('hidden')) {
      if (brandInput && !brandInput.contains(e.target) && !brandDropdown.contains(e.target)) {
        brandDropdown.classList.add('hidden');
      }
    }
  });

  // ─── Sync step helper (isolated error handling) ──────────

  /**
   * Run a sync step with isolated error handling.
   * Critical errors (401, 403) propagate. Non-critical errors (404, 409, 422, 500)
   * are logged as warnings and the step is skipped — subsequent steps still run.
   * Returns true if the step succeeded, false otherwise.
   */
  async function _syncStep(label, fn) {
    try {
      await fn();
      return true;
    } catch (e) {
      if (e.status === 401) throw e;
      if (e.status === 403) throw e;
      console.warn(`[suppliers] Sync step "${label}" failed, skipping:`, e.message);
      return false;
    }
  }

  // ─── Save ─────────────────────────────────────────────────

  async function _saveSupplier() {
    const errorEl = document.getElementById('supplierDetailError');
    if (errorEl) errorEl.classList.add('hidden');

    const saveBtn = document.getElementById('supplierSaveBtn');
    if (saveBtn) {
      saveBtn.disabled = true;
      saveBtn.textContent = 'Saving...';
    }

    let failedSteps = [];

    try {
      // ── Validate ──
      const displayName = document.getElementById('supplierDisplayName');
      if (!displayName || !displayName.value.trim()) {
        throw new Error('Display Name is required.');
      }

      // Basic email validation (UX only)
      for (const contact of _currentContacts) {
        if (contact.email) {
          const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
          if (!emailRegex.test(contact.email)) {
            throw new Error(`Invalid email format for contact "${contact.name || '(unnamed)'}".`);
          }
        }
      }

      // ── 1. Save supplier main fields ──
      const statusSelect = document.getElementById('supplierStatus');
      const notesTextarea = document.getElementById('supplierNotes');

      const supplierData = {
        display_name: displayName.value.trim(),
        status: statusSelect ? statusSelect.value : 'active',
        notes: notesTextarea ? notesTextarea.value : '',
      };

      const updatedSupplier = await _apiPut(`/suppliers/${currentSupplierId}`, supplierData);

      // ── 2. Sync contacts ──
      if (!await _syncStep('contacts', async () => {
        // Removed contacts (in original but not in current)
        for (const origC of _originalContacts) {
          if (origC.id && !_currentContacts.some(c => c.id === origC.id)) {
            await _apiDelete(`/suppliers/${currentSupplierId}/contacts/${origC.id}`);
          }
        }

        // Added or updated contacts
        for (const contact of _currentContacts) {
          const body = {
            name: contact.name || '',
            email: contact.email || '',
            phone: contact.phone || '',
            role: contact.role || '',
            position: contact.position || 0,
            is_default_rfq_contact: !!contact.is_default_rfq_contact,
          };
          if (contact.id) {
            await _apiPut(`/suppliers/${currentSupplierId}/contacts/${contact.id}`, body);
          } else {
            await _apiPost(`/suppliers/${currentSupplierId}/contacts`, body);
          }
        }
      })) {
        failedSteps.push('contacts');
      }

      // ── 3. Sync aliases ──
      if (!await _syncStep('aliases', async () => {
        for (const origA of _originalAliases) {
          if (origA.id && !_currentAliases.some(a => a.id === origA.id)) {
            await _apiDelete(`/suppliers/${currentSupplierId}/aliases/${origA.id}`);
          }
        }
        for (const alias of _currentAliases) {
          if (!alias.id) {
            await _apiPost(`/suppliers/${currentSupplierId}/aliases`, { alias: alias.alias_name || alias.alias || '' });
          }
        }
      })) {
        failedSteps.push('aliases');
      }

      // ── 4. Sync brands ──
      if (!await _syncStep('brands', async () => {
        // Remove brands no longer in current list
        for (const origB of _originalBrands) {
          if (origB.id && !_currentBrands.some(b => b.id === origB.id)) {
            await _apiDelete(`/suppliers/${currentSupplierId}/brands/${origB.id}`);
          }
        }
        // Add new brands (no id yet)
        for (const brand of _currentBrands) {
          if (!brand.id) {
            await _apiPost(`/suppliers/${currentSupplierId}/brands`, { name: brand.name || '' });
          }
        }
      })) {
        failedSteps.push('brands');
      }

      // ── 5. Clear dirty flag & reload from server ──
      _clearDirty();
      await loadDetail(currentSupplierId);
      let successMsg = 'Supplier saved';
      if (failedSteps.length) {
        successMsg += ' Some sub-resources could not be saved: ' + failedSteps.join(', ') + '. See console for details.';
      }
      _showSuccess(successMsg);
      // Refresh review badge in case status changed
      updateReviewBadge();
    } catch (e) {
      if (e.status === 401 && typeof showLogin === 'function') {
        showLogin();
        return;
      }
      let msg = e.message || 'Something went wrong. Please try again.';
      if (e.status === 403) {
        msg = 'You do not have permission for this action.';
      } else if (e.status === 409) {
        msg = 'Duplicate value (e.g. supplier name or alias already exists).';
      } else if (e.status >= 500) {
        msg = 'Something went wrong. Please try again.';
      }
      _showError('supplierDetailError', msg);
      if (saveBtn) {
        saveBtn.disabled = false;
        saveBtn.textContent = 'Save';
      }
    }
  }

  // ─── Purge Supplier ──────────────────────────────────────

  /**
   * Permanently delete a supplier (Master only).
   * Shows a confirm dialog, then calls DELETE /suppliers/{id}/purge.
   * On success, navigates back to the list and marks it stale.
   */
  async function _purgeSupplier(supplier) {
    const name = supplier.display_name || supplier.canonical_name || 'unnamed';
    const ok = await showConfirmPopup({
      message: 'Permanently purge supplier \'' + name + '\'?\n\n' +
        'This will permanently delete the supplier and all its contacts, aliases, and brands.\n' +
        'A snapshot will be stored in the audit log.\n\n' +
        'This action cannot be undone.',
      confirmText: 'Purge',
      cancelText: 'Cancel',
      danger: true,
    });
    if (!ok) return;

    _clearError('supplierDetailError');

    const purgeBtn = document.getElementById('supplierPurgeBtn');
    if (purgeBtn) purgeBtn.disabled = true;

    try {
      const data = await _apiDelete('/suppliers/' + supplier.id + '/purge');
      _showSuccess(data.detail || 'Supplier purged.');
      _listStale = true;
      _showListPanel();
    } catch (e) {
      if (e.status === 401 && typeof showLogin === 'function') {
        showLogin();
        return;
      }
      let msg = e.message || 'Failed to purge supplier.';
      if (e.status === 403) {
        msg = 'Permission denied. Only master can purge suppliers.';
      } else if (e.status === 404) {
        msg = 'Supplier not found.';
      } else if (e.status === 409) {
        // Backend sends an actionable message — surface it directly.
        msg = e.message;
      } else if (e.status >= 500) {
        msg = 'Something went wrong. Please try again.';
      }
      _showError('supplierDetailError', msg);
      if (purgeBtn) purgeBtn.disabled = false;
    }
  }

  async function _mergeSupplier(source) {
    const sourceName = source.display_name || source.canonical_name || 'unnamed';
    const targetName = await showPromptPopup({
      message: 'Merge "' + sourceName + '" into which supplier?\nEnter the target supplier name:',
      confirmText: 'Search',
    });
    if (!targetName || !targetName.trim()) return;

    _clearError('supplierDetailError');

    try {
      // Find target supplier by searching
      const searchResp = await _apiGet(`/suppliers?q=${encodeURIComponent(targetName.trim())}&per_page=10`);
      const items = searchResp.items || [];
      if (items.length === 0) {
        await showAlertPopup({ message: 'No supplier found matching "' + targetName.trim() + '".' });
        return;
      }

      let target;
      if (items.length === 1) {
        target = items[0];
      } else {
        // Multiple matches — let user pick
        const list = items.map((s, i) => `${i + 1}. ${s.display_name || s.canonical_name}`).join('\n');
        const pick = await showPromptPopup({
          message: 'Multiple suppliers found:\n\n' + list + '\n\nEnter the number of the target:',
          confirmText: 'Select',
        });
        if (!pick || isNaN(pick)) return;
        target = items[parseInt(pick, 10) - 1];
      }

      if (!target) {
        await showAlertPopup({ message: 'Invalid selection.' });
        return;
      }

      if (target.id === source.id) {
        await showAlertPopup({ message: 'Cannot merge a supplier into itself.' });
        return;
      }

      const targetDispName = target.display_name || target.canonical_name;
      const ok = await showConfirmPopup({
        message: 'Merge "' + sourceName + '" into "' + targetDispName + '"?\n\nAll quotations, contacts, aliases, and brands will be transferred. The source supplier will be deleted.',
        confirmText: 'Merge',
        cancelText: 'Cancel',
      });
      if (!ok) return;

      const resp = await _apiPost(`/suppliers/${source.id}/merge/${target.id}`);
      _showSuccess('Suppliers merged');
      _listStale = true;
      updateReviewBadge();
      await loadDetail(target.id);
    } catch (e) {
      if (e.status === 401 && typeof showLogin === 'function') {
        showLogin();
        return;
      }
      let msg = e.message || 'Failed to merge suppliers.';
      if (e.status === 403) {
        msg = 'Permission denied.';
      } else if (e.status === 404) {
        msg = 'Supplier not found.';
      } else if (e.status >= 500) {
        msg = 'Something went wrong. Please try again.';
      }
      _showError('supplierDetailError', msg);
    }
  }

  // ─── New Supplier ─────────────────────────────────────────

  /**
   * Create a new supplier and navigate to its detail.
   * On 409 (duplicate), alerts the user and re-prompts so they can
   * choose a different name or cancel.
   */
  async function newSupplier() {
    if (dirty) {
      const ok = await showConfirmPopup({
        message: 'You have unsaved changes. Discard them?',
        confirmText: 'Discard',
        cancelText: 'Keep editing',
        danger: true,
      });
      if (!ok) return;
    }

    _clearError('suppliersListError');

    // Loop until the user cancels or creates successfully
    while (true) {
      const name = await showPromptPopup({
        message: 'Enter supplier name:',
        confirmText: 'Create',
      });
      if (name === null || !name.trim()) return; // user cancelled

      try {
        const result = await _apiPost('/suppliers', { name: name });
        if (result && result.id) {
          _listStale = true;
          await loadDetail(result.id);
          _showSuccess('New supplier created.');
          return; // success, exit loop
        }
      } catch (e) {
        if (e.status === 401 && typeof showLogin === 'function') {
          showLogin();
          return;
        }
        if (e.status === 409) {
          // Duplicate — tell the user and re-prompt
          await showAlertPopup({ message: 'A supplier with this name already exists. Please choose a different name.' });
          continue; // back to prompt
        }
        // Other errors (network, 5xx, etc.)
        let msg = e.message || 'Failed to create supplier.';
        _showError('suppliersListError', msg);
        return;
      }
    }
  }

  // ─── Panel Navigation ─────────────────────────────────────

  async function _showListPanel() {
    if (dirty) {
      const ok = await showConfirmPopup({
        message: 'You have unsaved changes. Discard them?',
        confirmText: 'Discard',
        cancelText: 'Keep editing',
        danger: true,
      });
      if (!ok) return;
    }
    _clearDirty();
    currentSupplierId = null;
    const listPanel = document.getElementById('suppliersListPanel');
    const detailPanel = document.getElementById('supplierDetailPanel');
    if (listPanel) listPanel.classList.remove('hidden');
    if (detailPanel) detailPanel.classList.add('hidden');
    // If we just created a new supplier, reset to page 1; otherwise
    // preserve current pagination so the user doesn't lose their place.
    if (_listStale) {
      _currentPage = 1;
      _listStale = false;
    }
    // Always re-fetch from the backend to get fresh data.
    _clearError('suppliersListError');
    loadList();
  }

  // ─── Public API ───────────────────────────────────────────

  // ─── Review Badge ────────────────────────────────────────

  async function updateReviewBadge() {
    try {
      const data = await _apiGet('/suppliers?status=review&per_page=1');
      const badge = document.getElementById('suppliersReviewBadge');
      if (!badge) return;
      const count = data.total || 0;
      if (count > 0) {
        badge.textContent = count;
        badge.classList.remove('hidden');
      } else {
        badge.classList.add('hidden');
      }
    } catch (e) {
      // Silently ignore — badge is non-critical
    }
  }

  return {
    // State (read-only access for nav guard)
    get dirty() { return dirty; },
    get currentSupplierId() { return currentSupplierId; },

    // Constants
    DEBOUNCE_MS: DEBOUNCE_MS,
    AUTOCOMPLETE_MIN_CHARS: AUTOCOMPLETE_MIN_CHARS,
    AUTOCOMPLETE_MAX_RESULTS: AUTOCOMPLETE_MAX_RESULTS,

    // Safe render helper (exported for testability)
    renderTextSafe: renderTextSafe,

    // Public methods
    loadList: loadList,
    loadDetail: loadDetail,
    newSupplier: newSupplier,
    updateReviewBadge: updateReviewBadge,

    // Internal (exposed for inline handlers + testability)
    _onSearchInput: _onSearchInput,
    _showListPanel: _showListPanel,
    _setDirty: _setDirty,
    _clearDirty: _clearDirty,
    _listStale: _listStale,
  };

})();
