"""
Compare prices: old site (greenie.ae) vs new site (greenplants.ae).
Uses each Shopify store's public /products.json feed. Python 3.8+, no installs needed.

This module is the comparison engine used by the web UI (python app.py).
Nothing is written to disk: downloaded products are kept in memory only, and
reports are downloaded from the browser with the UI's download buttons.
"""
import json, re, time, urllib.error, urllib.request
from datetime import datetime
from difflib import SequenceMatcher

OLD = "https://www.greenie.ae"
NEW = "https://greenplants.ae"
MIN_MATCH = 0.85  # title similarity needed to treat two products as the same

OK = "OK"
MISMATCH = "PRICE MISMATCH"
VARIANT_MISSING = "VARIANT NOT FOUND ON NEW SITE"
PRODUCT_MISSING = "MISSING ON NEW SITE"

RETRY_HTTP = {429, 430, 500, 502, 503, 504}  # 430 = Shopify rate limit


def get_json(url, tries=6, log=print):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for attempt in range(1, tries + 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code not in RETRY_HTTP or attempt == tries:
                raise
            wait = int(e.headers.get("Retry-After") or 5 * attempt)
            err = f"HTTP {e.code}"
        except (urllib.error.URLError, OSError) as e:  # timeouts, resets, DNS hiccups
            if attempt == tries:
                raise
            wait, err = 5 * attempt, e
        log(f"  {err} -- retry {attempt}/{tries - 1} in {wait}s")
        time.sleep(wait)


CACHE = {}  # base URL -> (download time, products); in memory only, gone when the app stops


def fetch_all(base, use_cache=True, log=print):
    if use_cache and base in CACHE:
        when, products = CACHE[base]
        log(f"{base}: reusing data downloaded at {when:%H:%M} this session")
        return [dict(p) for p in products]
    products, page = [], 1
    while True:
        batch = get_json(f"{base}/products.json?limit=250&page={page}", log=log).get("products", [])
        if not batch:
            break
        products += batch
        log(f"{base}: page {page}, total {len(products)}")
        page += 1
        time.sleep(1.5)
    CACHE[base] = (datetime.now(), products)
    return [dict(p) for p in products]


def norm(t):
    t = t.lower()
    t = re.sub(r"\(.*?\)", " ", t)          # drop "(small)", "(Indoor)" etc.
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    return " ".join(t.split())


def best_match(title, candidates):
    n, best, score = norm(title), None, 0.0
    for c in candidates:
        if n == c["n"]:
            return c, 1.0
        sm = SequenceMatcher(None, n, c["n"])
        if sm.real_quick_ratio() <= score or sm.quick_ratio() <= score:
            continue  # cheap upper bounds: can't beat the current best
        s = sm.ratio()
        if s > score:
            best, score = c, s
    return best, score


def vprices(p):
    return {norm(v["title"]): float(v["price"]) for v in p["variants"]}


def compare(old, new, old_base=OLD, new_base=NEW, min_match=MIN_MATCH, progress=None):
    """Return one dict per old-site variant (or per missing product)."""
    for p in new:
        p["n"] = norm(p["title"])
    link = lambda base, p: f"{base}/products/{p['handle']}" if p and p.get("handle") else ""

    rows = []
    for i, o in enumerate(old, 1):
        if progress and (i % 25 == 0 or i == len(old)):
            progress(i, len(old))
        m, score = best_match(o["title"], new)
        base = {"old_title": o["title"], "old_url": link(old_base, o), "score": round(score, 2),
                "new_title": m["title"] if m else "", "new_url": link(new_base, m)}
        if not m or score < min_match:
            rows.append({**base, "variant": "", "old_price": None, "new_price": None,
                         "diff": None, "pct": None, "status": PRODUCT_MISSING})
            continue
        ov, nv = vprices(o), vprices(m)
        ovs, nvs = list(ov.items()), list(nv.items())
        for j, (vt, op) in enumerate(ovs):
            if vt in nv:
                npx = nv[vt]
            elif len(ovs) == len(nvs):      # same number of variants -> compare by position
                npx = nvs[j][1]
            else:
                npx = None
            if npx is None:
                status, diff, pct = VARIANT_MISSING, None, None
            else:
                diff = round(npx - op, 2)
                pct = round(diff / op * 100, 1) if op else None
                status = OK if abs(diff) < 0.01 else MISMATCH
            rows.append({**base, "variant": vt, "old_price": op, "new_price": npx,
                         "diff": diff, "pct": pct, "status": status})
    return rows
