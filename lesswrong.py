"""Fetch LessWrong posts via their public GraphQL API, with layered fallbacks.

This module knows nothing about Flask. It exposes a single entry point,
``fetch_post(lw_id)``, which returns a normalized dict describing a post or
``None`` if the post could not be retrieved and nothing usable is cached.

Design notes:
- Uses stdlib ``urllib`` so there is no extra dependency to install on the
  shared host (``requests`` is not available there).
- Results are cached per post id for ``CACHE_TTL`` seconds. Within the TTL a
  cached copy is returned without a network call.
- LessWrong sits behind Vercel bot protection which sometimes answers plain
  HTTP clients with a 429 JavaScript challenge no server can pass. When the
  GraphQL fetch fails, we fall back to scraping the same post from
  GreaterWrong (the LessWrong mirror), which serves plain HTML.
- Every successful fetch is persisted to ``DISK_CACHE_PATH`` (gitignored, so
  the server's writes never conflict with ``git pull`` deploys). If both live
  sources fail and nothing is in memory, the last-known-good copy is served
  from that file, or failing that from ``SEED_PATH``, a snapshot committed to
  the repo. A LessWrong outage or block should never break the blog.
"""

import json
import os
import re
import ssl
import time
import urllib.request
import urllib.error
from html import unescape

# Build an SSL context backed by a reliable CA bundle. Some Python builds (e.g.
# the python.org macOS build) ship without configured system certificates, which
# makes HTTPS verification fail; certifi provides a portable bundle. We keep
# certificate verification ON -- if certifi is somehow unavailable we fall back
# to the platform default context rather than disabling verification.
try:
    import certifi
    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except Exception:  # pragma: no cover - depends on environment
    _SSL_CONTEXT = ssl.create_default_context()

GRAPHQL_URL = "https://www.lesswrong.com/graphql"

# The GreaterWrong mirror serves the same posts as plain HTML without the
# bot challenge; used as a fallback when the GraphQL endpoint is unreachable.
GREATERWRONG_URL = "https://www.greaterwrong.com/posts/{lw_id}"

# How long (seconds) a fetched post stays fresh before we re-fetch.
CACHE_TTL = 15 * 60

# Seconds to wait on the network before giving up on a live fetch.
REQUEST_TIMEOUT = 8

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Last successful fetch, written at runtime. Gitignored: deploys are a git
# pull, so the server's own writes must stay out of the tracked tree.
DISK_CACHE_PATH = os.path.join(_DATA_DIR, "lesswrong_cache.json")

# Committed snapshot, the fallback of last resort for a fresh checkout whose
# runtime cache is empty while LessWrong is unreachable.
SEED_PATH = os.path.join(_DATA_DIR, "lesswrong_seed.json")

_USER_AGENT = "Mozilla/5.0 (compatible; HawraniWeb/1.0)"

# GraphQL query: look up a single post by _id and return the fields we render.
_POST_QUERY = (
    "query($id: String) {"
    "  post(input: {selector: {_id: $id}}) {"
    "    result { _id title postedAt pageUrl user { displayName }"
    "             contents { html wordCount } }"
    "  }"
    "}"
)

# Module-level cache: {lw_id: (fetched_at_epoch, post_dict)}
_cache = {}


