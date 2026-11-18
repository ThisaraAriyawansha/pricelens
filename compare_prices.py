"""
Compare prices: old site (greenie.ae) vs new site (greenplants.ae).
Uses each Shopify store's public /products.json feed. Python 3.8+, no installs needed.

Run:  python compare_prices.py          (command line, writes CSVs)
      python app.py                     (web UI in your browser)
Output: price_comparison.csv  (every variant, with status)
        price_issues.csv      (only mismatches + products missing on new site)
"""
import csv, json, os, re, time, urllib.error, urllib.request
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


def cache_path(base):
    return re.sub(r"\W+", "_", base.split("//")[1]) + ".json"


def fetch_all(base, use_cache=True, log=print):
    cache = cache_path(base)
    if use_cache and os.path.exists(cache):
        when = datetime.fromtimestamp(os.path.getmtime(cache)).strftime("%Y-%m-%d %H:%M")
        log(f"{base}: using saved data from {when} ({cache})")
        with open(cache, encoding="utf-8") as f:
            return json.load(f)
    products, page = [], 1
    while True:
        batch = get_json(f"{base}/products.json?limit=250&page={page}", log=log).get("products", [])
        if not batch:
            break
        products += batch
        log(f"{base}: page {page}, total {len(products)}")
        page += 1
        time.sleep(1.5)
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(products, f)
    return products


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


COLUMNS = [("old_title", "old_title"), ("new_title", "new_title"), ("score", "match_score"),
           ("variant", "variant"), ("old_price", "old_price_AED"), ("new_price", "new_price_AED"),
           ("diff", "difference"), ("pct", "difference_pct"), ("status", "status"),
           ("old_url", "old_url"), ("new_url", "new_url")]


def write_csv(name, rows):
    def write(path):
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow([h for _, h in COLUMNS])
            w.writerows([["" if r[k] is None else r[k] for k, _ in COLUMNS] for r in rows])
        return path
    try:
        return write(name)
    except PermissionError:  # usually: the file is open in Excel
        alt = f"{os.path.splitext(name)[0]}_{datetime.now():%Y%m%d_%H%M%S}.csv"
        print(f"  {name} is locked (open in Excel?) -- saving as {alt} instead")
        return write(alt)


def main():
    old, new = fetch_all(OLD), fetch_all(NEW)
    rows = compare(old, new)
    issues = [r for r in rows if r["status"] != OK]
    write_csv("price_comparison.csv", rows)
    path = write_csv("price_issues.csv", issues)
    print(f"Done. {len(rows)} rows compared, {len(issues)} issues -> {path}")
    print("Tip: sort by match_score and spot-check rows below 0.95 manually.")
    print("Cached product data is reused on the next run; delete the .json files to re-download.")


if __name__ == "__main__":
    main()
