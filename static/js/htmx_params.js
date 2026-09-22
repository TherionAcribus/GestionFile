// Paramètres HTMX déclaratifs — compatibles avec `script-src 'self'`.
//
// Avant ce point, les gabarits passaient les valeurs dynamiques aux requêtes
// HTMX via `hx-vals="js:{...}"` et `hx-on::...` : htmx compile ces expressions
// avec `Function(...)`, ce que la CSP interdit (pas de 'unsafe-eval'). Les
// requêtes partaient donc sans leurs paramètres (ou ne partaient pas).
//
// Désormais :
// - les valeurs STATIQUES restent dans `hx-vals` en JSON strict (parsé par
//   JSON.parse, jamais évalué) ;
// - les valeurs lues dans le DOM sont déclarées par des attributs
//   `data-param-*` sur l'élément déclencheur, résolus ici au moment de
//   `htmx:configRequest` — donc toujours à jour, comme le faisait `js:` ;
// - les paramètres trop riches pour un attribut (objets construits, tableaux
//   agrégés) passent par un collecteur nommé `data-params-fn`, résolu dans le
//   registre `HX_PARAM_COLLECTORS` (aucune évaluation de chaîne).
//
// Syntaxe des attributs :
//   data-param-<nom>="<sélecteur>"            -> el.value (défaut)
//   data-param-<nom>="<sélecteur>@checked"    -> el.checked   (booléen)
//   data-param-<nom>="<sélecteur>@selected"   -> el.selected  (booléen, <option>)
//   data-param-<nom>="<sélecteur>@text"       -> el.innerText
//   data-param-<nom>="<sélecteur>@html"       -> el.innerHTML
//   data-param-<nom>="<sélecteur>@options"    -> valeurs des <option> sélectionnées
//   data-param-<nom>="<sélecteur>@order"      -> data-id de TOUS les éléments
//                                               correspondants (listes triables)
//   data-param-<nom>-append="<texte>"         -> concaténé à la valeur lue
//   data-params-fn="<collecteur>"             -> HX_PARAM_COLLECTORS[nom](elt, params)
//   data-params-scope="<sélecteur>"           -> racine de recherche du collecteur
//
// Un élément introuvable produit '' (ou false / [] selon le mode), comme le
// faisait l'idiome `(document.getElementById('x')||{}).value || ''`.

