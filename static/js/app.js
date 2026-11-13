// Route -> the global init hook each page script exposes. app.js drives
// page initialization on both full page loads and SPA navigations, so the
// DOMContentLoaded listeners could be dropped from the page scripts.
const PAGE_INITS = {
    '/': 'initOverviewPage',
    '/dashboard': 'initOverviewPage',
    '/instances': 'initInstancesPage',
    '/apps': 'initAppsPage',
    '/logs': 'initLogsPage',
    '/settings': 'initSettingsPage',
};

// Optional per-route cleanup when navigating away (the Logs page keeps a
// live-refresh interval that must not poll a page sitting in the cache).
const PAGE_TEARDOWNS = {
    '/logs': 'teardownLogsPage',
};

// Optional per-route restart when returning to a cached page (restart what
// teardown stopped, with the state the checkboxes have on the cached DOM).
const PAGE_RESUMES = {
    '/logs': 'resumeLogsPage',
};

// Search boxes whose value has to survive route changes and full reloads.
const SEARCH_INPUT_IDS = ['search-email', 'search-instance'];
const SEARCH_STORAGE_KEY = 'redem_searches';

class App {
    constructor() {
        // Self-register FIRST: init (below) drives the page's own scripts,
        // which call back into `app` (loadSettings -> app.api, toast...).
        // Assigning only after construction finished would referenceError
        // every page that loads its data during init.
        window.app = this;
        this.ws = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 10;
        this.reconnectDelay = 1000;
        // SPA state: one cache entry per visited route. Caching the live
        // DOM node of each page preserves whatever the user had on screen
        // (search value, filters, pagination, listeners) with zero manual
        // copying when they switch tabs and come back.
        this.pages = {};
        this.titles = {};
        this.scrollPositions = {};
        this.executedScripts = new Set();
        this._navigating = false;
        this.init();
    }

