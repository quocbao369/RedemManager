let accountsPage = 1;
const accountsPerPage = 20;
let allAccounts = [];

function loadOverview() {
    // Sync instances to DB first, then load overview and accounts
    app.api('/api/instances/sync', { method: 'POST' })
        .catch(e => console.error('Sync failed:', e))
        .then(() => {
            return Promise.all([
                app.api('/api/overview'),
                app.api('/api/accounts')
            ]);
        })
        .then(([overview, accounts]) => {
            updateStats(overview);
            allAccounts = accounts;
            populateGroupFilter(accounts);
            accountsPage = 1;
            renderAccountsPage();
            renderPagination();
        })
        .catch(e => {
            console.error('Failed to load overview:', e);
            app.showToast('Failed to load accounts: ' + e.message, 'error');
        });
}

function updateStats(data) {
    document.getElementById('total-instances').textContent = data.total_instances || 0;
    document.getElementById('running-instances').textContent = data.running_instances || 0;
    document.getElementById('stopped-instances').textContent = data.stopped_instances || 0;
    document.getElementById('total-accounts').textContent = data.total_accounts || 0;
}

function loadAccounts() {
    const groupFilter = document.getElementById('filter-group').value;
    const sortOrder = document.getElementById('sort-points').value;
    const readyFilter = document.getElementById('filter-ready');
    const ready = readyFilter ? readyFilter.value : '';

    let url = '/api/accounts?';
    if (groupFilter) url += `group=${encodeURIComponent(groupFilter)}&`;
    if (sortOrder) url += `sort=${encodeURIComponent(sortOrder)}&`;
    if (ready) url += `ready=${encodeURIComponent(ready)}&`;
    
    app.api(url)
        .then(accounts => {
            allAccounts = accounts;
            populateGroupFilter(accounts);
            accountsPage = 1;
            renderAccountsPage();
            renderPagination();
        })
        .catch(e => console.error('Failed to load accounts:', e));
}

let allGroups = [];

function populateGroupFilter(accounts) {
    const select = document.getElementById('filter-group');
    if (!select) return;

    // Keep a master list of every group seen so filtering never shrinks the dropdown
    const seen = new Set(allGroups);
    accounts.forEach(a => {
        if (a.account_group) seen.add(a.account_group);
    });
    allGroups = Array.from(seen).sort();

    const currentValue = select.value;
    select.innerHTML = '<option value="">All Groups</option>';
    allGroups.forEach(g => {
        const opt = document.createElement('option');
        opt.value = g;
        opt.textContent = g;
        select.appendChild(opt);
    });
    // Restore the previous selection if it still exists
    select.value = allGroups.includes(currentValue) ? currentValue : '';
}

