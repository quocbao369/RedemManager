let allInstances = [];

function loadInstances() {
    app.api('/api/instances')
        .then(instances => {
            allInstances = instances;
            renderInstances();
        })
        .catch(e => {
            console.error('Failed to load instances:', e);
            app.showToast(e.message, 'error');
        });
}

// Instances matching the email search box (empty box = every instance).
// The match covers both the short LDPlayer name and the mapped account
// email, so typing a full address still finds the right row.
function getFilteredInstances() {
    const input = document.getElementById('search-instance');
    const q = (input ? input.value : '').trim().toLowerCase();
    if (!q) return allInstances;
    return allInstances.filter(i =>
        (i.name || '').toLowerCase().includes(q)
        || (i.email || '').toLowerCase().includes(q));
}

function onSearchInstance() {
    app.persistSearch();
    renderInstances();
}

function renderInstances() {
    const tbody = document.getElementById('instances-body');
    if (!tbody) return;

    const list = getFilteredInstances();
    if (!list.length) {
        tbody.innerHTML = '<tr><td colspan="11" style="text-align:center;color:var(--text-muted)">No instances found</td></tr>';
        return;
    }

    tbody.innerHTML = list.map(inst => `
        <tr>
            <td>${inst.index}</td>
            <td><span class="badge badge-${(inst.ld_version || 9) === 14 ? 'warning' : 'secondary'}">LD${inst.ld_version || 9}</span></td>
            <td>${escapeHtml(inst.name)}</td>
            <td>${inst.email
                ? `<code title="${escapeHtml(inst.email)}">${escapeHtml(inst.email)}</code>`
                : '<span class="text-muted">-</span>'}</td>
            <td><span class="badge badge-${inst.status === 'running' ? 'success' : 'secondary'}">${inst.status}</span></td>
            <td>${inst.serial ? `<code>${escapeHtml(inst.serial)}</code>` : '<span class="text-muted">-</span>'}</td>
            <td>${inst.android_version || '-'}</td>
            <td>${inst.resolution || '-'}</td>
            <td>${inst.cpu_cores || '-'}</td>
            <td>${inst.memory_mb ? `${inst.memory_mb}MB` : '-'}</td>
            <td>
                <div class="action-buttons">
                    ${inst.status === 'stopped'
                        ? `<button class="btn btn-primary btn-sm" onclick="launchInstance(${inst.id})">Start</button>`
                        : `<button class="btn btn-secondary btn-sm" onclick="quitInstance(${inst.id})">Stop</button>`
                    }
                    <button class="btn btn-secondary btn-sm" onclick="showRenameModal(${inst.id}, '${escapeHtml(inst.name)}')">Rename</button>
                    <button class="btn btn-secondary btn-sm" onclick="viewInstanceLog(${inst.id}, '${escapeHtml(inst.name)}')">Log</button>
                    <button class="btn btn-danger btn-sm" onclick="confirmRemoveInstance(${inst.id})">Remove</button>
                </div>
            </td>
        </tr>
    `).join('');
}

function launchInstance(dbId) {
    app.api(`/api/instances/${dbId}/launch`, { method: 'POST' })
        .then(() => {
            app.showToast(`Instance starting...`, 'success');
            setTimeout(loadInstances, 2000);
        })
        .catch(e => app.showToast(e.message, 'error'));
}

function quitInstance(dbId) {
    if (!confirm(`Stop instance?`)) return;
    app.api(`/api/instances/${dbId}/quit`, { method: 'POST' })
        .then(() => {
            app.showToast(`Instance stopping...`, 'success');
            setTimeout(loadInstances, 2000);
        })
        .catch(e => app.showToast(e.message, 'error'));
}

function confirmRemoveInstance(dbId) {
    if (!confirm(`Permanently remove instance? This cannot be undone.`)) return;
    app.api(`/api/instances/${dbId}/remove`, { method: 'POST' })
        .then(() => {
            app.showToast(`Instance removed`, 'success');
            loadInstances();
        })
        .catch(e => app.showToast(e.message, 'error'));
}

function showCreateInstanceModal() {
    const form = document.getElementById('create-instance-form');
    if (form) form.reset();
    // form.reset() clears values but never re-enables a locked select.
    const typeSelect = document.getElementById('ld-version-select');
    if (typeSelect) typeSelect.disabled = false;
    populateRuntimes();
    populateCloneSources();
    app.showModal('create-instance-modal');
}

