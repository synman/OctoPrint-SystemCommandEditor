/*
 * System Command Editor: icon suggestions for the icon inputs.
 *
 * search(list, text, limit) turns free text into Font Awesome 6 class suggestions. list is window.SceIcons: entries
 * [name, styles, label, words], styles a string of s (solid), r (regular) and b (brands). The module is pure: no DOM,
 * jQuery or Knockout. It is exported for Node tests and set on window in the browser.
 */
(function () {
    "use strict";

    const STYLE_NAMES = {s: "solid", r: "regular", b: "brands"};
    const STYLE_ORDER = "srb";
    const STYLE_TOKENS = {"fa-solid": "s", "fa-regular": "r", "fa-brands": "b"};
    const BARE_TOKENS = {solid: "s", regular: "r", brands: "b"};
    const DEFAULT_LIMIT = 40;

    function parse(text) {
        const words = String(text || "").trim().toLowerCase().split(/\s+/).filter(Boolean);
        let style = null;
        const kept = [];
        for (let i = 0; i < words.length; i++) {
            const w = words[i];
            if (STYLE_TOKENS[w]) {
                style = STYLE_TOKENS[w];
                continue;
            }
            // a bare style word counts only as the first of two or more tokens, so "solid" alone is a search
            if (i === 0 && words.length >= 2 && BARE_TOKENS[w]) {
                style = BARE_TOKENS[w];
                continue;
            }
            kept.push(w.indexOf("fa-") === 0 ? w.slice(3) : w);
        }
        return {query: kept.filter(Boolean).join("-"), style: style};
    }

    function rank(name, label, words, query) {
        if (name === query) return 0;
        if (name.indexOf(query) === 0) return 1;
        if (name.indexOf(query) !== -1) return 2;
        if (label.toLowerCase().indexOf(query) !== -1 || words.toLowerCase().indexOf(query) !== -1) return 3;
        return -1;
    }

    function search(list, text, limit) {
        const max = limit === undefined ? DEFAULT_LIMIT : limit;
        const parsed = parse(text);
        if (!parsed.query && !parsed.style) return {items: [], more: 0};

        const hits = [];
        for (const icon of list || []) {
            const name = icon[0];
            const styles = icon[1];
            const label = icon[2] || "";
            const words = icon[3] || "";
            const r = parsed.query ? rank(name, label, words, parsed.query) : 0;
            if (r < 0) continue;
            for (const style of STYLE_ORDER) {
                if (styles.indexOf(style) === -1) continue;
                if (parsed.style && parsed.style !== style) continue;
                hits.push({rank: r, name: name, style: style, label: label});
            }
        }

        hits.sort((a, b) =>
            a.rank - b.rank ||
            a.name.length - b.name.length ||
            (a.name < b.name ? -1 : a.name > b.name ? 1 : 0) ||
            STYLE_ORDER.indexOf(a.style) - STYLE_ORDER.indexOf(b.style));

        const items = hits.slice(0, max).map((h) => ({
            cls: "fa-" + STYLE_NAMES[h.style] + " fa-" + h.name,
            name: h.name,
            style: STYLE_NAMES[h.style],
            label: h.label,
        }));
        return {items: items, more: hits.length - items.length};
    }

    const api = {search: search};

    if (typeof module !== "undefined" && module.exports) module.exports = api;
    if (typeof window !== "undefined") window.SceIconSearch = api;
})();