function renderAccountRow(a) {
    // Proxy: keep the column narrow. The full value is available via the
    // title attribute, and the v4/v6 badge stays visible.
    let proxyHtml = '-';
    if (a.proxy_display) {
        const proxyType = a.proxy_type === 'v6' ? 'v6' : 'v4';
        const short = `${proxyType.toUpperCase()} ${a.proxy_display.split(':')[0]}`;
        proxyHtml = `<span class="proxy-badge ${proxyType}">${proxyType.toUpperCase()}</span>`
            + ` <code class="proxy-host" title="${escapeHtml(a.proxy_display)}">${escapeHtml(short)}</code>`;
    }
    
    let instanceHtml;
    if (a.has_instance) {
        const badgeClass = a.instance_status === 'running' ? 'success' : 'secondary';
        instanceHtml = `<span class="badge badge-${badgeClass}">${escapeHtml(a.instance_name)} (idx ${a.instance_id})</span>`;
    } else {
        instanceHtml = '<span class="text-muted">No instance</span>';
    }

    let statusHtml;
    if (a.suspended) {
        statusHtml = '<span class="badge badge-danger">Suspended</span>';
    } else {
        statusHtml = '<span class="badge badge-success">Active</span>';
    }
    if (a.rank) {
        statusHtml += ` <small>(${a.rank})</small>`;
    }

    let groupHtml = a.account_group ? `<span class="group-badge">${escapeHtml(a.account_group)}</span>` : '<span class="group-badge empty">-</span>';

    let actionsHtml;
    if (a.has_instance) {
        let controlBtn;
        if (a.instance_status === 'stopped') {
            controlBtn = `<button class="btn btn-primary btn-sm" onclick="startInstance(${a.instance_id}, '${escapeHtml(a.email)}')">Open</button>`;
        } else {
            controlBtn = `<button class="btn btn-secondary btn-sm" onclick="stopInstance(${a.instance_id})">Close</button>`;
        }
            actionsHtml = `
                ${controlBtn}
                ${a.instance_status === 'running'
                    ? `<button class="btn btn-success btn-sm" onclick="collectRedeem(${a.instance_id}, '${escapeHtml(a.email)}')" title="Doc diem va don hang moi nhat">OK</button>`
                    : ''}
                <button class="btn btn-unassign" onclick="unassignInstance('${escapeHtml(a.email)}')" title="Unassign">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
                        <path d="M18 6 6 18M6 6l12 12"/>
                    </svg>
                </button>
            `;
    } else {
        actionsHtml = `<button class="btn btn-primary btn-sm" onclick="assignInstance('${escapeHtml(a.email)}')">Assign Instance</button>`;
    }

    const createdHtml = a.created_at
        ? `<small>${escapeHtml(new Date(a.created_at).toLocaleString())}</small>`
        : '<span class="text-muted">-</span>';

    // Enough points to redeem - same thresholds as the VIP dashboard.
    let pointsHtml;
    if (a.points !== null && a.points !== undefined) {
        const ready = a.ready_to_redeem;
        pointsHtml = `<span class="points-cell ${ready ? 'points-ready' : ''}" `
            + `title="${ready ? 'Du dieu de quy doi' : 'Can them ' + (a.points_needed - a.points) + ' diem'}">`
            + `${a.points.toLocaleString()}</span>`
            + (ready ? `<span class="badge badge-ready" title="Du ${a.points_needed} diem">READY</span>` : '');
    } else {
        pointsHtml = '-';
    }

    const readyClass = a.ready_to_redeem ? ' row-ready' : '';

    return `
        <tr class="${readyClass.trim()}">
            <td>${escapeHtml(a.email)}</td>
            <td>${groupHtml}</td>
            <td>${createdHtml}</td>
            <td class="col-points">${pointsHtml}</td>
            <td>${proxyHtml}</td>
            <td>${instanceHtml}</td>
            <td>${statusHtml}</td>
            <td><div class="action-buttons">${actionsHtml}</div></td>
        </tr>
    `;
}

// Accounts matching the email search box (empty box = every account).
// Filtering is client-side so it stays instant while the user types and
// keeps working together with the Group / Points / Sort filters above.
function getFilteredAccounts() {
    const input = document.getElementById('search-email');
    const q = (input ? input.value : '').trim().toLowerCase();
    if (!q) return allAccounts;
    return allAccounts.filter(a => (a.email || '').toLowerCase().includes(q));
}

function onSearchEmail() {
    app.persistSearch();
    accountsPage = 1;
    renderAccountsPage();
    renderPagination();
}

function renderAccountsPage() {
    const tbody = document.getElementById('accounts-body');
    if (!tbody) return;

    const filtered = getFilteredAccounts();
    const start = (accountsPage - 1) * accountsPerPage;
    const end = start + accountsPerPage;
    const pageAccounts = filtered.slice(start, end);

    if (!pageAccounts.length) {
        tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--text-muted)">No accounts found</td></tr>';
        return;
    }

    tbody.innerHTML = pageAccounts.map(renderAccountRow).join('');
}

function renderPagination() {
    const paginationEl = document.getElementById('accounts-pagination');
    if (!paginationEl) return;

    const totalPages = Math.ceil(getFilteredAccounts().length / accountsPerPage);
    if (totalPages <= 1) {
        paginationEl.innerHTML = '';
        return;
    }

    let html = '';
    // Previous button
    html += `<button class="btn btn-secondary btn-sm" onclick="changePage(${accountsPage - 1})" ${accountsPage === 1 ? 'disabled' : ''}>Previous</button>`;
    
    // Page numbers
    const maxVisiblePages = 5;
    let startPage = Math.max(1, accountsPage - Math.floor(maxVisiblePages / 2));
    let endPage = Math.min(totalPages, startPage + maxVisiblePages - 1);
    
    if (endPage - startPage + 1 < maxVisiblePages) {
        startPage = Math.max(1, endPage - maxVisiblePages + 1);
    }
    
    for (let i = startPage; i <= endPage; i++) {
        html += `<button class="btn ${i === accountsPage ? 'btn-primary' : 'btn-secondary'} btn-sm" onclick="changePage(${i})">${i}</button>`;
    }
    
    // Next button
    html += `<button class="btn btn-secondary btn-sm" onclick="changePage(${accountsPage + 1})" ${accountsPage === totalPages ? 'disabled' : ''}>Next</button>`;
    
    paginationEl.innerHTML = html;
}

