#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# overclockers-exporter
# ========================
# Archive an entire overclockers.ru blog to a local, offline-readable copy:
# clean per-article HTML + full-resolution images, plus a browsable index.
#
# Usage
# -----
#     python overclockers_exporter.py <blog-url-or-name> [options]
# 
# Examples
# --------
#     python overclockers_exporter.py https://overclockers.ru/blog/Hard-Workshop
#     python overclockers_exporter.py Hard-Workshop --out ./my-archive
#     python overclockers_exporter.py .../blog/Hard-Workshop/show/123/foo --limit 5
#
# The tool is resumable: re-running skips articles already downloaded
# (tracked by a ".done" marker per article folder).

import argparse
import html as htmllib
import json
import os
import re
import sys
import time
from urllib.parse import urljoin, urlparse

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("Missing deps. Run:  pip install -r requirements.txt")

# Console may be cp1252/other; force UTF-8 so non-ASCII logging never crashes.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE = "https://overclockers.ru"
DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "overclockers_exporter/1.0 (+https://github.com/)")
PAGE_STEP = 20  # articles per listing page (site default)

# Body container candidates, in priority order. The site uses different
# templates for blog posts vs. news-format posts.
BODY_SELECTORS = [
    ('attr', {"itemprop": "articleBody"}),
    ('css', ".fr-view"),
    ('css', ".nl-text"),          # news-format posts
    ('tag', "article"),
]


def log(msg):
    print(time.strftime("[%H:%M:%S]"), msg, flush=True)


# ----------------------------- HTTP -----------------------------
class Fetcher:
    def __init__(self, ua, timeout, delay, tries=4):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": ua,
                               "Accept-Language": "ru,en;q=0.8"})
        self.timeout = timeout
        self.delay = delay
        self.tries = tries

    def get(self, url, binary=False):
        last = None
        for i in range(self.tries):
            try:
                r = self.s.get(url, timeout=self.timeout)
                if r.status_code == 200:
                    return r.content if binary else r.text
                last = "HTTP %s" % r.status_code
                if r.status_code in (404, 410):
                    break
            except Exception as e:  # noqa: BLE001 - network resilience
                last = repr(e)
            time.sleep(1.5 * (i + 1))
        raise RuntimeError("GET failed %s: %s" % (url, last))

    def polite_sleep(self):
        if self.delay:
            time.sleep(self.delay)


# --------------------------- helpers ----------------------------
def parse_blog_name(arg):
    """Accept a full URL, a /blog/<name>/... path, or a bare blog name."""
    arg = arg.strip()
    m = re.search(r"/blog/([^/?#]+)", arg)
    if m:
        return m.group(1)
    # bare name (no slashes/scheme)
    if "/" not in arg and "." not in arg:
        return arg
    raise SystemExit("Could not extract a blog name from: %r\n"
                     "Pass e.g. https://overclockers.ru/blog/Hard-Workshop" % arg)


def safe_name(s, maxlen=80):
    s = re.sub(r"[^0-9A-Za-zА-Яа-яЁё._-]+", "-", s).strip("-")
    return s[:maxlen] or "item"


def best_img_url(img):
    """Return the highest-quality real image URL for a lazy-loaded <img>."""
    for attr in ("data-src", "data-original"):
        v = img.get(attr)
        if v and "logo_gray_stub" not in v:
            return v
    src = img.get("src", "")
    if src and "logo_gray_stub" not in src and "/assets/" not in src:
        return src
    ss = img.get("data-srcset") or img.get("srcset")
    if ss:
        cand = ss.split(",")[-1].strip().split(" ")[0]
        if cand:
            return cand
    return None


def find_body(soup):
    for kind, sel in BODY_SELECTORS:
        if kind == 'attr':
            node = soup.find(attrs=sel)
        elif kind == 'css':
            node = soup.select_one(sel)
        else:
            node = soup.find(sel)
        if node is not None:
            return node
    return None


