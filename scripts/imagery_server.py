#!/usr/bin/env python3
"""Imagery server for the house viewer: Vexcel oblique aerial views (N / E / S / W) of the sample houses.

    python scripts/imagery_server.py            # http://127.0.0.1:8000, the target of the Vite /api proxy

Routes (the viewer reaches them through `npm run dev`, which forwards /api to :8000):
    GET /api/house-viewer/vexcel/<sample>                     -> {"views": {dir: {file, captureDate}}, ...}
    GET /api/house-viewer/vexcel/<sample>/oblique-<dir>.jpg   -> JPEG

Coordinates come from apps/web/public/house-viewer/samples/index.json. The key is VEXCEL_API_KEY from the
environment or from the repo's .env (gitignored). It is sent only in the X-API-KEY header (Vexcel API 2.0
"Header" scheme), never in a URL, a log line, a response or the cache. Images are cached under
data/cache/vexcel/<sample>/ (gitignored; Vexcel imagery is licensed, do not commit it). Read-only GET
requests to Vexcel: one coverage query plus one extract per direction, only on a cache miss.

Direction convention (checked visually against a known house): product-type oblique-<dir> is the direction the
camera faces, so oblique-south shows the north-facing facades.
Standard library only.
"""
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(ROOT, "apps", "web", "public", "house-viewer", "samples", "index.json")
CACHE = os.path.join(ROOT, "data", "cache", "vexcel")
ENV_FILE = os.path.join(ROOT, ".env")
BASE = "https://api.vexcelgroup.com/v2"
DIRS = ["north", "east", "south", "west"]
LAYERS = ["urban", "wide-area"]  # urban for cities, wide-area as regional coverage
BOX_M = 90                       # side (m) of the box requested for each oblique view
COPYRIGHT = "© Vexcel Imaging"
PREFIX = "/api/house-viewer/vexcel/"
_locks, _glock = {}, threading.Lock()


class VXError(Exception):
    def __init__(self, msg, status=502):
        super().__init__(msg)
        self.status = status


def api_key():
    key = os.environ.get("VEXCEL_API_KEY", "").strip()
    if key:
        return key
    try:
        for line in open(ENV_FILE, encoding="utf-8"):
            k, _, v = line.strip().partition("=")
            if k.strip().removeprefix("export ").strip() == "VEXCEL_API_KEY":
                return v.strip().strip("'\"")
    except OSError:
        pass
    return ""


def call(path, params, binary=False):
    key = api_key()
    if not key:
        raise VXError("VEXCEL_API_KEY is not set (add it to .env)", 503)
    req = urllib.request.Request(BASE + path + "?" + urllib.parse.urlencode(params), headers={"X-API-KEY": key, "Accept": "*/*"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                body = r.read()
                return r.status, body if binary else (json.loads(body) if body else None)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep(float(e.headers.get("Retry-After") or 5))
                continue
            return e.code, None
        except urllib.error.URLError as e:
            raise VXError("cannot reach Vexcel (%s)" % e.reason)
    return 429, None


def samples():
    return {s["id"]: s for s in json.load(open(SAMPLES, encoding="utf-8"))}


def obliques(sample_id, lat, lon):
    """Meta of the oblique views of one sample, from cache or from Vexcel (cache miss)."""
    folder = os.path.join(CACHE, sample_id)
    meta_path = os.path.join(folder, "meta.json")
    with _glock:
        lock = _locks.setdefault(sample_id, threading.Lock())
    with lock:
        if os.path.exists(meta_path):
            return json.load(open(meta_path, encoding="utf-8"))
        point = {"wkt": "POINT(%f %f)" % (lon, lat), "srid": "4326"}
        st, js = call("/oriented/collections", dict(point, **{"product-type": "oblique",
                      "include": "layer,collection,first-capture-date,last-capture-date,product-type"}))
        if st in (401, 403):
            raise VXError("Vexcel rejected the key (HTTP %d)" % st, st)
        cols = [f.get("properties", {}) for f in (js or {}).get("features", [])] if st == 200 else []
        os.makedirs(folder, exist_ok=True)
        meta = {"source": "Vexcel API 2.0", "copyright": COPYRIGHT, "convention": "oblique-<dir> = direction the camera faces",
                "views": {}, "notes": [] if st == 200 else ["oriented/collections HTTP %s" % st]}
        for d in DIRS:
            pt = "oblique-" + d
            cand = [c for c in cols if c.get("product-type") in (pt, "oblique", None)] or cols
            layer = next((l for l in LAYERS if any(c.get("layer") == l for c in cand)), None)
            params = dict(point, **{"product-type": pt, "bands": "rgb", "bbox-dimensions": "%d,%d" % (BOX_M, BOX_M), "image-format": "jpeg",
                                    # newest collection and, within it, the photo with the house closest to the center
                                    "order-by": "collection-last-capture-date-desc,image-center-distance-asc"})
            if layer:
                params["layer"] = layer
            st, img = call("/oriented/extract", params, binary=True)
            if st in (401, 403):
                raise VXError("Vexcel rejected the key (HTTP %d)" % st, st)
            if st == 200 and img and img[:2] == b"\xff\xd8":
                open(os.path.join(folder, pt + ".jpg"), "wb").write(img)
                info = sorted([c for c in cols if c.get("product-type") == pt], key=lambda c: c.get("last-capture-date") or "", reverse=True)
                meta["views"][d] = {"file": pt + ".jpg", "captureDate": (info[0].get("last-capture-date") or info[0].get("first-capture-date")) if info else None}
            else:
                meta["notes"].append("oriented/extract %s HTTP %s" % (pt, st))
        meta["fetchedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        json.dump(meta, open(meta_path, "w", encoding="utf-8"), indent=1)
        return meta


class Handler(BaseHTTPRequestHandler):
    def send(self, status, body, ctype="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "private, max-age=86400" if ctype == "image/jpeg" else "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def json(self, status, obj):
        self.send(status, json.dumps(obj).encode())

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if not path.startswith(PREFIX):
            return self.json(404, {"error": "not found"})
        parts = path[len(PREFIX):].strip("/").split("/")
        sample = samples().get(parts[0])
        if not sample or len(parts) > 2 or not sample.get("location"):
            return self.json(404, {"error": "unknown sample"})
        try:
            meta = obliques(sample["id"], sample["location"]["lat"], sample["location"]["lon"])
        except VXError as e:  # messages never include the key
            return self.json(e.status, {"error": str(e)})
        except Exception as e:
            return self.json(500, {"error": "unexpected error: %s" % type(e).__name__})
        if len(parts) == 1:
            return self.json(200, {k: meta[k] for k in ("source", "copyright", "convention", "views")})
        m = re.fullmatch(r"oblique-(north|east|south|west)\.jpg", parts[1])
        file = os.path.join(CACHE, sample["id"], parts[1]) if m and m.group(1) in meta["views"] else None
        if not file or not os.path.exists(file):
            return self.json(404, {"error": "no image for this view"})
        self.send(200, open(file, "rb").read(), "image/jpeg")

    def log_message(self, fmt, *args):  # paths only; the key never appears in a request line
        sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), fmt % args))


def main():
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8000
    print("imagery server on http://127.0.0.1:%d  (key %s)" % (port, "found" if api_key() else "MISSING: add VEXCEL_API_KEY to .env"))
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
