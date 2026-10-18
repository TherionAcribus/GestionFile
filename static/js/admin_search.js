// Recherche globale de l'administration (palette « Ctrl+K »).
//
// - Ouverture : bouton « Rechercher » de la barre latérale, Ctrl+K / Cmd+K, ou
//   « / » hors d'un champ de saisie.
// - Index : GET /admin/search/index, chargé à la première ouverture puis gardé
//   en mémoire pour la page. Il est déjà filtré par permission côté serveur.
// - Correspondance : tous les mots saisis doivent apparaître (sans accents ni
//   majuscules) dans le titre, le fil d'Ariane ou les mots-clés ; même règle
//   que admin_search.matches() côté Python.
// - Saut direct : une destination « …#focus=<id> » ouvre l'onglet, le volet
//   <details> ou le bloc repliable qui contient l'élément, le fait défiler à
//   l'écran et le met en évidence. Fonctionne aussi pour les éléments chargés
//   ensuite par HTMX (attente bornée).
//
// Aucun HTML n'est construit par concaténation : les titres proviennent en
// partie de la base (noms d'activités, de comptes) et sont insérés en texte.

const AdminSearch = (function () {
    const MAX_RESULTS = 40;
    const RECENT_KEY = 'adminSearchRecent';
    const MAX_RECENT = 6;
    const FOCUS_TIMEOUT_MS = 8000;
    const KIND_BONUS = { page: 8, tab: 6, action: 5, section: 4, entity: 2, setting: 0 };

    let entries = null;      // index chargé (avec champs normalisés)
    let loading = null;      // promesse de chargement en cours
    let results = [];
    let active = -1;
    let modal = null;

    function byId(id) { return document.getElementById(id); }

    function normalize(text) {
        return String(text || '').toLowerCase()
            .normalize('NFD').replace(/[̀-ͯ]/g, '');
    }

    // --- Index -------------------------------------------------------------

    function prepare(list) {
        return list.map(function (e) {
            e._title = normalize(e.title);
            e._section = normalize(e.section);
            e._keywords = normalize(e.keywords + ' ' + e.kind_label);
            e._all = e._title + ' ' + e._section + ' ' + e._keywords;
            return e;
        });
    }

    function load() {
        if (entries) return Promise.resolve(entries);
        if (loading) return loading;
        loading = fetch('/admin/search/index', {
            credentials: 'same-origin',
            headers: { 'Accept': 'application/json' }
        }).then(function (response) {
            if (!response.ok) throw new Error('HTTP ' + response.status);
            return response.json();
        }).then(function (data) {
            entries = prepare(data.entries || []);
            return entries;
        }).finally(function () {
            loading = null;
        });
        return loading;
    }

    // --- Recherche ---------------------------------------------------------

    function score(entry, tokens, query) {
        let total = KIND_BONUS[entry.kind] || 0;
        if (entry._title === query) total += 60;
        else if (entry._title.indexOf(query) === 0) total += 40;
        for (const token of tokens) {
            if (entry._all.indexOf(token) === -1) return -1;
            const at = entry._title.indexOf(token);
            if (at === 0 || (at > 0 && /[\s'’«(\-]/.test(entry._title.charAt(at - 1)))) total += 30;
            else if (at > 0) total += 15;
            else if (entry._section.indexOf(token) !== -1) total += 6;
            else total += 4;
        }
        return total - entry._title.length / 25;
    }

    function search(query) {
        const q = normalize(query).trim();
        if (!q) return defaultResults();
        const tokens = q.split(/\s+/);
        const scored = [];
        for (const entry of entries) {
            const s = score(entry, tokens, q);
            if (s >= 0) scored.push({ entry: entry, score: s });
        }
        scored.sort(function (a, b) { return b.score - a.score; });
        return scored.slice(0, MAX_RESULTS).map(function (r) { return r.entry; });
    }

    // Requête vide : éléments récemment ouverts, puis les pages.
    function defaultResults() {
        const byUrl = {};
        entries.forEach(function (e) { byUrl[e.url] = e; });
        const recent = readRecent().map(function (url) { return byUrl[url]; })
            .filter(Boolean).map(function (e) { return Object.assign({}, e, { _recent: true }); });
        const seen = new Set(recent.map(function (e) { return e.url; }));
        const pages = entries.filter(function (e) { return e.kind === 'page' && !seen.has(e.url); });
        return recent.concat(pages);
    }

    // --- Récents (confort par navigateur, jamais indispensable) -------------

    function readRecent() {
        try {
            const list = JSON.parse(window.localStorage.getItem(RECENT_KEY) || '[]');
            return Array.isArray(list) ? list.filter(function (u) { return typeof u === 'string'; }) : [];
        } catch (e) {
            return [];
        }
    }

    function remember(url) {
        try {
            const list = readRecent().filter(function (u) { return u !== url; });
            list.unshift(url);
            window.localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, MAX_RECENT)));
        } catch (e) { /* stockage indisponible : sans conséquence */ }
    }

    // --- Rendu -------------------------------------------------------------

    // Titre avec les mots trouvés en <mark>, construit en nœuds texte. Les
    // positions sont calculées sur la version normalisée, caractère par
    // caractère, pour rester alignées malgré les accents.
    function highlighted(title, tokens) {
        const chars = Array.from(title);
        const norm = chars.map(normalize);
        const flat = norm.join('');
        const owner = [];
        norm.forEach(function (n, i) { for (let k = 0; k < n.length; k++) owner.push(i); });
        const marked = new Array(chars.length).fill(false);
        tokens.forEach(function (token) {
            if (!token) return;
            let from = flat.indexOf(token);
            while (from !== -1) {
                for (let k = from; k < from + token.length; k++) marked[owner[k]] = true;
                from = flat.indexOf(token, from + token.length);
            }
        });
        const fragment = document.createDocumentFragment();
        let buffer = '';
        let state = false;
        function flush() {
            if (!buffer) return;
            if (state) {
                const mark = document.createElement('mark');
                mark.textContent = buffer;
                fragment.appendChild(mark);
            } else {
                fragment.appendChild(document.createTextNode(buffer));
            }
            buffer = '';
        }
        chars.forEach(function (c, i) {
            if (marked[i] !== state) { flush(); state = marked[i]; }
            buffer += c;
        });
        flush();
        return fragment;
    }

    function render(query) {
        const list = byId('admin-search-results');
        const status = byId('admin-search-status');
        const input = byId('admin-search-input');
        const tokens = normalize(query).trim().split(/\s+/).filter(Boolean);
        list.replaceChildren();
        results.forEach(function (entry, index) {
            const li = document.createElement('li');
            li.id = 'admin-search-option-' + index;
            li.setAttribute('role', 'option');
            li.setAttribute('aria-selected', 'false');
            li.className = 'admin-search-item';

            const link = document.createElement('a');
            link.href = entry.url;
            link.tabIndex = -1;
            link.dataset.index = String(index);

            const icon = document.createElement('i');
            icon.className = 'bi ' + (entry._recent ? 'bi-clock-history' : (entry.icon || 'bi-dot')) + ' admin-search-icon';
            icon.setAttribute('aria-hidden', 'true');

            const text = document.createElement('span');
            text.className = 'admin-search-text';
            const title = document.createElement('span');
            title.className = 'admin-search-title';
            title.appendChild(highlighted(entry.title, tokens));
            text.appendChild(title);
            if (entry.section) {
                const section = document.createElement('span');
                section.className = 'admin-search-section';
                section.textContent = entry.section;
                text.appendChild(section);
            }

            const kind = document.createElement('span');
            kind.className = 'badge admin-search-kind admin-search-kind-' + entry.kind;
            kind.textContent = entry._recent ? 'Récent' : entry.kind_label;

            link.append(icon, text, kind);
            li.appendChild(link);
            list.appendChild(li);
        });

        if (!query.trim()) {
            status.textContent = results.length ? '' : 'Tapez un mot : page, réglage, activité, comptoir…';
        } else if (!results.length) {
            status.textContent = 'Aucun résultat pour « ' + query.trim() + ' ».';
        } else {
            status.textContent = results.length + (results.length > 1 ? ' résultats' : ' résultat');
        }
        input.setAttribute('aria-expanded', results.length ? 'true' : 'false');
        setActive(results.length ? 0 : -1);
    }

    function setActive(index) {
        const list = byId('admin-search-results');
        const input = byId('admin-search-input');
        const previous = list.querySelector('[aria-selected="true"]');
        if (previous) previous.setAttribute('aria-selected', 'false');
        active = index;
        if (index < 0) {
            input.removeAttribute('aria-activedescendant');
            return;
        }
        const option = byId('admin-search-option-' + index);
        option.setAttribute('aria-selected', 'true');
        input.setAttribute('aria-activedescendant', option.id);
        option.scrollIntoView({ block: 'nearest' });
    }

    function refresh() {
        const input = byId('admin-search-input');
        if (!entries) return;
        results = search(input.value);
        render(input.value);
    }

    // --- Ouverture / navigation ---------------------------------------------

    function open() {
        const element = byId('admin-search-modal');
        if (!element || typeof bootstrap === 'undefined') return;
        modal = bootstrap.Modal.getOrCreateInstance(element);
        modal.show();
        // Focus immédiat : sans lui, les lettres tapées juste après Ctrl+K,
        // pendant l'animation d'ouverture, seraient perdues. La recherche
        // précédente est sélectionnée : la frappe la remplace.
        const input = byId('admin-search-input');
        input.focus();
        input.select();
        const status = byId('admin-search-status');
        if (!entries) {
            status.textContent = 'Chargement…';
            load().then(refresh).catch(function () {
                status.textContent = "La recherche n'a pas pu être chargée. Réessayez dans un instant.";
            });
        } else {
            refresh();
        }
    }

    function go(index) {
        const entry = results[index];
        if (!entry) return;
        remember(entry.url);
        const target = new URL(entry.url, window.location.origin);
        const samePage = target.pathname === window.location.pathname &&
            target.search === window.location.search;
        if (modal) modal.hide();
        if (samePage && target.hash) {
            // Seul le fragment change : pas de rechargement, on révèle sur place.
            history.replaceState(null, '', target.hash);
            revealFromHash();
        } else {
            window.location.href = entry.url;
        }
    }

    function onInputKey(event) {
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            if (!results.length) return;
            const step = event.key === 'ArrowDown' ? 1 : -1;
            setActive((active + step + results.length) % results.length);
        } else if (event.key === 'Enter') {
            event.preventDefault();
            if (active >= 0) {
                if (event.ctrlKey || event.metaKey) {
                    remember(results[active].url);
                    window.open(results[active].url, '_blank', 'noopener');
                } else {
                    go(active);
                }
            }
        }
    }

    function isEditable(element) {
        if (!element) return false;
        const tag = element.tagName;
        return element.isContentEditable || tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
    }

    function onGlobalKey(event) {
        const ctrlK = (event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === 'k';
        const slash = event.key === '/' && !event.ctrlKey && !event.metaKey && !event.altKey &&
            !isEditable(document.activeElement);
        if (ctrlK || slash) {
            event.preventDefault();
            open();
        }
    }

    // --- Saut direct vers un élément (#focus=<id>) --------------------------

    function focusTargetId() {
        const match = /^#focus=([A-Za-z0-9_-]+)$/.exec(window.location.hash);
        return match ? match[1] : null;
    }

    function waitForElement(id) {
        const found = byId(id);
        if (found) return Promise.resolve(found);
        return new Promise(function (resolve) {
            const observer = new MutationObserver(function () {
                const element = byId(id);
                if (element) {
                    observer.disconnect();
                    window.clearTimeout(timer);
                    resolve(element);
                }
            });
            const timer = window.setTimeout(function () {
                observer.disconnect();
                resolve(null);
            }, FOCUS_TIMEOUT_MS);
            observer.observe(document.body, { childList: true, subtree: true });
        });
    }

    // Ouvre, du plus extérieur au plus intérieur, les conteneurs masquant
    // l'élément : onglet Bootstrap, volet <details>, bloc .collapse.
    function revealAncestors(element) {
        const chain = [];
        for (let node = element; node && node !== document.body; node = node.parentElement) {
            chain.push(node);
        }
        chain.reverse().forEach(function (node) {
            if (node.tagName === 'DETAILS' && !node.open) {
                node.open = true;
            } else if (node.classList.contains('tab-pane') && !node.classList.contains('active') && node.id) {
                const trigger = document.querySelector(
                    '[data-bs-target="#' + node.id + '"], [href="#' + node.id + '"]');
                if (trigger && typeof bootstrap !== 'undefined') {
                    bootstrap.Tab.getOrCreateInstance(trigger).show();
                }
            } else if (node.classList.contains('collapse') && !node.classList.contains('show') &&
                       typeof bootstrap !== 'undefined') {
                bootstrap.Collapse.getOrCreateInstance(node, { toggle: false }).show();
            }
        });
    }

    function emphasize(element) {
        const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        const behavior = reduce ? 'auto' : 'smooth';
        if (element.classList.contains('tab-pane')) {
            // Onglet : on montre sa barre d'onglets, pas le milieu du contenu.
            const trigger = document.querySelector('[data-bs-target="#' + element.id + '"]');
            (trigger || element).scrollIntoView({ block: 'nearest', behavior: behavior });
            if (trigger) trigger.focus({ preventScroll: true });
            return;
        }
        element.scrollIntoView({ block: 'center', behavior: behavior });
        let box = element.closest('.admin-setting-card, .card, .row, section') || element;
        // Jamais la grille de mise en page entière (titre hors carte).
        if (box.querySelector('main') || box.offsetHeight > window.innerHeight * 0.9) box = element;
        box.classList.remove('admin-search-hit');
        void box.offsetWidth; // relance l'animation si l'élément était déjà marqué
        box.classList.add('admin-search-hit');
        window.setTimeout(function () { box.classList.remove('admin-search-hit'); }, 2600);
        if (!element.matches('input, textarea, select, button, a[href], [tabindex]')) {
            element.setAttribute('tabindex', '-1');
        }
        element.focus({ preventScroll: true });
    }

    function revealFromHash() {
        const id = focusTargetId();
        if (!id) return;
        // Le fragment a servi : on le retire pour qu'un rechargement de la
        // page ne refasse pas défiler l'utilisateur.
        history.replaceState(null, '', window.location.pathname + window.location.search);
        waitForElement(id).then(function (element) {
            if (!element) {
                if (typeof AdminFeedback !== 'undefined') {
                    AdminFeedback.announceError("L'élément recherché n'est pas affiché sur cette page.");
                }
                return;
            }
            revealAncestors(element);
            // Laisse aux transitions d'onglet / de dépliage le temps de finir.
            window.setTimeout(function () { emphasize(element); }, 380);
        });
    }

    // --- Initialisation ------------------------------------------------------

    function init() {
        document.addEventListener('keydown', onGlobalKey);
        document.querySelectorAll('[data-admin-search-open]').forEach(function (button) {
            button.addEventListener('click', open);
        });
        const input = byId('admin-search-input');
        const list = byId('admin-search-results');
        const element = byId('admin-search-modal');
        if (input && list && element) {
            input.addEventListener('input', refresh);
            input.addEventListener('keydown', onInputKey);
            list.addEventListener('click', function (event) {
                const link = event.target.closest('a[data-index]');
                if (!link) return;
                if (event.ctrlKey || event.metaKey || event.shiftKey || event.button !== 0) {
                    remember(results[Number(link.dataset.index)].url);
                    return; // ouverture dans un nouvel onglet : comportement natif
                }
                event.preventDefault();
                go(Number(link.dataset.index));
            });
            list.addEventListener('mousemove', function (event) {
                const link = event.target.closest('a[data-index]');
                if (link && Number(link.dataset.index) !== active) setActive(Number(link.dataset.index));
            });
            // Fin d'animation : Bootstrap place le focus sur la fenêtre ; on le
            // rend au champ en conservant sélection et saisie en cours.
            element.addEventListener('shown.bs.modal', function () {
                input.focus();
            });
        }
        window.addEventListener('hashchange', revealFromHash);
        revealFromHash();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    return { open: open, normalize: normalize };
})();
