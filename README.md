# overclockers-exporter

Archive an entire [overclockers.ru](https://overclockers.ru) blog to a local,
**offline-readable** copy — clean per-article HTML with **full-resolution
images** embedded locally, plus a browsable index of everything.

Point it at any blog URL and run it. It is **resumable**, polite to the
server, and survives crashes, sleep, and Ctrl+C.

```bash
python overclockers_exporter.py https://overclockers.ru/blog/Hard-Workshop
```

## Features

- **One argument in** — a blog URL, a `/show/…` article URL, or just the blog
  name (`Hard-Workshop`). The tool figures out the rest.
- **Full-res images** — the site lazy-loads thumbnails; this fetches the
  original (`_O`) images from `data-src` and rewrites the HTML to local paths,
  so articles render fully offline.
- **Clean article HTML** — extracts just the article body (blog *and*
  news-format templates), wraps it in a minimal styled page.
- **Browsable archive** — generates a master `index.html` listing every
  article (title, date, image count), sorted newest-first.
- **Resumable** — each finished article gets a `.done` marker; re-running skips
  what is already saved. Safe to interrupt and continue later.
- **Polite & robust** — configurable delay between requests, retries with
  backoff, per-article error isolation (one bad page never aborts the run).

## Install

Requires **Python 3.9+**.

```bash
pip install -r requirements.txt
```

## Usage

```bash
python overclockers_exporter.py <blog-url-or-name> [options]
```

| Option           | Default        | Description                                        |
|------------------|----------------|----------------------------------------------------|
| `--out DIR`      | `./<blog-name>`| Output directory                                   |
| `--delay SEC`    | `0.4`          | Delay between requests (raise it to be gentler)    |
| `--timeout SEC`  | `30`           | Per-request timeout                                |
| `--limit N`      | `0` (all)      | Only download the first N articles (handy to test) |
| `--max-pages N`  | `0` (all)      | Cap how many listing pages are scanned             |
| `--no-raw`       | off            | Don't keep the raw page HTML (saves disk space)    |
| `--user-agent S` | built-in       | Custom User-Agent                                  |

### Examples

```bash
# Full backup into a tidy folder
python overclockers_exporter.py Hard-Workshop --out archives/Hard-Workshop

# Quick test: just the 3 most recent articles
python overclockers_exporter.py https://overclockers.ru/blog/Hard-Workshop --limit 3

# Gentler crawl, no raw-page copies
python overclockers_exporter.py Hard-Workshop --delay 1.0 --no-raw
```

## Output layout

```
<out>/
├── index.html            ← browsable list of all articles (open this)
├── index.json            ← machine-readable list of discovered articles
├── failures.json         ← anything that could not be saved (empty = all good)
└── <id>_<slug>/
    ├── index.html        ← the article, offline-readable, images inlined locally
    ├── images/           ← full-resolution images
    ├── meta.json         ← id, title, date, source URL, image count
    └── page_raw.html     ← original page as fetched (omit with --no-raw)
```

Open the top-level `index.html` in any browser to read the whole archive
offline.

## How resume works

Re-run the same command at any time. Articles with a `.done` marker are
skipped; only missing or interrupted ones are fetched. Delete an article folder
to force a re-download, or delete `index.json` to re-scan the blog for new
posts.

## Notes & etiquette

- This is for **personal archival / backup**. Respect the site's content and
  authors; don't hammer the server — the default delay is deliberate, and you
  can raise it with `--delay`.
- Selectors target overclockers.ru's current templates (blog + news formats).
  If the site changes its markup, update `BODY_SELECTORS` in the script.
- Co-edit with Claude Opus 4.8 by Anthropic

## License

MIT — see [LICENSE](LICENSE).
