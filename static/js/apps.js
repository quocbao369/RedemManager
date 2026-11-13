let selectedInstances = new Set();

function loadAppsStatus() {
    app.api('/api/apps/status')
        .then(data => {
            renderBingInfo(data.bing);
            renderOxyProxyInfo(data.oxyproxy);
            loadTargetInstances();
        })
        .catch(e => app.showToast(e.message, 'error'));
}

function renderBingInfo(bing) {
    document.getElementById('bing-path').textContent = bing.path;
    document.getElementById('bing-exists').innerHTML = bing.exists
        ? '<span class="badge badge-success">Found</span>'
        : '<span class="badge badge-danger">Missing</span>';
    document.getElementById('bing-package').textContent = bing.package || '-';
    document.getElementById('bing-version').textContent = bing.version || '-';
    document.getElementById('bing-status').innerHTML = bing.valid
        ? '<span class="badge badge-success">Valid</span>'
        : `<span class="badge badge-danger">Invalid: ${escapeHtml(bing.error || 'Unknown')}</span>`;
}

function renderOxyProxyInfo(oxy) {
    document.getElementById('oxyproxy-path').textContent = oxy.path;
    document.getElementById('oxyproxy-exists').innerHTML = oxy.exists
        ? '<span class="badge badge-success">Found</span>'
        : '<span class="badge badge-danger">Missing</span>';
    document.getElementById('oxyproxy-count').textContent = oxy.apk_count;
    document.getElementById('oxyproxy-base').textContent = oxy.base_apk || '-';
    document.getElementById('oxyproxy-splits').textContent = oxy.split_count;
}

function loadTargetInstances() {
    app.api('/api/instances')
        .then(instances => {
            const tbody = document.getElementById('target-instances-body');
            if (!tbody) return;

            tbody.innerHTML = instances.map(inst => {
                const checked = selectedInstances.has(inst.index) ? 'checked' : '';
                return `
                    <tr>
                        <td><input type="checkbox" class="instance-checkbox" value="${inst.index}" ${checked} onchange="toggleInstanceSelection(${inst.index}, this.checked)"></td>
                        <td>${inst.index}</td>
                        <td>${escapeHtml(inst.name)}</td>
                        <td><span class="badge badge-${inst.status === 'running' ? 'success' : 'secondary'}">${inst.status}</span></td>
                        <td>${inst.serial ? `<code>${escapeHtml(inst.serial)}</code>` : '-'}</td>
                        <td><span class="badge badge-secondary">Unknown</span></td>
                        <td><span class="badge badge-secondary">Unknown</span></td>
                    </tr>
                `;
            }).join('');
        })
        .catch(e => app.showToast(e.message, 'error'));
}

function toggleInstanceSelection(index, checked) {
    if (checked) selectedInstances.add(index);
    else selectedInstances.delete(index);
    updateSelectAllCheckbox();
}

function updateSelectAllCheckbox() {
    const checkboxes = document.querySelectorAll('.instance-checkbox');
    const allChecked = Array.from(checkboxes).every(cb => cb.checked);
    const anyChecked = Array.from(checkboxes).some(cb => cb.checked);
    const selectAll = document.getElementById('select-all-instances');
    if (selectAll) {
        selectAll.checked = allChecked;
        selectAll.indeterminate = anyChecked && !allChecked;
    }
}

// Init is driven by app.js (full page load + first SPA visit); returning to
// a cached page reuses the already-bound checkbox on its DOM node.
function initAppsPage() {
    const selectAll = document.getElementById('select-all-instances');
    if (selectAll && !selectAll.dataset.bound) {
        selectAll.dataset.bound = '1';
        selectAll.addEventListener('change', (e) => {
            const checkboxes = document.querySelectorAll('.instance-checkbox');
            checkboxes.forEach(cb => {
                cb.checked = e.target.checked;
                toggleInstanceSelection(parseInt(cb.value), e.target.checked);
            });
        });
    }
    loadAppsStatus();
}
window.initAppsPage = initAppsPage;

function checkBingAPK() {
    app.showToast('Verifying Bing APK...', 'info');
    loadAppsStatus();
}

function analyzeOxyProxyXAPK() {
    app.showToast('Analyzing OxyProxy XAPK...', 'info');
    loadAppsStatus();
}

function installBingToSelected() {
    if (selectedInstances.size === 0) {
        app.showToast('No instances selected', 'warning');
        return;
    }

    selectedInstances.forEach(index => {
        app.api('/api/instances')
            .then(instances => instances.find(i => i.index === index))
            .then(inst => {
                if (!inst || !inst.serial) {
                    app.showToast(`Instance ${index} has no ADB serial`, 'error');
                    return;
                }
                return app.api('/api/apps/install/bing', {
                    method: 'POST',
                    body: JSON.stringify({ instance_id: inst.id }),
                });
            })
            .then(result => {
                if (result) app.showToast(`Instance ${index}: ${result.message}`, result.success ? 'success' : 'error');
            })
            .catch(e => app.showToast(`Instance ${index}: ${e.message}`, 'error'));
    });
    setTimeout(loadTargetInstances, 1000);
}

function installOxyProxyToSelected() {
    if (selectedInstances.size === 0) {
        app.showToast('No instances selected', 'warning');
        return;
    }

    selectedInstances.forEach(index => {
        app.api('/api/instances')
            .then(instances => instances.find(i => i.index === index))
            .then(inst => {
                if (!inst || !inst.serial) {
                    app.showToast(`Instance ${index} has no ADB serial`, 'error');
                    return;
                }
                return app.api('/api/apps/install/oxyproxy', {
                    method: 'POST',
                    body: JSON.stringify({ instance_id: inst.id }),
                });
            })
            .then(result => {
                if (result) app.showToast(`Instance ${index}: ${result.message}`, result.success ? 'success' : 'error');
            })
            .catch(e => app.showToast(`Instance ${index}: ${e.message}`, 'error'));
    });
    setTimeout(loadTargetInstances, 1000);
}

function launchBingOnSelected() {
    if (selectedInstances.size === 0) {
        app.showToast('No instances selected', 'warning');
        return;
    }

    selectedInstances.forEach(index => {
        app.api('/api/instances')
            .then(instances => instances.find(i => i.index === index))
            .then(inst => {
                if (!inst || !inst.serial) {
                    app.showToast(`Instance ${index} has no ADB serial`, 'error');
                    return;
                }
                return app.api('/api/apps/launch/bing', {
                    method: 'POST',
                    body: JSON.stringify({ instance_id: inst.id }),
                });
            })
            .then(result => {
                if (result) app.showToast(`Instance ${index}: ${result.message}`, result.success ? 'success' : 'error');
            })
            .catch(e => app.showToast(`Instance ${index}: ${e.message}`, 'error'));
    });
}