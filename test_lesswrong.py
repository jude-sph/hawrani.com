"""Tests for the LessWrong fetcher. Network is stubbed -- no live calls."""

import json
import os
import tempfile
import urllib.error

import lesswrong


def sample_response(title="A Post", html="<p>body</p>"):
    """A well-formed GraphQL response body."""
    return {
        "data": {
            "post": {
                "result": {
                    "_id": "abc123",
                    "title": title,
                    "postedAt": "2026-07-10T00:31:48.557Z",
                    "pageUrl": "https://www.lesswrong.com/posts/abc123/a-post",
                    "user": {"displayName": "Hawrani"},
                    "contents": {"html": html, "wordCount": 42},
                }
            }
        }
    }


# A structurally faithful miniature of a GreaterWrong post page: header metadata,
# GW's injected table of contents and scripts, proxied images carrying the
# original URL in data-original-src, relative links, and a nested div in the body.
GW_SAMPLE_PAGE = """<html><head><title>A GW Post - LessWrong 2.0 viewer</title></head><body>
<main class="post"><h1 class="post-title">A GW &amp; Post</h1>
<div class="post-meta top-post-meta">
<a class="author" href="/users/hawrani" data-userid="x">Hawrani</a>
<span class="date hide-until-init" data-js-date=1783643508000>10 Jul 2026 0:31 UTC<script>prettyDate()</script></span>
<span class="read-time" title="1,915 words">6<span> min read</span></span>
<a class="lw2-link" href="https://www.lesswrong.com/posts/abc123/a-gw-post">LW<span> link</span></a>
</div>
<div class="body-text post-body"><figure style='--aspect-ratio: 1.5'><img src='/proxy-assets/AAA' data-original-src='https://res.cloudinary.com/img.png' srcset='/proxy-assets/AAA 1x, /proxy-assets/BBB 2x' loading='lazy' alt="hero.png"></figure><nav class="contents"><script>injectTOC()</script><div class="contents-head">Contents</div><ul><li><a href="#Sec">Sec</a></li></ul></nav><p>Hello <a href="/posts/zzz/other">other</a> <a href="#Sec">anchor</a></p><div class="imgonly"><picture class='invertible' data-original-src='https://res.cloudinary.com/img2.png'><source srcset='/proxy-assets/CCC-inverted' media='all and (prefers-color-scheme: dark)'><img src='/proxy-assets/CCC' loading='lazy' alt="fig.png"></picture></div><div class="nested"><p>inner</p></div></div>
<div class="comments">comments here</div></main></body></html>"""


def failing_transport(_payload):
    raise urllib.error.URLError("network down")


def failing_gw_transport(_lw_id):
    raise urllib.error.URLError("network down")


def setup_function(_):
    lesswrong.clear_cache()
    # Point both disk locations at a fresh temp dir so tests never touch the
    # real data/ files.
    tmp = tempfile.mkdtemp()
    lesswrong.DISK_CACHE_PATH = os.path.join(tmp, "cache.json")
    lesswrong.SEED_PATH = os.path.join(tmp, "seed.json")


def test_parses_successful_response():
    def transport(_payload):
        return sample_response()

    post = lesswrong.fetch_post("abc123", transport=transport)
    assert post["title"] == "A Post"
    assert post["html"] == "<p>body</p>"
    assert post["date"] == "2026-07-10"  # date-only slice of postedAt
    assert post["author"] == "Hawrani"
    assert post["url"].endswith("/a-post")
    assert post["word_count"] == 42


def test_serves_from_cache_within_ttl():
    calls = {"n": 0}

    def transport(_payload):
        calls["n"] += 1
        return sample_response(title=f"call {calls['n']}")

    clock = {"t": 1000.0}
    now = lambda: clock["t"]

    first = lesswrong.fetch_post("abc123", transport=transport, now=now)
    clock["t"] += lesswrong.CACHE_TTL - 1  # still fresh
    second = lesswrong.fetch_post("abc123", transport=transport, now=now)

    assert calls["n"] == 1  # only fetched once
    assert first["title"] == second["title"] == "call 1"


def test_refetches_after_ttl():
    calls = {"n": 0}

    def transport(_payload):
        calls["n"] += 1
        return sample_response(title=f"call {calls['n']}")

    clock = {"t": 1000.0}
    now = lambda: clock["t"]

    first = lesswrong.fetch_post("abc123", transport=transport, now=now)
    clock["t"] += lesswrong.CACHE_TTL + 1  # now stale
    second = lesswrong.fetch_post("abc123", transport=transport, now=now)

    assert calls["n"] == 2
    assert first["title"] == "call 1"
    assert second["title"] == "call 2"


def test_stale_fallback_on_error():
    state = {"fail": False}

    def transport(_payload):
        if state["fail"]:
            raise urllib.error.URLError("network down")
        return sample_response(title="good copy")

    clock = {"t": 1000.0}
    now = lambda: clock["t"]

    good = lesswrong.fetch_post("abc123", transport=transport, now=now)
    assert good["title"] == "good copy"

    # TTL expires and the live fetch now fails -> serve the stale cached copy.
    state["fail"] = True
    clock["t"] += lesswrong.CACHE_TTL + 1
    stale = lesswrong.fetch_post("abc123", transport=transport, now=now)
    assert stale["title"] == "good copy"


