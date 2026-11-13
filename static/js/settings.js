function loadSettings() {
    app.api('/api/settings')
        .then(config => {
            document.getElementById('ldplayer-path').value = config.ldplayer_path;
            document.getElementById('adb-path').value = config.adb_path;
            document.getElementById('bing-apk-path').value = config.bing_apk_path;
            document.getElementById('oxyproxy-xapk-path').value = config.oxyproxy_xapk_path;
            document.getElementById('default-timeout').value = config.default_timeout;
            document.getElementById('boot-timeout').value = config.boot_timeout;
            document.getElementById('server-port').value = config.server_port;
            document.getElementById('server-host').value = config.server_host || '127.0.0.1';
            document.getElementById('bing-order-history-url').value = config.bing_order_history_url || '';
            document.getElementById('connectivity-test-url').value = config.connectivity_test_url || '';
            document.getElementById('bing-package-name').value = config.bing_package_name || '';
            document.getElementById('oxyproxy-package-name').value = config.oxyproxy_package_name || '';
            document.getElementById('database-path').value = config.database_path || '';
            document.getElementById('rewards-db-path').value = config.rewards_db_path || '';
            document.getElementById('log-level').value = config.log_level || 'INFO';
            document.getElementById('workflow-lock-timeout').value = config.workflow_lock_timeout || 3600;
            if (document.getElementById('proxy-rotation-url')) {
                document.getElementById('proxy-rotation-url').value = config.proxy_rotation_url || '';
                document.getElementById('proxy-global').value = config.proxy_global || '';
            }
            updateConfigDisplay(config);
        })
        .catch(e => app.showToast(e.message, 'error'));
}

function updateConfigDisplay(config) {
    document.getElementById('current-config').textContent = JSON.stringify(config, null, 2);
}

function saveSettings(data) {
    app.api('/api/settings', {
        method: 'POST',
        body: JSON.stringify(data),
    })
    .then(result => {
        if (result.success) {
            app.showToast('Settings saved', 'success');
            loadSettings();
        } else {
            app.showToast(`Validation errors: ${result.errors.join(', ')}`, 'error');
        }
    })
    .catch(e => app.showToast(e.message, 'error'));
}

function validateSettings() {
    const resultsEl = document.getElementById('validation-results');
    resultsEl.innerHTML = '<div class="log-entry"><span class="log-message">Validating...</span></div>';

    app.api('/api/settings', {
        method: 'POST',
        body: JSON.stringify({}),
    })
    .then(result => {
        if (result.errors.length === 0) {
            resultsEl.innerHTML = '<div class="success">All paths are valid</div>';
            app.showToast('Validation passed', 'success');
        } else {
            resultsEl.innerHTML = result.errors.map(e => `<div class="error">${escapeHtml(e)}</div>`).join('');
            app.showToast('Validation failed', 'error');
        }
    })
    .catch(e => app.showToast(e.message, 'error'));
}

// --- File picker (Database & Logging section) ------------------------------
// The dashboard server runs on this machine, so it lists real directories
// for the picker (browsers never expose full paths from <input type="file">,
// which the settings forms need in plain text).
// Fields that auto-load their data as soon as a file is confirmed in the
// picker - no "Load" button press needed.
const AUTO_LOAD_TARGETS = new Set(['database-path', 'rewards-db-path']);
let pickerTargetId = null;   // id of the settings input receiving the file
let pickerPath = '';         // directory currently listed ('' = drive roots)
let pickerParent = '';       // directory the "Up" button jumps to
let pickerSelection = '';    // file the user highlighted
let dbLoadInFlight = false;  // avoid stacking duplicate auto-loads

function openFilePicker(inputId) {
    const input = document.getElementById(inputId);
    if (!input) return;
    pickerTargetId = inputId;
    pickerSelection = '';
    const group = input.closest('.form-group');
    const label = group ? group.querySelector('label') : null;
    document.getElementById('file-picker-title').textContent = label ? label.textContent : inputId;
    document.getElementById('file-picker-selected').textContent = '';
    app.showModal('file-picker-modal');
    // Start where the field already points (empty path = drive list).
    browsePickerDir(input.value.trim());
}

function hideFilePicker() {
    app.hideModal('file-picker-modal');
}

// .db checkbox toggled: re-list the current directory with the new filter.
function refreshPickerView() {
    browsePickerDir(pickerPath);
}

// "Lên" button: go to the parent directory ('' = back to the drive list).
function browsePickerUp() {
    browsePickerDir(pickerParent);
}

// "Đi" button: jump to whatever path is in the bar.
function goPickerPath() {
    const bar = document.getElementById('file-picker-path');
    if (bar) browsePickerDir(bar.value);
}