function changePage(page) {
    const totalPages = Math.ceil(getFilteredAccounts().length / accountsPerPage);
    if (page < 1 || page > totalPages) return;
    accountsPage = page;
    renderAccountsPage();
    renderPagination();
}

function renderAccounts(accounts) {
    allAccounts = accounts;
    accountsPage = 1;
    renderAccountsPage();
    renderPagination();
}

function startInstance(dbId, email) {
    app.showToast('Launching instance, installing apps, configuring proxy and login...', 'info');
    let url = `/api/instances/${dbId}/launch?sign_in_bing=true`;
    if (email) url += `&account_email=${encodeURIComponent(email)}`;
    app.api(url, { method: 'POST' })
        .then(() => {
            app.showToast('Instance launching. App install, proxy setup and login run automatically.', 'success');
            // Poll while the instance boots and the setup runs.
            pollLaunchStatus(dbId, 0);
        })
        .catch(e => app.showToast(e.message, 'error'));
}

function collectRedeem(dbId, email) {
    if (!confirm(`Doc diem va don hang moi nhat cho ${email}?`)) return;
    app.showToast('Dang doc man hinh Bing, vui long doi...', 'info');
    app.api(`/api/accounts/${encodeURIComponent(email)}/collect-redeem`, { method: 'POST' })
        .then(r => {
            const bits = [];
            bits.push(`Diem: ${r.points !== null ? r.points : '?'}`);
            if (r.order_date) bits.push(`Don ${r.order_date} (${r.reward_type})`);
            if (r.voucher_url) {
                bits.push(r.voucher_inserted ? 'Da luu voucher moi'
                    : (r.voucher_exists ? 'Voucher da ton tai' : 'Chua luu'));
            } else {
                bits.push('KHONG lay duoc link (khong co URL quy doi)');
            }
            if (r.redeemed_ip) bits.push(`IP ${r.redeemed_ip} da ghi nho`);
            const msg = bits.join(' | ');
            app.showToast(msg, r.voucher_url ? 'success' : 'warning', 20000);
            loadAccounts();
        })
        .catch(e => app.showToast('That bai: ' + e.message, 'error', 20000));
}

function bingLoginOnly(dbId, email) {
    app.showToast('Signing in to Bing...', 'info');
    const url = `/api/instances/${dbId}/launch?install_apps=false&account_email=${encodeURIComponent(email)}&sign_in_bing=true`;
    app.api(url, { method: 'POST' })
        .then(() => {
            app.showToast('Bing sign-in started in background.', 'success');
            pollLaunchStatus(dbId, 0);
        })
        .catch(e => app.showToast(e.message, 'error'));
}

const jobSeen = {};

function pollJobProgress(dbId, onDone) {
    const since = jobSeen[dbId] || 0;
    app.api(`/api/instances/${dbId}/progress?since=${since}`)
        .then(job => {
            const steps = job.steps || [];
            steps.forEach(s => {
                app.showToast(s.message, s.level === 'error' ? 'error'
                    : (s.level === 'warning' ? 'warning'
                        : (s.level === 'success' ? 'success' : 'info')), 12000);
            });
            if (steps.length) {
                // Use the server timestamp so nothing is skipped or repeated.
                jobSeen[dbId] = steps[steps.length - 1].ts;
                const last = steps[steps.length - 1];
                // A finished job reports either success or an explicit error.
                if (last.level === 'success' || last.level === 'error') {
                    if (typeof onDone === 'function') onDone(last);
                }
            }
        })
        .catch(() => {});
}

function pollRemoveStatus(dbId, attempt) {
    let finished = false;
    pollJobProgress(dbId, last => { finished = true; });

    if (attempt > 150) {                     // ~5 minutes
        app.showToast('Xoa LDPlayer qua lau, hay kiem tra lai', 'warning', 20000);
        loadAccounts();
        return;
    }
    setTimeout(() => {
        if (finished) {
            loadAccounts();
            if (typeof loadInstances === 'function') loadInstances();
            loadOverview();
            return;
        }
        pollRemoveStatus(dbId, attempt + 1);
    }, 2000);
}

function pollLaunchStatus(dbId, attempt) {
    pollJobProgress(dbId);
    if (attempt > 90) {          // ~4.5 minutes
        loadAccounts();
        return;
    }
    setTimeout(() => {
        app.api('/api/instances')
            .then(instances => {
                const inst = instances.find(i => i.id === dbId);
                if (!inst) { loadAccounts(); return; }
                loadAccounts();
                pollLaunchStatus(dbId, attempt + 1);
            })
            .catch(() => pollLaunchStatus(dbId, attempt + 1));
    }, 3000);
}