(function () {
    'use strict';

    // Défense en profondeur : désactive toutes les voies d'évaluation de htmx
    // (hx-vals js:, hx-on, filtres de hx-trigger). Aucun gabarit ne s'en sert
    // plus ; un éventuel oubli échoue alors proprement (htmx:evalDisallowedError)
    // au lieu de produire une violation CSP.
    if (window.htmx && htmx.config) {
        htmx.config.allowEval = false;
    }

    // Registre extensible : les pages riches (couleurs, permissions) y posent
    // leur collecteur sans modifier ce fichier.
    var HX_PARAM_COLLECTORS = window.HX_PARAM_COLLECTORS || {};
    window.HX_PARAM_COLLECTORS = HX_PARAM_COLLECTORS;

    function findElement(selector) {
        try {
            return document.querySelector(selector);
        } catch (e) {
            // Sélecteur invalide pour querySelector (ex. id contenant des
            // espaces ou des points) : repli sur getElementById, qui accepte
            // n'importe quel id.
            if (selector.charAt(0) === '#') {
                return document.getElementById(selector.substring(1));
            }
            return null;
        }
    }

    function readParamValue(selector, mode) {
        if (mode === 'order') {
            try {
                return Array.prototype.map.call(
                    document.querySelectorAll(selector),
                    function (el) { return el.getAttribute('data-id'); }
                );
            } catch (e) {
                return [];
            }
        }
        var el = findElement(selector);
        if (!el) {
            return (mode === 'checked' || mode === 'selected') ? false
                : (mode === 'options') ? [] : '';
        }
        switch (mode) {
            case 'checked':  return !!el.checked;
            case 'selected': return !!el.selected;
            case 'text':     return el.innerText;
            case 'html':     return el.innerHTML;
            case 'options':  return Array.prototype.map.call(
                el.selectedOptions || [],
                function (o) { return o.value; }
            );
            // Élément non-champ (ou sans valeur) : '' comme le faisait
            // l'idiome `(document.getElementById('x')||{}).value || ''`.
            default:         return el.value == null ? '' : el.value;
        }
    }

    document.addEventListener('htmx:configRequest', function (evt) {
        // htmx 2.x ne met pas `elt` dans le detail : l'évènement est émis sur
        // l'élément déclencheur lui-même -> evt.target.
        var elt = (evt.detail && evt.detail.elt) || evt.target;
        if (!elt || !elt.attributes) { return; }
        var params = evt.detail.parameters;
        var appends = {};
        var i, attr, name;

        // Les suffixes -append sont relevés d'abord : l'ordre des attributs
        // n'est pas garanti.
        for (i = 0; i < elt.attributes.length; i++) {
            attr = elt.attributes[i];
            if (attr.name.indexOf('data-param-') === 0
                    && attr.name.slice(-7) === '-append') {
                appends[attr.name.slice(11, -7)] = attr.value;
            }
        }

        for (i = 0; i < elt.attributes.length; i++) {
            attr = elt.attributes[i];
            if (attr.name.indexOf('data-param-') !== 0
                    || attr.name.slice(-7) === '-append') {
                continue;
            }
            name = attr.name.slice(11);
            // "<sélecteur>" ou "<sélecteur>@<mode>" — le mode est le dernier
            // segment, ce qui laisse les sélecteurs contenant '@' utilisables.
            var parts = attr.value.split('@');
            var mode = parts.length > 1 ? parts.pop() : 'value';
            var value = readParamValue(parts.join('@'), mode);
            if (appends[name] !== undefined) {
                value = '' + value + appends[name];
            }
            params[name] = value;
        }

        var fn = elt.getAttribute('data-params-fn');
        if (fn && typeof HX_PARAM_COLLECTORS[fn] === 'function') {
            HX_PARAM_COLLECTORS[fn](elt, params);
        }
    });

    // Collecteur partagé : cases `.permission-checkbox` -> objet JSON
    // {permission: bool} envoyé dans le paramètre `permissions`. La portée est
    // `data-params-scope` (un rôle en édition) ou le document entier
    // (formulaire de création).
    HX_PARAM_COLLECTORS.rolePermissions = function (elt, params) {
        var scopeSel = elt.getAttribute('data-params-scope');
        var root = scopeSel ? findElement(scopeSel) : document;
        var perms = {};
        (root || document).querySelectorAll('.permission-checkbox')
            .forEach(function (cb) {
                perms[cb.getAttribute('data-permission')] = !!cb.checked;
            });
        params['permissions'] = JSON.stringify(perms);
    };

    // Remplace le tri par en-tête : l'attribut onclick qui mettait à jour les
    // champs cachés {prefix}-sort / {prefix}-dir avant la requête. La requête
    // du <th> porte déjà sort/dir en hx-vals statique ; la mise à jour des
    // champs cachés sert les requêtes SUIVANTES (recherche, pagination).
    document.addEventListener('click', function (e) {
        var th = e.target.closest
            ? e.target.closest('th.sortable[data-sort-key]') : null;
        if (!th) { return; }
        var prefix = th.getAttribute('data-sort-prefix');
        var sort = document.getElementById(prefix + '-sort');
        var dir = document.getElementById(prefix + '-dir');
        if (sort) { sort.value = th.getAttribute('data-sort-key'); }
        if (dir) { dir.value = th.getAttribute('data-sort-dir'); }
    });

    // Remplace le filtre `hx-trigger="keyup[keyCode==13]"` (évalué via
    // Function() — interdit par la CSP) : `data-enter-trigger="evt"` émet
    // l'évènement `evt` sur l'élément quand Entrée est pressée dans un de ses
    // descendants, et `hx-trigger="evt"` le déclenche.
    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Enter' || !e.target || !e.target.closest) { return; }
        var el = e.target.closest('[data-enter-trigger]');
        if (el && window.htmx) {
            htmx.trigger(el, el.getAttribute('data-enter-trigger'));
        }
    });
})();
