#!/usr/bin/env python3
"""Check: Arch Linux news items from the last 30 days that require manual intervention.

The feed is cached in $AA_STATE_DIR/arch-news.xml for 6 hours. With AA_NEWS_OFFLINE=1
(or when the download fails) only the cache is used; no cache means severity "none".
"""
import json
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path

FEED_URL = "https://archlinux.org/feeds/news/"
CACHE_TTL = 6 * 3600
MAX_AGE_DAYS = 30
TRIGGERS = ("manual intervention", "requires manual", "requires action")


def feed_text(cache: Path) -> str | None:
    fresh = cache.is_file() and time.time() - cache.stat().st_mtime < CACHE_TTL
    if os.environ.get("AA_NEWS_OFFLINE") or fresh:
        return cache.read_text() if cache.is_file() else None
    try:
        with urllib.request.urlopen(FEED_URL, timeout=10) as r:
            text = r.read().decode("utf-8")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(text)
        return text
    except Exception as e:  # network down, DNS, HTTP error: fall back to cache
        print(f"feed download failed: {e}", file=sys.stderr)
        return cache.read_text() if cache.is_file() else None


def packages_from(title: str) -> list[str]:
    """Package-looking words from the title (lowercase, before 'requires'/'>=' etc.)."""
    head = re.split(r"\s+(?:>=|<=|=|requires|require|needs|and)\s+", title, maxsplit=1)[0]
    words = re.findall(r"[a-z0-9][a-z0-9+._-]*", head.lower())
    return [w for w in words if len(w) > 2 and w not in {"the", "new", "for", "with", "from"}]


def main() -> None:
    cache = Path(os.environ.get("AA_STATE_DIR", ".")) / "arch-news.xml"
    text = feed_text(cache)
    empty = {"id": "arch-news", "severity": "none", "title": "No Arch news requiring intervention", "summary": ""}
    if not text:
        print(json.dumps(empty))
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    items = []
    for it in ET.fromstring(text).iter("item"):
        title = unescape(it.findtext("title", "") or "")
        desc = unescape(re.sub(r"<[^>]+>", " ", it.findtext("description", "") or ""))
        try:
            date = parsedate_to_datetime(it.findtext("pubDate", "") or "")
        except (TypeError, ValueError):
            continue
        if date < cutoff:
            continue
        if not any(t in (title + " " + desc).lower() for t in TRIGGERS):
            continue
        items.append({"name": title, "title": title, "date": date.date().isoformat(),
                      "link": it.findtext("link", "") or "", "packages": packages_from(title)})
    if not items:
        print(json.dumps(empty))
        return
    print(json.dumps({
        "id": "arch-news", "severity": "high",
        "title": (f"{len(items)} Arch news items require manual intervention" if len(items) != 1
                  else "1 Arch news item requires manual intervention"),
        "summary": "; ".join(i["title"] for i in items), "items": items, "actions": [],
    }))


if __name__ == "__main__":
    main()