function stopInstance(dbId) {
    app.api(`/api/instances/${dbId}/quit`, { method: 'POST' })
        .then(() => {
            app.showToast(`Instance stopping...`, 'success');
            setTimeout(loadAccounts, 2000);
        })
        .catch(e => app.showToast(e.message, 'error'));
}

function installAppsAfterLaunch(dbId) {
    // Kept for backwards compatibility: the server installs any missing app
    // itself during /launch.
    app.showToast('Missing apps are installed automatically after launch', 'info');
}

function goToInstance(index) {
    // Same SPA path as the nav links: swaps the page instead of reloading,
    // which keeps the search state alive across both pages.
    app.navigate('/instances');
}

let assignTargetEmail = null;

async function assignInstance(email) {
    // A fresh instance is created rather than cloned: a copy of LDPlayer-0
    // keeps the same device fingerprint (Android ID / IMEI / MAC), which makes
    // Microsoft flag the account with a 24 hour device check. Which LDPlayer
    // installation (9 or 14) owns the new instance is picked in the modal.
    assignTargetEmail = email;
    const label = document.getElementById('assign-email-label');
    if (label) label.textContent = email;
    const typeSelect = document.getElementById('assign-ld-version');
    if (typeSelect) typeSelect.disabled = false;
    await populateAssignRuntimes();
    app.showModal('assign-instance-modal');
}

// Mark the installations that are drivable; LD14 stays visible but locked
// with a hint until its folder is configured in Settings > Paths.
async function populateAssignRuntimes() {
    const select = document.getElementById('assign-ld-version');
    if (!select) return;
    try {
        const { versions } = await app.api('/api/ldplayer/runtimes');
        const byVersion = {};
        (versions || []).forEach(v => { byVersion[v.version] = v; });
        Array.from(select.options).forEach(opt => {
            const info = byVersion[Number(opt.value)];
            if (info) {
                opt.disabled = !info.available;
                opt.textContent = info.available
                    ? info.name
                    : `${info.name} (chua cau hinh trong Settings)`;
            }
        });
        // Do not leave the selection on a disabled option.
        if (select.selectedOptions[0] && select.selectedOptions[0].disabled) {
            const firstEnabled = Array.from(select.options).find(o => !o.disabled);
            if (firstEnabled) select.value = firstEnabled.value;
        }
    } catch (err) {
        console.warn('khong tai duoc runtimes', err);
    }
}

function hideAssignModal() {
    app.hideModal('assign-instance-modal');
    assignTargetEmail = null;
}

async function confirmAssignInstance() {
    const email = assignTargetEmail;
    if (!email) { hideAssignModal(); return; }
    const typeSelect = document.getElementById('assign-ld-version');
    const ldVersion = typeSelect ? (Number(typeSelect.value) || 9) : 9;
    app.hideModal('assign-instance-modal');
    assignTargetEmail = null;

    try {
        const result = await app.api('/api/instances', {
            method: 'POST',
            body: JSON.stringify({ name: email, phone_mode: true, ld_version: ldVersion }),
        });
        app.showToast(`Tao instance moi tren LD${result.ld_version || ldVersion} (index ${result.index})`, 'success');
        assignInstanceToAccount(email, result.index);
    } catch (e) {
        app.showToast(e.message, 'error');
    }
}

function assignInstanceToAccount(email, instanceId) {
    app.api(`/api/accounts/${email}/assign`, {
        method: 'POST',
        body: JSON.stringify({ instance_id: instanceId }),
    })
    .then(() => {
        app.showToast('Instance assigned', 'success');
        loadAccounts();
    })
    .catch(e => app.showToast(e.message, 'error'));
}