    init() {
        this.cacheInitialState();
        this.connectWebSocket();
        this.startTimeUpdater();
        this.reloadWhenVisible();
        this.enableSpaNavigation();
        const boot = () => {
            this.restoreSearch();
            this.initCurrentPage();
        };
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', boot);
        } else {
            // App is normally constructed from a DOMContentLoaded listener
            // (bottom of this file), so the page scripts - which define the
            // init hooks - have already run by the time we get here.
            boot();
        }
    }

    // Register the page that came with the full document load plus every
    // script that has already executed, so revisiting this route never
    // re-fetches or re-executes anything.
    cacheInitialState() {
        const main = document.querySelector('main.main-content');
        if (main) {
            this.pages[window.location.pathname] = main;
            this.titles[window.location.pathname] = document.title;
        }
        document.querySelectorAll('script[src]').forEach(s => {
            const src = s.getAttribute('src');
            if (src) this.executedScripts.add(src);
        });
    }

    // Runs the current route's init hook; the hook binds listeners and
    // pulls the first batch of data.
    initCurrentPage() {
        const hook = PAGE_INITS[window.location.pathname];
        if (hook && typeof window[hook] === 'function') window[hook]();
    }

    // Stop anything the page keeps running in the background before its
    // node is detached into the cache.
    teardownCurrentPage(path) {
        const hook = PAGE_TEARDOWNS[path];
        if (hook && typeof window[hook] === 'function') window[hook]();
    }

    restartCurrentPage(path) {
        const hook = PAGE_RESUMES[path];
        if (hook && typeof window[hook] === 'function') window[hook]();
    }

    // Every tab loads its data exactly once on open. Coming back to the
    // page - another tab, the taskbar, or the browser Back button restoring
    // it from cache - left the old numbers on screen. Reload the current
    // page's data whenever it becomes visible again.
    reloadWhenVisible() {
        const reload = () => {
            if (document.visibilityState !== 'visible') return;
            const now = Date.now();
            const minGap = 5000; // avoid hammering the API when alt-tabbing
            if (this._lastAutoReload && now - this._lastAutoReload < minGap) return;
            this._lastAutoReload = now;
            this.refreshCurrentPage();
        };
        document.addEventListener('visibilitychange', reload);
        window.addEventListener('focus', reload);
        // Back/forward navigation can restore the page without re-running
        // the scripts - the data then has to be fetched explicitly.
        window.addEventListener('pageshow', (e) => {
            if (e.persisted) reload();
        });
        // Persist the search boxes on reloads/closes too, not only on input.
        window.addEventListener('pagehide', () => this.persistSearch());
    }

    // Routes the top-nav links through showPage so switching tabs swaps the
    // <main> node instead of reloading the whole document: the search values
    // and all other page state survive, and returning to a visited tab is
    // instant. Any failure falls back to a classic full page load.
    enableSpaNavigation() {
        document.addEventListener('click', (e) => {
            const link = e.target.closest('.nav-links a');
            if (!link) return;
            if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
            const href = link.getAttribute('href');
            if (!href || href.startsWith('#')) return;
            e.preventDefault();
            if (window.location.pathname !== href) this.showPage(href, true);
        });
        // Back/forward buttons walk the same cached pages.
        window.addEventListener('popstate', () => {
            this.showPage(window.location.pathname, false);
        });
    }

    // Programmatic navigation for page code (same SPA path as the nav
    // links; unknown routes fall back to a classic location change).
    navigate(path) {
        if (PAGE_INITS[path] && window.location.pathname !== path) {
            this.showPage(path, true);
        } else {
            window.location.href = path;
        }
    }

    async showPage(path, push = true) {
        if (this._navigating) return;
        this._navigating = true;
        try {
            // Save the state of the page being left, stop its timers and
            // close leftover modals so they do not haunt the cached node.
            this.savePageState(window.location.pathname);
            this.teardownCurrentPage(window.location.pathname);
            this.closeModals();

            const isNew = !this.pages[path];
            let mainNode;
            if (isNew) {
                const response = await fetch(path, {
                    headers: { 'X-Requested-With': 'redem-spa' },
                });
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                const contentType = response.headers.get('content-type') || '';
                if (!contentType.includes('text/html')) {
                    throw new Error(`Unexpected content type: ${contentType}`);
                }
                const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
                mainNode = doc.querySelector('main.main-content');
                if (!mainNode) throw new Error('Trang khong co main content');
                this.titles[path] = doc.title || path;
                // Page scripts execute exactly once per browser tab; every
                // later visit reuses both the cached node and the handlers.
                await this.loadScriptsOnce(doc);
                this.pages[path] = mainNode;
            } else {
                mainNode = this.pages[path];
            }

            const current = document.querySelector('main.main-content');
            if (current) current.replaceWith(mainNode);
            document.title = this.titles[path] || document.title;
            this.setActiveNav(path);
            window.scrollTo(0, this.scrollPositions[path] || 0);
            if (push) history.pushState({ path }, '', path);

            if (isNew) {
                // Restore the session's search values before the init hook
                // fetches data, so the first render already filters on them.
                this.restoreSearch();
                this.initCurrentPage();
            } else {
                // Returning to a cached page: restart its timers, then
                // refresh the data in the background so the numbers stay
                // current. The DOM (search value, filters, selection) is
                // exactly what the user left behind.
                this.restartCurrentPage(path);
                this.refreshCurrentPage();
            }
        } catch (e) {
            console.error('SPA navigation failed, falling back to full load:', e);
            window.location.href = path;
        } finally {
            this._navigating = false;
        }
    }

    savePageState(path) {
        this.persistSearch();
        this.scrollPositions[path] = window.scrollY;
    }

    closeModals() {
        document.querySelectorAll('.modal.active').forEach(m => m.classList.remove('active'));
    }

    setActiveNav(path) {
        document.querySelectorAll('.nav-links a').forEach(a => {
            a.classList.toggle('active', a.getAttribute('href') === path);
        });
    }

    // Execute each page script exactly once per browser tab. Scripts that
    // the initial document already loaded (cached in cacheInitialState) are
    // skipped, so revisiting the landing route never re-runs them.
    loadScriptsOnce(doc) {
        const pending = [];
        doc.querySelectorAll('script[src]').forEach(s => {
            const src = s.getAttribute('src');
            if (src && !this.executedScripts.has(src)) pending.push(src);
        });
        return Promise.all(pending.map(src => new Promise(resolve => {
            this.executedScripts.add(src);
            const el = document.createElement('script');
            el.src = src;
            el.onload = () => resolve();
            el.onerror = () => {
                console.error('Khong tai duoc script:', src);
                resolve();
            };
            document.body.appendChild(el);
        })));
    }

    persistSearch() {
        // Typed searches survive route changes and full reloads (F5, back/
        // forward). Per-tab storage on purpose: sessionStorage.
        try {
            const saved = {};
            for (const id of SEARCH_INPUT_IDS) {
                const el = document.getElementById(id);
                if (el) saved[id] = el.value;
            }
            sessionStorage.setItem(SEARCH_STORAGE_KEY, JSON.stringify(saved));
        } catch (e) { /* storage unavailable (private mode, sandboxed iframe) */ }
    }

    restoreSearch() {
        let saved;
        try {
            saved = JSON.parse(sessionStorage.getItem(SEARCH_STORAGE_KEY) || '{}');
        } catch (e) {
            return;
        }
        for (const id of SEARCH_INPUT_IDS) {
            const el = document.getElementById(id);
            // Never clobber a value the DOM already carries.
            if (el && typeof saved[id] === 'string' && !el.value) el.value = saved[id];
        }
    }

    refreshCurrentPage() {
        const path = window.location.pathname;
        if (path === '/' || path === '/dashboard') {
            if (typeof loadOverview === 'function') loadOverview();
        } else if (path === '/instances') {
            if (typeof loadInstances === 'function') loadInstances();
        } else if (path === '/apps') {
            if (typeof loadAppsStatus === 'function') loadAppsStatus();
            if (typeof loadTargetInstances === 'function') loadTargetInstances();
        } else if (path === '/logs') {
            if (typeof loadLogs === 'function') loadLogs();
        } else if (path === '/settings') {
            if (typeof loadSettings === 'function') loadSettings();
        }
    }

    connectWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws`;

        this.ws = new WebSocket(wsUrl);

        this.ws.onopen = () => {
            console.log('WebSocket connected');
            this.reconnectAttempts = 0;
            this.updateConnectionStatus(true);
        };

        this.ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                this.handleMessage(data);
            } catch (e) {
                console.error('Failed to parse WS message:', e);
            }
        };

        this.ws.onclose = () => {
            console.log('WebSocket disconnected');
            this.updateConnectionStatus(false);
            this.scheduleReconnect();
        };

        this.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
        };
    }

    scheduleReconnect() {
        if (this.reconnectAttempts < this.maxReconnectAttempts) {
            const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts);
            this.reconnectAttempts++;
            console.log(`Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);
            setTimeout(() => this.connectWebSocket(), delay);
        } else {
            console.error('Max reconnect attempts reached');
        }
    }

    updateConnectionStatus(connected) {
        const el = document.getElementById('connection-status');
        if (el) {
            el.className = `status-indicator ${connected ? 'connected' : 'disconnected'}`;
            el.innerHTML = `<span class="dot"></span> ${connected ? 'Connected' : 'Disconnected'}`;
        }
    }

    handleMessage(data) {
        if (data.type === 'workflow_update') {
            this.onWorkflowUpdate(data);
        }
    }

    onWorkflowUpdate(data) {
        if (typeof window.onWorkflowUpdate === 'function') {
            window.onWorkflowUpdate(data);
        }
        this.refreshCurrentPage();
    }

    startTimeUpdater() {
        const timeEl = document.getElementById('server-time');
        if (timeEl) {
            setInterval(() => {
                timeEl.textContent = new Date().toLocaleString();
            }, 1000);
        }
    }

    async api(path, options = {}) {
        const response = await fetch(path, {
            headers: {
                'Content-Type': 'application/json',
                ...options.headers,
            },
            ...options,
        });

        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: 'Request failed' }));
            throw new Error(error.detail || `HTTP ${response.status}`);
        }

        return response.json();
    }

    showToast(message, type = 'info', duration = 8000) {
        const container = document.getElementById('toast-container') || this.createToastContainer();
        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        toast.textContent = message;
        container.appendChild(toast);

        setTimeout(() => toast.classList.add('show'), 10);
        setTimeout(() => {
            toast.classList.remove('show');
            setTimeout(() => toast.remove(), 300);
        }, duration);
    }

    createToastContainer() {
        // Layout lives in style.css (.toast / #toast-container).
        const container = document.createElement('div');
        container.id = 'toast-container';
        document.body.appendChild(container);
        return container;
    }

    showModal(id) {
        const modal = document.getElementById(id);
        if (modal) modal.classList.add('active');
    }

    hideModal(id) {
        const modal = document.getElementById(id);
        if (modal) modal.classList.remove('active');
    }

    formatDate(dateString) {
        if (!dateString) return '-';
        const date = new Date(dateString);
        return date.toLocaleString();
    }

    formatRelativeTime(dateString) {
        if (!dateString) return '-';
        const date = new Date(dateString);
        const now = new Date();
        const diff = Math.floor((now - date) / 1000);

        if (diff < 60) return `${diff}s ago`;
        if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
        if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
        return `${Math.floor(diff / 86400)}d ago`;
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        if (!window.app) window.app = new App();
    });
} else if (!window.app) {
    window.app = new App();
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

window.escapeHtml = escapeHtml;