// Fill the "Loai gia lap" dropdown with the LDPlayer installations that can
// actually be driven (ldconsole.exe exists). LDPlayer 14 stays selectable
// but disabled with a hint until its folder is configured in Settings.
async function populateRuntimes() {
    const select = document.getElementById('ld-version-select');
    if (!select) return;
    try {
        const { versions } = await app.api('/api/ldplayer/runtimes');
        const byVersion = {};
        (versions || []).forEach(v => { byVersion[v.version] = v; });
        Array.from(select.options).forEach(opt => {
            const info = byVersion[Number(opt.value)];
            if (info) {
                opt.disabled = !info.available;
                if (!info.available) opt.textContent = `${info.name} (chua cau hinh trong Settings)`;
                else opt.textContent = info.name;
            }
        });
        // Do not leave the selection on a disabled option.
        if (select.selectedOptions[0] && select.selectedOptions[0].disabled) {
            const firstEnabled = Array.from(select.options).find(o => !o.disabled);
            if (firstEnabled) select.value = firstEnabled.value;
        }
    } catch (err) {
        console.warn('khong tai duoc danh sach runtimes', err);
    }
}

// Picking a clone source decides the installation: the copy always lives on
// the same LDPlayer as its source, so the type select follows and locks.
function onCloneSourceChange() {
    const select = document.getElementById('clone-from-select');
    const typeSelect = document.getElementById('ld-version-select');
    if (!select || !typeSelect) return;
    if (select.value) {
        typeSelect.value = select.value.split(':')[0];
        typeSelect.disabled = true;
    } else {
        typeSelect.disabled = false;
    }
}

// Fill the "copy from" dropdown with the instances LDPlayer currently has.
// The option value carries the installation too ("14:5") because the same
// index can exist in both LDPlayer 9 and 14.
async function populateCloneSources() {
    const select = document.getElementById('clone-from-select');
    if (!select) return;
    try {
        const items = await app.api('/api/instances');
        select.innerHTML = '<option value="">-- Tao moi (khong copy) --</option>';
        (items || []).forEach((inst) => {
            const opt = document.createElement('option');
            opt.value = `${inst.ld_version || 9}:${inst.index}`;
            const running = String(inst.status).toLowerCase() === 'running';
            opt.textContent = `LD${inst.ld_version || 9}-${inst.index} - ${inst.name}`
                + (running ? ' (dang chay)' : '');
            select.appendChild(opt);
        });
    } catch (err) {
        console.warn('khong tai duoc danh sach instance', err);
    }
}

function hideCreateInstanceModal() {
    app.hideModal('create-instance-modal');
}

function showRenameModal(dbId, currentName) {
    document.getElementById('rename-instance-index').value = dbId;
    document.getElementById('rename-instance-name').value = currentName;
    app.showModal('rename-instance-modal');
}

function hideRenameModal() {
    app.hideModal('rename-instance-modal');
}

// Init is driven by app.js (full page load + first SPA visit). Returning to
// a cached page reuses the already-bound listeners on its DOM node, so the
// bind blocks below guard themselves against double-binding anyway.
function initInstancesPage() {
    const form = document.getElementById('create-instance-form');
    if (form && !form.dataset.bound) {
        form.dataset.bound = '1';
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const formData = new FormData(form);
            const name = formData.get('name') || null;
            const phoneMode = formData.get('phone_mode') === 'on';
            // "14:5" = clone index 5 from LDPlayer 14; plain "5" (legacy) = LD9.
            const rawClone = formData.get('clone_from') || '';
            let cloneFrom = null;
            let ldVersion = Number(formData.get('ld_version')) || 9;
            if (rawClone && rawClone.includes(':')) {
                const parts = rawClone.split(':');
                ldVersion = Number(parts[0]) || ldVersion;
                cloneFrom = Number(parts[1]);
            } else if (rawClone) {
                cloneFrom = Number(rawClone);
            }

            try {
                const result = await app.api('/api/instances', {
                    method: 'POST',
                    body: JSON.stringify({ name, phone_mode: phoneMode, clone_from: cloneFrom, ld_version: ldVersion }),
                });
                app.showToast(
                    cloneFrom === null
                        ? `Instance created on LD${result.ld_version || ldVersion} with index ${result.index}`
                        : `Copied from LD${ldVersion}-${cloneFrom} -> index ${result.index}`,
                    'success');
                hideCreateInstanceModal();
                loadInstances();
            } catch (err) {
                app.showToast(err.message, 'error');
            }
        });
    }

    const renameForm = document.getElementById('rename-instance-form');
    if (renameForm && !renameForm.dataset.bound) {
        renameForm.dataset.bound = '1';
        renameForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const index = document.getElementById('rename-instance-index').value;
            const name = document.getElementById('rename-instance-name').value;

            try {
                await app.api(`/api/instances/${index}/rename`, {
                    method: 'POST',
                    body: JSON.stringify({ name }),
                });
                app.showToast(`Instance renamed`, 'success');
                hideRenameModal();
                loadInstances();
            } catch (err) {
                app.showToast(err.message, 'error');
            }
        });
    }

    loadInstances();
}
window.initInstancesPage = initInstancesPage;