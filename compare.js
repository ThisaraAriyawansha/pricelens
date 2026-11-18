/*
 * PriceLens comparison engine (runs entirely in the browser; no server needed).
 * Downloads each Shopify store's public /products.json feed, matches products by
 * title and compares variant prices. JavaScript port of the original compare_prices.py.
 */
(function (root) {
  "use strict";

  const OLD = "https://www.greenie.ae";
  const NEW = "https://greenplants.ae";
  const MIN_MATCH = 0.85; // title similarity needed to treat two products as the same

  const OK = "OK";
  const MISMATCH = "PRICE MISMATCH";
  const VARIANT_MISSING = "VARIANT NOT FOUND ON NEW SITE";
  const PRODUCT_MISSING = "MISSING ON NEW SITE";

  const RETRY_HTTP = new Set([429, 430, 500, 502, 503, 504]); // 430 = Shopify rate limit
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  // Same as Python's round(): exact halves go to the even digit (11.25 -> 11.2, 0.625 -> 0.62).
  function round(x, d) {
    const exact = Math.abs(x).toFixed(100);          // exact decimal expansion of the binary value
    const cut = exact.indexOf(".") + 1 + d;
    const rest = exact.slice(cut);
    if (/^50*$/.test(rest)) {                         // exactly halfway
      const kept = exact.slice(0, cut);
      const last = +kept.replace(".", "").slice(-1);
      const down = Number(kept), up = Number((down + 10 ** -d).toFixed(d));
      return Math.sign(x) * (last % 2 === 0 ? down : up) || 0;
    }
    return Number(x.toFixed(d)) || 0;
  }

  class FetchError extends Error {
    constructor(kind, site, detail) { super(detail || kind); this.kind = kind; this.site = site; this.status = detail; }
  }

  // ---------- downloading ----------
  async function getJSON(url, site, log, tries = 6) {
    for (let attempt = 1; ; attempt++) {
      let wait, err;
      let res;
      try {
        res = await fetch(url, { cache: "no-store" });
      } catch (e) {
        // Network failure, or the site doesn't allow browser access (CORS). Fewer retries:
        // a site that blocks browsers will never succeed, so don't make the user wait long.
        if (attempt >= 3) throw new FetchError("network", site);
        wait = 5 * attempt; err = "connection failed";
      }
      if (res) {
        if (res.ok) {
          try { return await res.json(); } catch { throw new FetchError("notjson", site); }
        }
        if (!RETRY_HTTP.has(res.status) || attempt >= tries) throw new FetchError("http", site, res.status);
        wait = parseInt(res.headers.get("Retry-After"), 10) || 5 * attempt;
        err = `HTTP ${res.status}`;
      }
      log(`  ${err} -- retry ${attempt} in ${wait}s`);
      await sleep(wait * 1000);
    }
  }

  const CACHE = new Map(); // base URL -> {when, products}; in memory only, gone when the tab closes

  async function fetchAll(base, useCache, log) {
    if (useCache && CACHE.has(base)) {
      const { when, products } = CACHE.get(base);
      log(`${base}: reusing data downloaded at ${when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} this session`);
      return products.map(p => ({ ...p }));
    }
    let products = [];
    for (let page = 1; ; page++) {
      const data = await getJSON(`${base}/products.json?limit=250&page=${page}`, base, log);
      const batch = (data && data.products) || [];
      if (!batch.length) break;
      products = products.concat(batch);
      log(`${base}: page ${page}, total ${products.length}`);
      await sleep(1500);
    }
    CACHE.set(base, { when: new Date(), products });
    return products.map(p => ({ ...p }));
  }

  // ---------- title matching ----------
  function norm(t) {
    t = String(t || "").toLowerCase();
    t = t.replace(/\(.*?\)/g, " ");      // drop "(small)", "(Indoor)" etc.
    t = t.replace(/[^a-z0-9 ]/g, " ");
    return t.split(" ").filter(Boolean).join(" ");
  }

  /* Faithful port of Python's difflib.SequenceMatcher(None, a, b) (with autojunk),
     so similarity scores match the original Python tool exactly. */
  function prepB(b) {
    const b2j = new Map();
    for (let i = 0; i < b.length; i++) {
      const c = b[i];
      let l = b2j.get(c);
      if (!l) b2j.set(c, l = []);
      l.push(i);
    }
    const n = b.length;
    if (n >= 200) {
      const ntest = Math.floor(n / 100) + 1;
      for (const [c, idxs] of [...b2j]) if (idxs.length > ntest) b2j.delete(c);
    }
    const counts = new Map();
    for (const c of b) counts.set(c, (counts.get(c) || 0) + 1);
    return { s: b, b2j, counts };
  }

  function findLongestMatch(a, B, alo, ahi, blo, bhi) {
    const b = B.s, b2j = B.b2j;
    let besti = alo, bestj = blo, bestsize = 0;
    let j2len = new Map();
    for (let i = alo; i < ahi; i++) {
      const newj2len = new Map();
      const idxs = b2j.get(a[i]);
      if (idxs) {
        for (const j of idxs) {
          if (j < blo) continue;
          if (j >= bhi) break;
          const k = (j2len.get(j - 1) || 0) + 1;
          newj2len.set(j, k);
          if (k > bestsize) { besti = i - k + 1; bestj = j - k + 1; bestsize = k; }
        }
      }
      j2len = newj2len;
    }
    // No junk function is used, so only the "popular" (autojunk) extension applies.
    while (besti > alo && bestj > blo && a[besti - 1] === b[bestj - 1]) { besti--; bestj--; bestsize++; }
    while (besti + bestsize < ahi && bestj + bestsize < bhi && a[besti + bestsize] === b[bestj + bestsize]) bestsize++;
    return [besti, bestj, bestsize];
  }

  function matchingChars(a, B) {
    const la = a.length, lb = B.s.length;
    const queue = [[0, la, 0, lb]];
    let total = 0;
    while (queue.length) {
      const [alo, ahi, blo, bhi] = queue.pop();
      const [i, j, k] = findLongestMatch(a, B, alo, ahi, blo, bhi);
      if (k) {
        total += k;
        if (alo < i && blo < j) queue.push([alo, i, blo, j]);
        if (i + k < ahi && j + k < bhi) queue.push([i + k, ahi, j + k, bhi]);
      }
    }
    return total;
  }

  const calc = (m, len) => len ? 2 * m / len : 1;

  function realQuickRatio(a, B) {
    const la = a.length, lb = B.s.length;
    return calc(Math.min(la, lb), la + lb);
  }

  function quickRatio(a, B) {
    const avail = new Map();
    let matches = 0;
    for (const c of a) {
      const n = avail.has(c) ? avail.get(c) : (B.counts.get(c) || 0);
      avail.set(c, n - 1);
      if (n > 0) matches++;
    }
    return calc(matches, a.length + B.s.length);
  }

  const ratio = (a, B) => calc(matchingChars(a, B), a.length + B.s.length);

  function bestMatch(title, candidates) {
    const n = norm(title);
    let best = null, score = 0;
    for (const c of candidates) {
      if (n === c._n) return [c, 1.0];
      if (realQuickRatio(n, c._sm) <= score || quickRatio(n, c._sm) <= score) continue; // can't beat current best
      const s = ratio(n, c._sm);
      if (s > score) { best = c; score = s; }
    }
    return [best, score];
  }

  function vprices(p) {
    const m = new Map();
    for (const v of p.variants || []) m.set(norm(v.title), parseFloat(v.price));
    return m;
  }

  // ---------- comparison ----------
  async function compare(oldP, newP, oldBase = OLD, newBase = NEW, minMatch = MIN_MATCH, progress = null) {
    for (const p of newP) { p._n = norm(p.title); p._sm = prepB(p._n); }
    const link = (base, p) => p && p.handle ? `${base}/products/${p.handle}` : "";

    const rows = [];
    let lastYield = Date.now();
    for (let i = 1; i <= oldP.length; i++) {
      const o = oldP[i - 1];
      if (progress && (i % 25 === 0 || i === oldP.length)) progress(i, oldP.length);
      if (Date.now() - lastYield > 50) { await sleep(0); lastYield = Date.now(); } // keep the page responsive

      const [m, score] = bestMatch(o.title, newP);
      const base = { old_title: o.title, old_url: link(oldBase, o), score: round(score, 2),
                     new_title: m ? m.title : "", new_url: link(newBase, m) };
      if (!m || score < minMatch) {
        rows.push({ ...base, variant: "", old_price: null, new_price: null, diff: null, pct: null, status: PRODUCT_MISSING });
        continue;
      }
      const ov = vprices(o), nv = vprices(m);
      const ovs = [...ov], nvs = [...nv];
      ovs.forEach(([vt, op], j) => {
        let npx;
        if (nv.has(vt)) npx = nv.get(vt);
        else if (ovs.length === nvs.length) npx = nvs[j][1]; // same number of variants -> compare by position
        else npx = null;
        let status, diff, pct;
        if (npx == null) { status = VARIANT_MISSING; diff = null; pct = null; }
        else {
          diff = round(npx - op, 2);
          pct = op ? round(diff / op * 100, 1) : null;
          status = Math.abs(diff) < 0.01 ? OK : MISMATCH;
        }
        rows.push({ ...base, variant: vt, old_price: op, new_price: npx, diff, pct, status });
      });
    }
    for (const p of newP) { delete p._n; delete p._sm; }
    return rows;
  }

  // ---------- helpers for the UI ----------
  function cleanUrl(u, label) {
    u = String(u || "").trim();
    if (!u) throw new Error(`Please enter the ${label} URL.`);
    if (!u.includes("://")) u = "https://" + u;
    let p;
    try { p = new URL(u); } catch { p = null; }
    if (!p || !["http:", "https:"].includes(p.protocol) || !p.host.includes("."))
      throw new Error(`The ${label} URL “${u}” doesn't look like a website address.`);
    return `${p.protocol}//${p.host.toLowerCase()}`;
  }

  function friendly(e) {
    if (!(e instanceof FetchError)) return e && e.message ? e.message : `Unexpected error: ${e}`;
    const site = e.site;
    if (e.kind === "http") {
      if (e.status === 404) return `${site} has no /products.json feed (HTTP 404). Is it a Shopify store?`;
      if (e.status === 401 || e.status === 403) return `${site} refused access (HTTP ${e.status}). The store may be password-protected.`;
      return `${site} returned HTTP ${e.status} even after several retries. Try again in a minute.`;
    }
    if (e.kind === "notjson") return `${site} didn't return product data. It may not be a Shopify store, or it's behind a password page.`;
    return `Couldn't load products from ${site}. Check the URL and your internet connection. ` +
           `The site must be a public Shopify store (other sites block browser access).`;
  }

  async function run({ old, new: neu, minMatch = MIN_MATCH, useCache = false }, { log = () => {}, stage = () => {}, progress = () => {} } = {}) {
    stage("Downloading products from the old site…");
    const oldP = await fetchAll(old, useCache, log);
    log(`Old site: ${oldP.length} products`);
    stage("Downloading products from the new site…");
    const newP = await fetchAll(neu, useCache, log);
    log(`New site: ${newP.length} products`);
    if (!oldP.length || !newP.length) throw new Error(`${!oldP.length ? old : neu} returned 0 products.`);
    stage("Matching products and comparing prices…");
    return compare(oldP, newP, old, neu, minMatch, (i, t) => progress(i / t));
  }

  const api = { OLD, NEW, MIN_MATCH, OK, MISMATCH, VARIANT_MISSING, PRODUCT_MISSING,
                norm, compare, fetchAll, cleanUrl, friendly, run };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.PriceLens = api;
})(typeof window !== "undefined" ? window : globalThis);