def test_returns_none_on_error_with_no_cache():
    def transport(_payload):
        raise urllib.error.URLError("network down")

    assert lesswrong.fetch_post("abc123", transport=transport) is None


def test_returns_none_for_unknown_post():
    def transport(_payload):
        return {"data": {"post": {"result": None}}}

    assert lesswrong.fetch_post("nope", transport=transport) is None


def test_extracts_first_image():
    def transport(_payload):
        return sample_response(html='<figure><img src="https://cdn/x.png" alt="hero"></figure><p>body</p>')

    post = lesswrong.fetch_post("abc123", transport=transport)
    assert post["image"] == "https://cdn/x.png"


def test_image_is_none_when_no_image():
    def transport(_payload):
        return sample_response(html="<p>text only</p>")

    post = lesswrong.fetch_post("abc123", transport=transport)
    assert post["image"] is None


def test_returns_none_when_body_missing():
    def transport(_payload):
        body = sample_response()
        body["data"]["post"]["result"]["contents"]["html"] = ""
        return body

    assert lesswrong.fetch_post("abc123", transport=transport) is None


# --- GreaterWrong mirror fallback ---


def test_gw_parser_extracts_metadata():
    post = lesswrong._parse_greaterwrong(GW_SAMPLE_PAGE, "abc123")
    assert post["id"] == "abc123"
    assert post["title"] == "A GW & Post"
    assert post["date"] == "2026-07-10"  # from the epoch-millis data-js-date
    assert post["author"] == "Hawrani"
    assert post["word_count"] == 1915
    assert post["url"] == "https://www.lesswrong.com/posts/abc123/a-gw-post"


def test_gw_parser_cleans_body():
    post = lesswrong._parse_greaterwrong(GW_SAMPLE_PAGE, "abc123")
    body = post["html"]
    # Balanced-div extraction: keeps nested content, stops before the comments.
    assert "<p>inner</p>" in body
    assert "comments here" not in body
    # Proxied image restored to its original URL; GW-only attributes dropped.
    assert 'src="https://res.cloudinary.com/img.png"' in body
    assert "proxy-assets" not in body
    assert "data-original-src" not in body
    # GW's injected TOC and scripts are gone.
    assert "<script" not in body
    assert "contents-head" not in body
    # GW's dark-mode <picture> wrapper collapses to a plain img pointing at
    # the original asset, keeping the img's own attributes.
    assert "<picture" not in body
    assert "<source" not in body
    assert 'src="https://res.cloudinary.com/img2.png"' in body
    assert 'alt="fig.png"' in body
    # Relative links become absolute LessWrong links; anchors are untouched.
    assert 'href="https://www.lesswrong.com/posts/zzz/other"' in body
    assert 'href="#Sec"' in body
    # The first image doubles as the cover, same as the GraphQL path.
    assert post["image"] == "https://res.cloudinary.com/img.png"


def test_gw_fallback_when_graphql_fails():
    def gw_transport(lw_id):
        assert lw_id == "abc123"
        return GW_SAMPLE_PAGE

    post = lesswrong.fetch_post("abc123", transport=failing_transport,
                                gw_transport=gw_transport)
    assert post["title"] == "A GW & Post"


def test_gw_result_is_cached():
    calls = {"n": 0}

    def gw_transport(_lw_id):
        calls["n"] += 1
        return GW_SAMPLE_PAGE

    lesswrong.fetch_post("abc123", transport=failing_transport, gw_transport=gw_transport)
    lesswrong.fetch_post("abc123", transport=failing_transport, gw_transport=gw_transport)
    assert calls["n"] == 1


# --- Disk persistence ---


def test_disk_cache_survives_restart():
    def transport(_payload):
        return sample_response(title="good copy")

    lesswrong.fetch_post("abc123", transport=transport)
    lesswrong.clear_cache()  # simulate a worker restart

    post = lesswrong.fetch_post("abc123", transport=failing_transport,
                                gw_transport=failing_gw_transport)
    assert post["title"] == "good copy"


def test_seed_file_serves_when_nothing_else_does():
    seed_post = {"id": "abc123", "title": "seeded", "html": "<p>x</p>", "date": "2026-07-10",
                 "author": "Hawrani", "url": "https://www.lesswrong.com/posts/abc123",
                 "word_count": 1, "image": None}
    with open(lesswrong.SEED_PATH, "w") as f:
        json.dump({"abc123": {"fetched_at": 0, "post": seed_post}}, f)

    post = lesswrong.fetch_post("abc123", transport=failing_transport,
                                gw_transport=failing_gw_transport)
    assert post["title"] == "seeded"


def test_returns_none_when_all_sources_fail():
    assert lesswrong.fetch_post("abc123", transport=failing_transport,
                                gw_transport=failing_gw_transport) is None
