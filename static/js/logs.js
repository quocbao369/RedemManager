function loadLogs() {
    const workflowId = document.getElementById('log-filter-workflow').value;
    const instanceId = document.getElementById('log-filter-instance').value;
    const level = document.getElementById('log-filter-level').value;
    const limit = parseInt(document.getElementById('log-filter-limit').value) || 200;

    const params = new URLSearchParams();
    if (workflowId) params.append('workflow_id', workflowId);
    if (instanceId) params.append('instance_id', instanceId);
    if (level) params.append('level', level);
    params.append('limit', limit.toString());

    app.api(`/api/logs?${params.toString()}`)
        .then(renderLogs)
        .catch(e => app.showToast(e.message, 'error'));
}

function renderLogs(logs) {
    const container = document.getElementById('log-content');
    if (!container) return;

    // Entries arrive newest-first, so the top is what the user cares about.
    // Keep their position while auto-refreshing unless they were at the top.
    const prevTop = container.scrollTop;
    const atTop = prevTop < 40;

    if (!logs.length) {
        container.innerHTML = '<div class="log-entry"><span class="log-message text-muted">No logs found</span></div>';
        return;
    }

    container.innerHTML = logs.map(log => `
        <div class="log-entry">
            <span class="log-time">${app.formatDate(log.created_at)}</span>
            <span class="log-level ${log.level}">${log.level}</span>
            <span class="log-message">${escapeHtml(log.message)}</span>
            <span class="log-meta">
                ${log.workflow_id ? `WF:${log.workflow_id}` : ''}
                ${log.instance_id ? ` Inst:${log.instance_id}` : ''}
            </span>
        </div>
    `).join('');

    container.scrollTop = atTop ? 0 : prevTop;
}

function populateLogFilters() {
    Promise.all([
        app.api('/api/workflows'),
        app.api('/api/instances'),
    ])
    .then(([workflows, instances]) => {
        const wfSelect = document.getElementById('log-filter-workflow');
        const instSelect = document.getElementById('log-filter-instance');

        if (wfSelect) {
            wfSelect.innerHTML = '<option value="">All</option>' +
                workflows.map(w => `<option value="${w.id}">#${w.id} - Inst ${w.instance_id} (${w.status})</option>`).join('');
        }

        if (instSelect) {
            instSelect.innerHTML = '<option value="">All</option>' +
                instances.map(i => `<option value="${i.id}">${i.name} (idx ${i.index})</option>`).join('');
        }
    })
    .catch(e => console.error('Failed to populate filters:', e));
}

function resetLogFilters() {
    document.getElementById('log-filter-workflow').value = '';
    document.getElementById('log-filter-instance').value = '';
    document.getElementById('log-filter-level').value = '';
    document.getElementById('log-filter-limit').value = '200';
    loadLogs();
}

function exportLogs() {
    const workflowId = document.getElementById('log-filter-workflow').value;
    const instanceId = document.getElementById('log-filter-instance').value;
    const level = document.getElementById('log-filter-level').value;
    const limit = parseInt(document.getElementById('log-filter-limit').value) || 200;

    const params = new URLSearchParams();
    if (workflowId) params.append('workflow_id', workflowId);
    if (instanceId) params.append('instance_id', instanceId);
    if (level) params.append('level', level);
    params.append('limit', limit.toString());

    app.api(`/api/logs?${params.toString()}`)
        .then(logs => {
            const blob = new Blob([JSON.stringify(logs, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `redem-logs-${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.json`;
            a.click();
            URL.revokeObjectURL(url);
            app.showToast('Logs exported', 'success');
        })
        .catch(e => app.showToast(e.message, 'error'));
}

function clearLogs() {
    document.getElementById('log-content').innerHTML = '<div class="log-entry"><span class="log-message text-muted">Cleared - click Apply Filters to reload</span></div>';
}

// --- live updates -----------------------------------------------------------
let logTimer = null;

function toggleAutoRefresh(on) {
    const el = document.getElementById('log-auto-refresh');
    const live = el ? el.checked : false;
    if (logTimer) {
        clearInterval(logTimer);
        logTimer = null;
    }
    const badge = document.getElementById('log-live-badge');
    if (live) {
        loadLogs();
        logTimer = setInterval(loadLogs, 5000);
        if (badge) badge.style.display = 'inline-block';
    } else if (badge) {
        badge.style.display = 'none';
    }
    return live;
}

// Init is driven by app.js (full page load + first SPA visit). When the
// user navigates away App.teardownLogsPage stops the live poller so it
// does not keep hitting the API while the page sits in the SPA cache, and
// App.restartCurrentPage turns it back on when they return.
function initLogsPage() {
    populateLogFilters();
    loadLogs();

    const box = document.getElementById('log-auto-refresh');
    if (box && !box.dataset.bound) {
        box.dataset.bound = '1';
        box.addEventListener('change', () => toggleAutoRefresh());
    }
    toggleAutoRefresh();
}
window.initLogsPage = initLogsPage;

function teardownLogsPage() {
    if (logTimer) {
        clearInterval(logTimer);
        logTimer = null;
    }
}
window.teardownLogsPage = teardownLogsPage;

function resumeLogsPage() {
    const box = document.getElementById('log-auto-refresh');
    if (box && box.checked) toggleAutoRefresh();
}
window.resumeLogsPage = resumeLogsPage;