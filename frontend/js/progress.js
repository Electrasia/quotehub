/**
 * frontend/js/progress.js — File processing with streaming progress.
 *
 * Handles the SSE streaming from /process-stream, inline progress bars,
 * and cancellation of active processing.
 *
 * Depends on globals from:
 *   app.js   — isConnected, uploadedFiles, currentFileIndex, processing,
 *              abortController, currentFilePercent, extractedData,
 *              reviewPages, reviewCurrentPage
 *   utils.js — showBriefPopup
 *   upload.js — renderFileList, updateStepClickability
 *   review.js — showReview (called when processing completes)
 */

// ─── Process All Pages (streaming progress) ──────────────────

/**
 * Process all pending files via /process-stream with SSE progress.
 *
 * Processes the first pending file in the queue, updates inline
 * progress UI, and transitions to the review screen on completion.
 */
async function processAll() {
    if (!isConnected) { showBriefPopup('Please connect to AI server first.'); return; }
    if (uploadedFiles.length === 0) return;

    let fileIdx = uploadedFiles.findIndex(f => f.status === 'pending');
    if (fileIdx === -1) { showBriefPopup('No pending files to process.'); return; }

    const file = uploadedFiles[fileIdx];
    const fileId = file.file_id;
    currentFileIndex = fileId;

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
            body: JSON.stringify({ file_id: fileId }),
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

                        const pagesResp = await fetch(`/next-file?file_id=${encodeURIComponent(fileId)}`);
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

/**
 * Update the per-file progress row: filename + status text + bar width.
 *
 * @param {string} filename — The file being processed
 * @param {string} statusText — Human-readable status message
 * @param {number} percent — Progress percentage (0-100)
 */
function updateInlineProgress(filename, statusText, percent) {
    const label = document.getElementById('perFileLabel');
    const fill  = document.getElementById('perFileProgressFill');
    if (label) label.textContent = `${filename} — ${statusText}`;
    if (fill)  fill.style.width = `${Math.max(0, Math.min(100, percent))}%`;
}

/**
 * Compute and display weighted overall progress across all files.
 *
 * - N = total files in queue
 * - each completed file contributes 1/N
 * - currently processing file contributes (currentFilePercent/100) * (1/N)
 * - pending files contribute 0
 */
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

/**
 * Cancel active processing and reset UI state.
 *
 * Aborts any active SSE stream, resets the current file to pending,
 * hides the inline progress area, and returns to the file list.
 */
function cancelProcessing() {
    if (abortController) {
        abortController.abort();
        abortController = null;
    }
    processing = false;
    currentFilePercent = 0;
    // Reset current file status to pending
    if (currentFileIndex) {
        const fileIdx = uploadedFiles.findIndex(f => f.file_id === currentFileIndex);
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