async function browsePickerDir(path) {
    const list = document.getElementById('file-picker-list');
    if (list) list.innerHTML = '<div class="fp-note">Đang tải...</div>';
    const filter = document.getElementById('file-picker-filter');
    try {
        const params = new URLSearchParams({ path: path || '' });
        if (filter && filter.checked) params.set('exts', '.db,.sqlite,.sqlite3');
        const data = await app.api(`/api/system/browse?${params.toString()}`);
        pickerPath = data.path || '';
        pickerParent = data.parent || '';
        renderPickerEntries(data);
    } catch (e) {
        if (list) list.innerHTML = `<div class="fp-note fp-error">${escapeHtml(e.message)}</div>`;
    }
}

function renderPickerEntries(data) {
    const list = document.getElementById('file-picker-list');
    if (!list) return;
    document.getElementById('file-picker-path').value = data.path || '';

    const rows = [];
    if (data.drives) {
        rows.push('<div class="fp-note">Danh sách ổ đĩa</div>');
    } else if (data.parent) {
        rows.push(`<div class="fp-entry fp-up" data-kind="parent" data-path="${escapeHtml(data.parent)}">` +
            `<span class="fp-icon">&#128193;</span><span class="fp-name">..</span></div>`);
    }

    for (const entry of data.entries) {
        const icon = entry.kind === 'file' ? '&#128196;' : '&#128193;';
        const selected = entry.path === pickerSelection ? ' fp-selected' : '';
        rows.push(`<div class="fp-entry${selected}" data-kind="${entry.kind}" data-path="${escapeHtml(entry.path)}">` +
            `<span class="fp-icon">${icon}</span>` +
            `<span class="fp-name">${escapeHtml(entry.name)}</span>` +
            `<span class="fp-full">${escapeHtml(entry.path)}</span></div>`);
    }

    if (!data.entries.length) {
        rows.push('<div class="fp-note">Thư mục trống (hoặc không có file nào khớp bộ lọc)</div>');
    }
    list.innerHTML = rows.join('');
}

// Bind the modal's claim handlers exactly once (the modal DOM node is
// cached by the SPA navigator and survives page switches).
function bindFilePicker() {
    const list = document.getElementById('file-picker-list');
    if (list && !list.dataset.bound) {
        list.dataset.bound = '1';
        list.addEventListener('click', (e) => {
            const entry = e.target.closest('.fp-entry');
            if (!entry) return;
            if (entry.dataset.kind === 'file') {
                pickerSelection = entry.dataset.path;
                list.querySelectorAll('.fp-entry.fp-selected').forEach(el => el.classList.remove('fp-selected'));
                entry.classList.add('fp-selected');
                document.getElementById('file-picker-selected').textContent = pickerSelection;
            } else {
                // dir / drive / parent row: enter it.
                pickerSelection = '';
                document.getElementById('file-picker-selected').textContent = '';
                browsePickerDir(entry.dataset.path);
            }
        });
        // Double click a file = pick it right away.
        list.addEventListener('dblclick', (e) => {
            const entry = e.target.closest('.fp-entry');
            if (entry && entry.dataset.kind === 'file') {
                pickerSelection = entry.dataset.path;
                confirmFilePicker();
            }
        });
    }
    const bar = document.getElementById('file-picker-path');
    if (bar && !bar.dataset.bound) {
        bar.dataset.bound = '1';
        bar.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                goPickerPath();
            }
        });
    }
}

function confirmFilePicker() {
    if (!pickerSelection) {
        app.showToast('Chưa chọn file nào', 'warning');
        return;
    }
    const input = document.getElementById(pickerTargetId);
    if (input) input.value = pickerSelection;
    const target = pickerTargetId;
    hideFilePicker();
    // Picking a file IS the trigger: apply it to the running app right
    // away, no separate button press (Auto-load for the DB path fields).
    if (AUTO_LOAD_TARGETS.has(target)) loadNewDatabaseData();
}