# ------------------------ phase 1: index ------------------------
def collect_urls(fetch, blog, out, max_pages):
    index_file = os.path.join(out, "index.json")
    if os.path.exists(index_file):
        with open(index_file, encoding="utf-8") as f:
            data = json.load(f)
        log("index.json exists -> %d articles (reuse; delete to re-scan)"
            % len(data))
        return data, index_file

    pat = re.compile(r"/blog/%s/show/(\d+)/([^/?#]+)" % re.escape(blog))
    seen, order, offset, pages = {}, [], 0, 0
    while True:
        url = "%s/blog/%s?offset=%d&max=%d" % (BASE, blog, offset, PAGE_STEP)
        soup = BeautifulSoup(fetch.get(url), "html.parser")
        found = 0
        for a in soup.find_all("a", href=True):
            m = pat.search(a["href"])
            if not m:
                continue
            aid = m.group(1)
            if aid in seen:
                continue
            seen[aid] = True
            order.append({"id": aid, "slug": m.group(2),
                          "url": urljoin(BASE, a["href"].split("?")[0]),
                          "title": " ".join(a.get_text(strip=True).split())})
            found += 1
        log("offset %d: +%d new (total %d)" % (offset, found, len(order)))
        pages += 1
        if found == 0 and offset > 0:
            break
        if max_pages and pages >= max_pages:
            log("reached --max-pages %d, stopping scan" % max_pages)
            break
        offset += PAGE_STEP
        fetch.polite_sleep()

    with open(index_file, "w", encoding="utf-8") as f:
        json.dump(order, f, ensure_ascii=False, indent=1)
    log("collected %d article URLs -> index.json" % len(order))
    return order, index_file


# ----------------------- phase 2: article -----------------------
def download_article(fetch, rec, out, idx, total, save_raw=True):
    folder = os.path.join(out, "%s_%s" % (rec["id"], safe_name(rec["slug"])))
    done = os.path.join(folder, ".done")
    if os.path.exists(done):
        return "skip", folder
    os.makedirs(folder, exist_ok=True)

    html = fetch.get(rec["url"])
    if save_raw:
        with open(os.path.join(folder, "page_raw.html"), "w",
                  encoding="utf-8") as f:
            f.write(html)

    soup = BeautifulSoup(html, "html.parser")

    title = rec.get("title") or rec["slug"]
    t = soup.find("title")
    if t and t.get_text(strip=True):
        title = t.get_text(strip=True)

    date = ""
    dt = soup.find(attrs={"itemprop": "datePublished"})
    if dt:
        date = dt.get("content") or dt.get_text(strip=True)

    body = find_body(soup)
    if body is None:
        raise RuntimeError("no article body found")

    imgdir = os.path.join(folder, "images")
    n_img = 0
    for img in body.find_all("img"):
        u = best_img_url(img)
        if not u:
            continue
        u = urljoin(BASE, u)
        name = safe_name(os.path.basename(urlparse(u).path) or "img", 120)
        if "." not in name:
            name += ".jpg"
        local = os.path.join(imgdir, name)
        if not os.path.exists(local):
            try:
                data = fetch.get(u, binary=True)
                os.makedirs(imgdir, exist_ok=True)
                with open(local, "wb") as f:
                    f.write(data)
                fetch.polite_sleep()
            except Exception as e:  # noqa: BLE001
                log("  img fail %s: %s" % (u, e))
                continue
        for a in ("data-src", "data-srcset", "srcset", "data-original",
                  "sizes", "class"):
            if img.has_attr(a):
                del img[a]
        img["src"] = "images/" + name
        n_img += 1

    write_article_html(folder, title, date, rec["url"], body)

    with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({"id": rec["id"], "title": title, "date": date,
                   "url": rec["url"], "images": n_img, "folder":
                   os.path.basename(folder)}, f, ensure_ascii=False, indent=1)
    with open(done, "w") as f:
        f.write("ok")
    log("[%d/%d] OK %s  (%d imgs)  %s" % (idx, total, rec["id"], n_img,
                                          title[:60]))
    return "ok", folder


ARTICLE_CSS = (
    "body{max-width:820px;margin:24px auto;padding:0 16px;"
    "font:16px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;color:#1a1a1a}"
    "img{max-width:100%;height:auto;display:block;margin:12px auto}"
    ".meta{color:#666;font-size:14px;margin-bottom:20px}"
    "h1{font-size:24px;line-height:1.3}a{color:#0b62c4}"
    "table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:4px}"
)


def write_article_html(folder, title, date, url, body):
    esc = htmllib.escape
    head = (
        "<!DOCTYPE html><html lang='ru'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>%s</title><style>%s</style></head><body>"
        "<p><a href='../index.html'>&larr; all articles</a></p>"
        "<h1>%s</h1><div class='meta'>%s &middot; <a href='%s'>original</a>"
        "</div>" % (esc(title), ARTICLE_CSS, esc(title), esc(date), esc(url))
    )
    out_html = head + body.decode_contents() + "</body></html>"
    with open(os.path.join(folder, "index.html"), "w", encoding="utf-8") as f:
        f.write(out_html)


