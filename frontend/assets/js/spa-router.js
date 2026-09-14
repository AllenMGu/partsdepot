(function () {
    const DEFAULT_ROUTE = 'dashboard-view.html';
    const ROUTE_ALIASES = {
        'dashboard.html': 'dashboard-view.html',
        'index.html': 'dashboard-view.html'
    };
    const ALLOWED_BASES = new Set([
        'dashboard-view.html',
        'warehouse.html',
        'location.html',
        'goods.html',
        'stock.html',
        'scan.html',
        'inbound.html',
        'outbound.html',
        'check.html',
        'user.html'
    ]);

    const frame = document.getElementById('spaViewFrame');
    if (!frame) return;

    let currentRoute = '';

    function normalizeRoute(rawRoute) {
        const source = (rawRoute || '').trim();
        if (!source) return DEFAULT_ROUTE;

        let url;
        try {
            url = new URL(source, window.location.href);
        } catch (error) {
            return DEFAULT_ROUTE;
        }

        let base = url.pathname.split('/').pop() || DEFAULT_ROUTE;
        base = ROUTE_ALIASES[base] || base;
        if (!ALLOWED_BASES.has(base)) {
            return DEFAULT_ROUTE;
        }

        const cleanParams = new URLSearchParams(url.search);
        cleanParams.delete('embedded');
        cleanParams.delete('view');
        const query = cleanParams.toString();
        return `${base}${query ? `?${query}` : ''}${url.hash}`;
    }

    function getRouteFromUrl() {
        const params = new URLSearchParams(window.location.search);
        return normalizeRoute(params.get('view') || DEFAULT_ROUTE);
    }

    function updateBrowserUrl(route, replace = false) {
        const url = new URL(window.location.href);
        url.searchParams.set('view', route);
        const method = replace ? 'replaceState' : 'pushState';
        window.history[method]({ route }, '', `${url.pathname}${url.search}`);
    }

    function markActive(route) {
        const base = route.split('?')[0].split('#')[0];
        document.querySelectorAll('a[data-route]').forEach((link) => {
            const isActive = normalizeRoute(link.dataset.route || '')
                .split('?')[0]
                .split('#')[0] === base;
            if (isActive) {
                link.classList.add('sidebar-active');
                link.classList.remove('text-gray-700');
            } else {
                link.classList.remove('sidebar-active');
                if (!link.classList.contains('text-gray-700')) {
                    link.classList.add('text-gray-700');
                }
            }
        });
    }

    function addEmbeddedQuery(route) {
        const url = new URL(route, window.location.href);
        url.searchParams.delete('view');
        url.searchParams.set('embedded', '1');
        const file = url.pathname.split('/').pop();
        return `${file}${url.search}${url.hash}`;
    }

    function navigate(route, options = {}) {
        const normalized = normalizeRoute(route);
        const replace = Boolean(options.replace);
        const skipHistory = Boolean(options.skipHistory);

        if (!skipHistory) {
            updateBrowserUrl(normalized, replace);
        }

        if (normalized === currentRoute && frame.src) {
            markActive(normalized);
            return;
        }

        currentRoute = normalized;
        markActive(normalized);
        frame.src = addEmbeddedQuery(normalized);
    }

    function bindShellLinks() {
        document.addEventListener('click', (event) => {
            const link = event.target.closest('a[data-route]');
            if (!link) return;
            if (event.defaultPrevented) return;
            if (event.button !== 0) return;
            if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;

            event.preventDefault();
            navigate(link.dataset.route || link.getAttribute('href') || DEFAULT_ROUTE);
        });
    }

    function patchEmbeddedDocument(doc) {
        if (!doc || !doc.head || !doc.body) return;

        if (!doc.getElementById('wms-spa-embedded-style')) {
            const style = doc.createElement('style');
            style.id = 'wms-spa-embedded-style';
            style.textContent = `
header { display: none !important; }
#sidebar { display: none !important; }
body { overflow: hidden !important; }
body > div.flex.flex-1.overflow-hidden { height: 100vh !important; }
main.flex-1 { height: 100vh !important; overflow-y: auto !important; }
`;
            doc.head.appendChild(style);
        }

        if (!doc.__WMS_SPA_LINK_HOOKED__) {
            doc.addEventListener('click', (event) => {
                const link = event.target.closest('a[href]');
                if (!link) return;
                const href = link.getAttribute('href');
                if (!href || href.startsWith('#') || href.startsWith('javascript:')) return;
                if (link.target && link.target !== '_self') return;

                const nextRoute = normalizeRoute(href);
                if (!nextRoute) return;

                event.preventDefault();
                navigate(nextRoute);
            }, true);
            doc.__WMS_SPA_LINK_HOOKED__ = true;
        }
    }

    function bindFrameLifecycle() {
        frame.addEventListener('load', () => {
            let frameUrl;
            let rawBase = '';
            try {
                const frameWindow = frame.contentWindow;
                const frameDoc = frame.contentDocument;
                if (!frameWindow || !frameDoc) return;

                if (frameDoc.getElementById('spaViewFrame')) {
                    currentRoute = DEFAULT_ROUTE;
                    markActive(DEFAULT_ROUTE);
                    frame.src = addEmbeddedQuery(DEFAULT_ROUTE);
                    return;
                }

                patchEmbeddedDocument(frameDoc);

                rawBase = frameWindow.location.pathname.split('/').pop() || '';
                frameUrl = `${rawBase}${frameWindow.location.search || ''}${frameWindow.location.hash || ''}`;
            } catch (error) {
                return;
            }

            const normalized = normalizeRoute(frameUrl);
            if (rawBase === 'index.html' || rawBase === 'dashboard.html') {
                currentRoute = normalized;
                markActive(normalized);
                frame.src = addEmbeddedQuery(normalized);
                return;
            }

            if (normalized !== currentRoute) {
                navigate(normalized, { replace: false, skipHistory: false });
            }
        });
    }

    window.addEventListener('popstate', () => {
        navigate(getRouteFromUrl(), { replace: true, skipHistory: true });
    });

    window.__WMS_SPA_NAVIGATE__ = function (route) {
        navigate(route || DEFAULT_ROUTE);
    };

    bindShellLinks();
    bindFrameLifecycle();
    navigate(getRouteFromUrl(), { replace: true });
})();