// The "Load data mới" button next to Save in Database & Logging: applies
// whatever file the two path fields currently show to the RUNNING app
// (re-point rewards_service, drop cached app-DB connections) and reports
// what was actually loaded - no restart needed.
function loadNewDatabaseData() {
    if (dbLoadInFlight) return;
    const el = document.getElementById('db-load-results');
    const data = {};
    const dbPathEl = document.getElementById('database-path');
    const rewardsEl = document.getElementById('rewards-db-path');
    if (dbPathEl && dbPathEl.value.trim()) data.database_path = dbPathEl.value.trim();
    if (rewardsEl && rewardsEl.value.trim()) data.rewards_db_path = rewardsEl.value.trim();

    if (!Object.keys(data).length) {
        if (el) el.innerHTML = '<div class="error">Chưa có đường dẫn database nào để load.</div>';
        app.showToast('Chưa chọn file database', 'warning');
        return;
    }

    dbLoadInFlight = true;
    if (el) el.innerHTML = '<div class="log-entry"><span class="log-message">Đang tải lại dữ liệu...</span></div>';
    app.api('/api/settings/load', { method: 'POST', body: JSON.stringify(data) })
        .then(result => {
            const bits = [];
            if (result.app_db && result.app_db.changed) {
                bits.push(`App DB: <code>${escapeHtml(result.app_db.path)}</code>` +
                    (result.app_db.instances !== null ? ` - ${result.app_db.instances} instances` : ''));
            }
            if (result.rewards_db && result.rewards_db.changed) {
                bits.push(`Rewards DB: <code>${escapeHtml(result.rewards_db.path)}</code>` +
                    (result.rewards_db.accounts !== null ? ` - ${result.rewards_db.accounts} accounts` : ''));
            }
            if (result.errors && result.errors.length) {
                if (el) {
                    el.innerHTML = `<div class="error">${result.errors.map(escapeHtml).join('<br>')}` +
                        (bits.length ? `<br>${bits.join('<br>')}` : '') + '</div>';
                }
                app.showToast(`Load DB xong (có ${result.errors.length} cảnh báo)`, 'warning');
            } else {
                if (el) el.innerHTML = `<div class="success">Đã load data mới<br>${bits.join('<br>')}</div>`;
                app.showToast('Đã load data mới từ database', 'success');
            }
            loadSettings();
        })
        .catch(e => {
            if (el) el.innerHTML = `<div class="error">${escapeHtml(e.message)}</div>`;
            app.showToast(e.message, 'error');
        })
        .finally(() => {
            dbLoadInFlight = false;
        });
}


// Bind a submit handler exactly once per form element so re-running init
// on a restored page cannot queue duplicate submissions.
function bindFormOnce(id, buildPayload) {
    const form = document.getElementById(id);
    if (!form || form.dataset.bound) return;
    form.dataset.bound = '1';
    form.addEventListener('submit', (e) => {
        e.preventDefault();
        const formData = new FormData(form);
        saveSettings(buildPayload(formData));
    });
}

// Init is driven by app.js (full page load + first SPA visit); returning to
// a cached page reuses the already-bound forms on its DOM node.
function initSettingsPage() {
    bindFilePicker();
    bindFormOnce('paths-form', formData => collectEntries(formData, String));
    bindFormOnce('timeouts-form', formData => collectEntries(formData, parseInt));
    bindFormOnce('urls-form', formData => collectEntries(formData, String));
    bindFormOnce('packages-form', formData => collectEntries(formData, String));

    bindFormOnce('db-log-form', formData => {
        const data = {};
        for (const [key, value] of formData.entries()) {
            if (key === 'log_level') {
                data[key] = value;
            } else if (value) {
                data[key] = parseInt(value) || value;
            }
        }
        return data;
    });

    // server_host must stay a string; numeric fields are parsed.
    bindFormOnce('server-form', formData => {
        const data = {};
        for (const [key, value] of formData.entries()) {
            if (key === 'server_port' || key === 'workflow_lock_timeout') {
                data[key] = parseInt(value);
            } else if (value) {
                data[key] = value;
            }
        }
        return data;
    });

    const proxyForm = document.getElementById('proxy-form');
    if (proxyForm && !proxyForm.dataset.bound) {
        proxyForm.dataset.bound = '1';
        proxyForm.addEventListener('submit', (e) => {
            e.preventDefault();
            const formData = new FormData(proxyForm);
            const data = {};
            for (const [key, value] of formData.entries()) {
                if (key) data[key] = value;
            }
            app.api('/api/settings/proxy', {
                method: 'POST',
                body: JSON.stringify(data),
            })
            .then(result => {
                const el = document.getElementById('proxy-results');
                if (result.success) {
                    el.innerHTML = '<div class="success">Proxy settings saved</div>';
                    app.showToast('Proxy settings saved', 'success');
                } else {
                    el.innerHTML = `<div class="error">${escapeHtml(result.message)}</div>`;
                    app.showToast(result.message, 'error');
                }
            })
            .catch(e => app.showToast(e.message, 'error'));
        });
    }

    loadSettings();
}
window.initSettingsPage = initSettingsPage;

// FormData -> plain object; empty inputs are skipped (the original forms
// only sent the fields the user actually filled in).
function collectEntries(formData, cast) {
    const data = {};
    for (const [key, value] of formData.entries()) {
        if (value) data[key] = cast(value);
    }
    return data;
}

function rotateProxyNow() {
    const el = document.getElementById('proxy-results');
    el.innerHTML = '<div class="log-entry"><span class="log-message">Rotating...</span></div>';
    app.api('/api/settings/proxy/rotate', { method: 'POST' })
        .then(result => {
            if (result.success) {
                el.innerHTML = `<div class="success">Rotated: ${escapeHtml(result.proxy)} (ip ${escapeHtml(result.ip || '?')})</div>`;
                app.showToast('Proxy rotated: ' + result.proxy, 'success');
            } else {
                el.innerHTML = `<div class="error">${escapeHtml(result.message)}</div>`;
                app.showToast(result.message, 'error');
            }
        })
        .catch(e => app.showToast(e.message, 'error'));
}