def _default_transport(payload):
    """Perform the GraphQL POST and return the parsed JSON response.

    Isolated so tests can inject a fake transport instead of hitting the
    network. Raises on any network/HTTP error.
    """
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        GRAPHQL_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            # LessWrong rejects requests without a browser-like User-Agent.
            "User-Agent": _USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT, context=_SSL_CONTEXT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _default_gw_transport(lw_id):
    """GET the post's GreaterWrong page and return the HTML text.

    Isolated so tests can inject a fake transport. Raises on any
    network/HTTP error.
    """
    req = urllib.request.Request(
        GREATERWRONG_URL.format(lw_id=lw_id),
        headers={"Accept": "text/html", "User-Agent": _USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT, context=_SSL_CONTEXT) as resp:
        return resp.read().decode("utf-8", "replace")


def _parse_response(body):
    """Turn a raw GraphQL response dict into our normalized post dict.

    Returns None if the expected fields are missing (unknown id, GraphQL
    errors, or an unexpected shape).
    """
    try:
        result = body["data"]["post"]["result"]
    except (TypeError, KeyError):
        return None
    if not result:
        return None

    contents = result.get("contents") or {}
    html = contents.get("html")
    if not html:
        # A post with no body is not useful to render.
        return None

    posted_at = result.get("postedAt") or ""
    # postedAt looks like "2026-07-10T00:31:48.557Z"; keep the date portion for
    # display and sorting alongside local posts (which use "YYYY-MM-DD").
    date = posted_at[:10] if posted_at else ""

    user = result.get("user") or {}

    # The first inline image doubles as a cover/hero for the post.
    img_match = re.search(r'<img[^>]+src="([^"]+)"', html)
    image = img_match.group(1) if img_match else None

    return {
        "id": result.get("_id"),
        "title": result.get("title") or "Untitled",
        "html": html,
        "date": date,
        "author": user.get("displayName"),
        "url": result.get("pageUrl") or "",
        "word_count": contents.get("wordCount"),
        "image": image,
    }


def _extract_post_body(page):
    """Return the inner HTML of GreaterWrong's post-body div, or None.

    The body contains nested divs, so we walk div tags counting depth rather
    than trusting a non-greedy match.
    """
    opening = re.search(r'<div[^>]*class=["\']body-text post-body["\'][^>]*>', page)
    if not opening:
        return None
    start = opening.end()
    depth = 1
    for tag in re.finditer(r"<div\b|</div>", page[start:]):
        depth += 1 if tag.group(0) == "<div" else -1
        if depth == 0:
            return page[start:start + tag.start()]
    return None


def _clean_gw_body(body):
    """Normalize GreaterWrong body HTML to look like LessWrong's own HTML."""
    # GreaterWrong injects its own table of contents and inline scripts;
    # LessWrong's GraphQL HTML has neither.
    body = re.sub(r'<nav class="contents">.*?</nav>', "", body, flags=re.S)
    body = re.sub(r"<script\b.*?</script>", "", body, flags=re.S)

    # GW wraps some images in <picture class="invertible"> carrying the
    # original URL on the picture tag, plus a dark-mode <source> that only
    # exists on GW's proxy. Collapse each to a plain img on the original
    # asset, which is what LessWrong's own HTML has.
    def fix_picture(block_match):
        block = block_match.group(0)
        orig = re.search(r'data-original-src=([\'"])(.*?)\1', block)
        img = re.search(r"<img\b[^>]*>", block)
        if not (orig and img):
            return block
        return re.sub(r'(?<!-)src=([\'"]).*?\1',
                      lambda _m: 'src="%s"' % orig.group(2), img.group(0), count=1)

    body = re.sub(r"<picture\b.*?</picture>", fix_picture, body, flags=re.S)

    # Standalone images are served via GreaterWrong's asset proxy, with the
    # original URL kept in data-original-src. Restore the original and drop
    # the GW-only attributes. (?<!-) keeps src= from matching inside
    # data-original-src=.
    def fix_img(tag_match):
        tag = tag_match.group(0)
        orig = re.search(r'data-original-src=([\'"])(.*?)\1', tag)
        if orig:
            tag = re.sub(r'(?<!-)src=([\'"]).*?\1',
                         lambda _m: 'src="%s"' % orig.group(2), tag, count=1)
            tag = re.sub(r'\s+data-original-src=([\'"]).*?\1', "", tag)
        tag = re.sub(r'\s+srcset=([\'"])[^\'"]*proxy-assets.*?\1', "", tag)
        return tag

    body = re.sub(r"<img\b[^>]*>", fix_img, body)

    # Anything still pointing at the proxy, and site-relative links, become
    # absolute so they work when served from our domain.
    body = re.sub(r'(src=[\'"])/proxy-assets/',
                  r"\g<1>https://www.greaterwrong.com/proxy-assets/", body)
    body = re.sub(r'(href=[\'"])/(?!/)', r"\g<1>https://www.lesswrong.com/", body)
    return body


def _parse_greaterwrong(page, lw_id):
    """Turn a GreaterWrong post page into the same normalized dict as
    ``_parse_response``. Returns None if no post body is found."""
    body = _extract_post_body(page)
    if not body:
        return None
    body = _clean_gw_body(body)

    m = re.search(r'<h1 class="post-title">(.*?)</h1>', page, re.S)
    title = unescape(re.sub(r"<[^>]+>", "", m.group(1)).strip()) if m else "Untitled"

    # The header carries the posting time as epoch milliseconds.
    m = re.search(r'data-js-date=["\']?(\d+)', page)
    date = time.strftime("%Y-%m-%d", time.gmtime(int(m.group(1)) / 1000)) if m else ""

    m = re.search(r'<a class="author"[^>]*>(.*?)</a>', page)
    author = unescape(m.group(1).strip()) if m else None

    m = re.search(r'class="read-time" title="([\d,]+) words?"', page)
    word_count = int(m.group(1).replace(",", "")) if m else None

    # GreaterWrong links every post back to its canonical LessWrong URL.
    m = re.search(r'<a class="lw2-link" href="([^"]+)"', page)
    url = m.group(1) if m else "https://www.lesswrong.com/posts/%s" % lw_id

    img_match = re.search(r'<img[^>]+src=[\'"]([^\'"]+)[\'"]', body)

    return {
        "id": lw_id,
        "title": title,
        "html": body,
        "date": date,
        "author": author,
        "url": url,
        "word_count": word_count,
        "image": img_match.group(1) if img_match else None,
    }


def _disk_store(lw_id, fetched_at, post):
    """Persist a successful fetch. Best-effort: a read-only filesystem or a
    racing writer must never break page rendering."""
    try:
        try:
            with open(DISK_CACHE_PATH, encoding="utf-8") as f:
                stored = json.load(f)
        except (OSError, ValueError):
            stored = {}
        stored[lw_id] = {"fetched_at": fetched_at, "post": post}
        tmp_path = DISK_CACHE_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(stored, f)
        os.replace(tmp_path, DISK_CACHE_PATH)
    except OSError:
        pass


def _disk_load(lw_id):
    """Return (fetched_at, post) for the last-known-good copy of ``lw_id``,
    checking the runtime cache first and the committed seed second."""
    for path in (DISK_CACHE_PATH, SEED_PATH):
        try:
            with open(path, encoding="utf-8") as f:
                entry = json.load(f)[lw_id]
            return (entry["fetched_at"], entry["post"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None


def fetch_post(lw_id, transport=None, gw_transport=None, now=None):
    """Return a normalized post dict for ``lw_id``, or None.

    Serves a cached copy while it is still fresh (within CACHE_TTL). Otherwise
    tries the GraphQL API, then the GreaterWrong mirror; on total failure falls
    back to any cached copy (however stale), then to the on-disk copy of the
    last successful fetch, before finally returning None.

    ``transport``, ``gw_transport`` and ``now`` are injectable for testing.
    Injecting ``transport`` alone disables the mirror fallback so existing
    tests exercise the GraphQL path without ever touching the network.
    """
    if transport is None and gw_transport is None:
        gw_transport = _default_gw_transport
    transport = transport or _default_transport
    now = now or time.time

    cached = _cache.get(lw_id)
    current_time = now()

    if cached is not None:
        fetched_at, data = cached
        if current_time - fetched_at < CACHE_TTL:
            return data

    # Cache is empty or stale -> attempt a live fetch.
    try:
        body = transport({"query": _POST_QUERY, "variables": {"id": lw_id}})
        post = _parse_response(body)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        post = None

    # GraphQL failed (e.g. LessWrong's bot challenge) -> try the mirror.
    if post is None and gw_transport is not None:
        try:
            post = _parse_greaterwrong(gw_transport(lw_id), lw_id)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            post = None

    if post is not None:
        _cache[lw_id] = (current_time, post)
        _disk_store(lw_id, current_time, post)
        return post

    # Both live sources failed. Serve a stale cached copy if we have one.
    if cached is not None:
        return cached[1]

    # Fresh process with nothing in memory: fall back to the last-known-good
    # copy on disk. Cached under its original timestamp (stale), so later
    # requests keep retrying the live sources.
    disk = _disk_load(lw_id)
    if disk is not None:
        _cache[lw_id] = disk
        return disk[1]

    return None


def clear_cache():
    """Drop all cached posts. Used by tests."""
    _cache.clear()