# ----------------------- master index --------------------------
def build_master_index(out, blog):
    items = []
    for name in os.listdir(out):
        meta_path = os.path.join(out, name, "meta.json")
        if os.path.isfile(meta_path):
            try:
                with open(meta_path, encoding="utf-8") as f:
                    items.append(json.load(f))
            except Exception:
                pass
    items.sort(key=lambda m: m.get("date", ""), reverse=True)
    esc = htmllib.escape
    rows = []
    for m in items:
        link = "%s/index.html" % m.get("folder", "")
        rows.append(
            "<li><a href='%s'>%s</a><span class='m'> &middot; %s &middot; "
            "%d img</span></li>" % (esc(link), esc(m.get("title", "?")),
                                    esc((m.get("date", "") or "")[:10]),
                                    m.get("images", 0)))
    html = (
        "<!DOCTYPE html><html lang='ru'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>%s — archive</title><style>"
        "body{max-width:900px;margin:24px auto;padding:0 16px;"
        "font:16px/1.6 -apple-system,Segoe UI,Roboto,sans-serif}"
        "h1{font-size:22px}ol{padding-left:22px}li{margin:6px 0}"
        ".m{color:#888;font-size:13px}a{color:#0b62c4;text-decoration:none}"
        "a:hover{text-decoration:underline}.c{color:#666;margin-bottom:16px}"
        "</style></head><body><h1>%s</h1>"
        "<div class='c'>%d articles &middot; offline archive of "
        "<a href='%s/blog/%s'>overclockers.ru/blog/%s</a></div><ol>%s</ol>"
        "</body></html>" % (esc(blog), esc(blog), len(items), BASE, esc(blog),
                            esc(blog), "".join(rows)))
    path = os.path.join(out, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path, len(items)


# ------------------------------ CLI -----------------------------
def main(argv=None):
    p = argparse.ArgumentParser(
        description="Archive an overclockers.ru blog (HTML + images), "
                    "offline-readable and resumable.")
    p.add_argument("blog", help="blog URL or name, e.g. "
                                 "https://overclockers.ru/blog/Hard-Workshop "
                                 "or just Hard-Workshop")
    p.add_argument("--out", help="output directory (default: ./<blog-name>)")
    p.add_argument("--delay", type=float, default=0.4,
                   help="seconds between requests (default 0.4; be polite)")
    p.add_argument("--timeout", type=int, default=30,
                   help="per-request timeout in seconds (default 30)")
    p.add_argument("--limit", type=int, default=0,
                   help="only download the first N articles (0 = all)")
    p.add_argument("--max-pages", type=int, default=0,
                   help="cap listing pages scanned (0 = until empty)")
    p.add_argument("--no-raw", action="store_true",
                   help="do not save the raw page HTML (saves disk space)")
    p.add_argument("--user-agent", default=DEFAULT_UA,
                   help="custom User-Agent string")
    args = p.parse_args(argv)

    blog = parse_blog_name(args.blog)
    out = os.path.abspath(args.out or blog)
    os.makedirs(out, exist_ok=True)
    log("blog=%s  out=%s" % (blog, out))

    fetch = Fetcher(args.user_agent, args.timeout, args.delay)
    arts, _ = collect_urls(fetch, blog, out, args.max_pages)
    if args.limit:
        arts = arts[:args.limit]
    total = len(arts)

    ok = skip = 0
    failures = []
    try:
        for i, rec in enumerate(arts, 1):
            try:
                status, _ = download_article(fetch, rec, out, i, total,
                                             save_raw=not args.no_raw)
                if status == "ok":
                    ok += 1
                    fetch.polite_sleep()
                else:
                    skip += 1
            except Exception as e:  # noqa: BLE001
                log("[%d/%d] FAIL %s: %s" % (i, total, rec["id"], e))
                failures.append({"id": rec["id"], "url": rec["url"],
                                 "error": str(e)})
    except KeyboardInterrupt:
        log("interrupted — saving progress (re-run to resume)")

    with open(os.path.join(out, "failures.json"), "w", encoding="utf-8") as f:
        json.dump(failures, f, ensure_ascii=False, indent=1)
    idx_path, n = build_master_index(out, blog)
    log("DONE total=%d ok=%d skip=%d fail=%d" % (total, ok, skip,
                                                 len(failures)))
    log("index: %s  (%d articles archived)" % (idx_path, n))
    if failures:
        log("failures listed in failures.json")


if __name__ == "__main__":
    main()
