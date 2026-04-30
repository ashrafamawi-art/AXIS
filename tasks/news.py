"""
AXIS news fetcher — pulls AI stories from public RSS feeds via stdlib only.

Primary source : Google News RSS (AI query)
Fallback       : Hacker News top-stories API
"""

import json
import re
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone


_GOOGLE_NEWS_URL = (
    "https://news.google.com/rss/search"
    "?q=artificial+intelligence+AI+machine+learning"
    "&hl=en-US&gl=US&ceid=US:en"
)
_HN_TOP_URL     = "https://hacker-news.firebaseio.com/v0/topstories.json"
_HN_ITEM_URL    = "https://hacker-news.firebaseio.com/v0/item/{}.json"
_AI_KEYWORDS    = {"ai", "gpt", "llm", "openai", "anthropic", "gemini", "claude",
                   "machine learning", "deep learning", "neural", "generative",
                   "artificial intelligence", "large language"}


def _fetch_url(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "AXIS/1.0 (+https://github.com/axis)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    # decode common HTML entities
    for entity, char in [("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                          ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")]:
        text = text.replace(entity, char)
    return re.sub(r"\s+", " ", text).strip()


def _resolve_google_link(redirect_url: str) -> str:
    """Follow Google News redirect to get the real article URL."""
    try:
        req = urllib.request.Request(
            redirect_url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; AXIS/1.0)"},
        )
        # Don't follow redirect — just extract the destination from Location header
        opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler())
        with opener.open(req, timeout=5) as resp:
            return resp.url
    except Exception:
        return redirect_url


def _from_google_news(count: int) -> list[dict]:
    data = _fetch_url(_GOOGLE_NEWS_URL)
    root = ET.fromstring(data)
    stories = []
    for item in root.findall(".//item"):
        title  = _strip_html(item.findtext("title") or "")
        link   = _strip_html(item.findtext("link")  or "")
        raw_desc = _strip_html(item.findtext("description") or "")
        pub    = item.findtext("pubDate") or ""
        source = item.findtext("source") or "Google News"

        # Google News description echoes "title  source" — extract real blurb
        # from the content:encoded field if available, else skip the echo
        desc = raw_desc
        if desc.startswith(title.split(" - ")[0][:30]):
            desc = ""   # suppress title echo

        # Resolve redirect URL to actual article link
        real_link = _resolve_google_link(link) if link.startswith("https://news.google.com") else link

        stories.append({
            "title":       title.split(" - ")[0].strip(),   # drop trailing "- Source" from title
            "source":      title.split(" - ")[-1].strip() if " - " in title else source,
            "link":        real_link,
            "description": desc[:500],
            "pub_date":    pub,
        })
        if len(stories) >= count:
            break
    return stories


def _from_hackernews(count: int) -> list[dict]:
    raw = _fetch_url(_HN_TOP_URL)
    ids = json.loads(raw)
    stories = []
    for sid in ids:
        if len(stories) >= count * 5:   # over-fetch, then filter
            break
        try:
            item_raw = _fetch_url(_HN_ITEM_URL.format(sid))
            item = json.loads(item_raw)
            title = item.get("title", "")
            url   = item.get("url",   f"https://news.ycombinator.com/item?id={sid}")
            score = item.get("score", 0)
            if any(kw in title.lower() for kw in _AI_KEYWORDS):
                stories.append({
                    "title":       title,
                    "link":        url,
                    "description": f"HN score: {score} | {item.get('descendants', 0)} comments",
                    "pub_date":    datetime.fromtimestamp(item.get("time", 0), tz=timezone.utc)
                                   .strftime("%a, %d %b %Y %H:%M:%S +0000"),
                    "source":      "Hacker News",
                })
        except Exception:
            continue
        if len(stories) >= count:
            break
    return stories[:count]


def fetch_ai_news(count: int = 3) -> list[dict]:
    """
    Return `count` AI news stories as a list of dicts:
      title, link, description, pub_date, source
    Tries Google News first, falls back to Hacker News.
    """
    try:
        stories = _from_google_news(count)
        if stories:
            return stories
    except Exception as exc:
        pass   # fall through to HN

    return _from_hackernews(count)
