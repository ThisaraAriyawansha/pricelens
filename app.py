"""
Price Compare web UI.  Run:  python app.py   (opens your browser; no installs needed)

A tiny local server: the browser can't read other stores' /products.json directly
(cross-origin), so this server fetches them and runs the comparison from compare_prices.py.
"""
import json, os, socket, threading, uuid, webbrowser
import urllib.error
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import compare_prices as cp

HERE = os.path.dirname(os.path.abspath(__file__))
JOBS = {}


def clean_url(u, label):
    u = (u or "").strip()
    if not u:
        raise ValueError(f"Please enter the {label} URL.")
    if "://" not in u:
        u = "https://" + u
    p = urlparse(u)
    if p.scheme not in ("http", "https") or "." not in p.netloc:
        raise ValueError(f"The {label} URL “{u}” doesn't look like a website address.")
    return f"{p.scheme}://{p.netloc.lower()}"


def friendly(e, site):
    if isinstance(e, urllib.error.HTTPError):
        if e.code == 404:
            return f"{site} has no /products.json feed (HTTP 404). Is it a Shopify store?"
        if e.code in (401, 403):
            return f"{site} refused access (HTTP {e.code}). The store may be password-protected."
        return f"{site} returned HTTP {e.code} even after several retries. Try again in a minute."
    if isinstance(e, json.JSONDecodeError):
        return f"{site} didn't return product data. It may not be a Shopify store, or it's behind a password page."
    if isinstance(e, (urllib.error.URLError, OSError)):
        reason = getattr(e, "reason", e)
        return f"Couldn't connect to {site} ({reason}). Check the URL and your internet connection, then try again."
    return f"Unexpected error: {e}"


def run_job(job, old, new, min_match, use_cache):
    log = job["log"].append
    site = old
    try:
        job["stage"] = "Downloading products from the old site…"
        old_p = cp.fetch_all(old, use_cache, log)
        log(f"Old site: {len(old_p)} products")
        site = new
        job["stage"] = "Downloading products from the new site…"
        new_p = cp.fetch_all(new, use_cache, log)
        log(f"New site: {len(new_p)} products")
        if not old_p or not new_p:
            raise ValueError(f"{old if not old_p else new} returned 0 products.")
        job["stage"] = "Matching products and comparing prices…"
        rows = cp.compare(old_p, new_p, old, new, min_match,
                          progress=lambda i, t: job.update(progress=i / t))
        job.update(result=rows, status="done", stage="Done")
    except ValueError as e:
        job.update(status="error", error=str(e))
    except Exception as e:
        job.update(status="error", error=friendly(e, site))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/":
            with open(os.path.join(HERE, "ui.html"), "rb") as f:
                return self.send(200, f.read(), "text/html; charset=utf-8")
        if url.path == "/api/config":
            return self.send(200, {"old": cp.OLD, "new": cp.NEW, "min_match": cp.MIN_MATCH})
        if url.path == "/api/job":
            job = JOBS.get(parse_qs(url.query).get("id", [""])[0])
            if not job:
                return self.send(404, {"error": "Unknown job"})
            out = {k: v for k, v in job.items() if k != "result"}
            if job["status"] == "done":
                out["result"] = job["result"]
            return self.send(200, out)
        self.send(404, {"error": "Not found"})

    def do_POST(self):
        if self.path != "/api/compare":
            return self.send(404, {"error": "Not found"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            old, new = clean_url(body.get("old"), "old site"), clean_url(body.get("new"), "new site")
            if old == new:
                raise ValueError("The old and new site URLs are the same.")
            min_match = min(max(float(body.get("min_match", cp.MIN_MATCH)), 0.5), 1.0)
        except (ValueError, TypeError) as e:
            return self.send(400, {"error": str(e)})
        jid = uuid.uuid4().hex
        JOBS[jid] = {"status": "running", "stage": "Starting…", "progress": 0, "log": [],
                     "old": old, "new": new}
        for k in list(JOBS)[:-5]:  # keep only the last few results in memory
            JOBS.pop(k, None)
        threading.Thread(target=run_job, daemon=True,
                         args=(JOBS[jid], old, new, min_match, bool(body.get("use_cache")))).start()
        self.send(200, {"id": jid, "old": old, "new": new})


def free_port(start=8765):
    for port in range(start, start + 50):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("No free port found")


if __name__ == "__main__":
    os.chdir(HERE)  # keep the cached .json files next to the scripts
    port = free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"Price Compare running at {url}  (press Ctrl+C to stop)")
    threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