async function unassignInstance(email) {
    const ok = confirm(
        `Unassign va XOA LDPlayer cho ${email}?\n\n`
        + 'Toan bo may emu va du lieu ben trong se bi xoa.');
    if (!ok) return;

    let instanceId = null;
    try {
        const m = await app.api(`/api/accounts/${encodeURIComponent(email)}/mapping`);
        instanceId = m.instance_id;
    } catch (e) {
        console.warn('khong doc duoc mapping', e);
    }

    try {
        await app.api(`/api/accounts/${encodeURIComponent(email)}/assign`, {
            method: 'POST',
            body: JSON.stringify({ instance_id: null }),
        });
    } catch (e) {
        app.showToast(e.message, 'error');
        return;
    }

    if (!instanceId) {
        app.showToast('Da unassign (khong tim thay LDPlayer de xoa)', 'success');
        loadAccounts();
        return;
    }

    // Deleting wipes the disk image and can take minutes, so the request
    // returns immediately and the work is followed through the progress feed.
    app.showToast('Dang xoa LDPlayer...', 'info');
    app.api(`/api/instances/${instanceId}/remove`, { method: 'POST' })
        .then(() => {
            pollRemoveStatus(instanceId, 0);
        })
        .catch(e => {
            app.showToast('Loi khi xoa LDPlayer: ' + e.message, 'error');
            loadAccounts();
        });
}

async function createInstance() {
    const name = prompt('Instance name (optional):');
    if (name === null) return;

    let cloneFrom = null;
    let ldVersion = 9;
    try {
        // /api/instances returns a plain array; the old "{ items }" destructure
        // silently produced an empty copy list.
        const items = await app.api('/api/instances');
        const list = (items || [])
            .map(i => `  LD${i.ld_version || 9}-${i.index}   ${i.name}`
                + (String(i.status).toLowerCase() === 'running' ? '   (dang chay)' : ''))
            .join('\n');
        const picked = prompt(
            `Copy tu instance nao? (de trong = tao moi khong copy)\n`
            + `Nhap dang "9:5" hoac "14:5" de chon ban LDPlayer, neu chi nhap so thi la LD9.\n\n${list}\n\nNhap so index:`,
            '');
        if (picked === null) return;
        const raw = picked.trim();
        if (raw !== '') {
            if (raw.includes(':')) {
                const parts = raw.split(':');
                ldVersion = Number(parts[0]) || 9;
                cloneFrom = Number(parts[1]);
            } else {
                cloneFrom = Number(raw);
            }
            if (!Number.isInteger(cloneFrom)) {
                app.showToast('Index khong hop le', 'error');
                return;
            }
        }
    } catch (e) {
        console.warn('khong tai duoc danh sach instance', e);
    }

    app.api('/api/instances', {
        method: 'POST',
        body: JSON.stringify({ name: name || null, clone_from: cloneFrom, ld_version: ldVersion }),
    })
    .then(result => {
        const usedVersion = result.ld_version || ldVersion;
        app.showToast(
            cloneFrom === null
                ? `Instance created on LD${usedVersion} (index ${result.index})`
                : `Copied from LD${usedVersion}-${cloneFrom} -> index ${result.index}`,
            'success');
        loadOverview();
        if (typeof loadInstances === 'function') loadInstances();
    })
    .catch(e => app.showToast(e.message, 'error'));
}

function refreshInstances() {
    app.api('/api/instances')
        .then(() => {
            app.showToast('Instances refreshed', 'success');
            loadOverview();
            if (typeof loadInstances === 'function') loadInstances();
        })
        .catch(e => app.showToast(e.message, 'error'));
}

function syncInstances() {
    app.api('/api/instances')
        .then(() => {
            app.showToast('Synced to database', 'success');
            loadOverview();
        })
        .catch(e => app.showToast(e.message, 'error'));
}

function loadRecentLogs() {
    app.api('/api/logs?limit=50')
        .then(updateRecentLogs)
        .catch(e => app.showToast(e.message, 'error'));
}

// Init is driven by app.js: it runs right after this file on a full page
// load and at the end of the first SPA visit to this route. Returning to
// a cached page only refreshes the data (see App.showPage in app.js).
function initOverviewPage() {
    loadOverview();
}
window.initOverviewPage = initOverviewPage;

// Export functions to global scope
window.loadOverview = loadOverview;
window.loadAccounts = loadAccounts;
window.renderAccounts = renderAccounts;
window.renderAccountsPage = renderAccountsPage;
window.renderPagination = renderPagination;
window.changePage = changePage;
window.startInstance = startInstance;
window.bingLoginOnly = bingLoginOnly;
window.collectRedeem = collectRedeem;
window.stopInstance = stopInstance;
window.goToInstance = goToInstance;
window.assignInstance = assignInstance;
window.assignInstanceToAccount = assignInstanceToAccount;
window.unassignInstance = unassignInstance;
window.hideAssignModal = hideAssignModal;
window.confirmAssignInstance = confirmAssignInstance;
window.createInstance = createInstance;
window.refreshInstances = refreshInstances;
window.syncInstances = syncInstances;
window.loadRecentLogs = loadRecentLogs;