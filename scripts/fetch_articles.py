#!/usr/bin/env python3
"""
抓取英文媒体 RSS，生成 articles.json 供 H5 外刊页渲染。
GitHub Actions 每天定时跑一次，结果 commit 回仓库。
"""
import feedparser
import json
import re
from datetime import datetime, timezone
from html import unescape

SOURCES = [
    {
        "name": "The Guardian",
        "url": "https://www.theguardian.com/international/rss",
        "color": "#052962",
        "desc": "卫报 · 国际新闻",
    },
    {
        "name": "BBC World",
        "url": "https://feeds.bbci.co.uk/news/world/rss.xml",
        "color": "#B84A32",
        "desc": "BBC · 世界新闻",
    },
    {
        "name": "TED Talks",
        "url": "https://feeds.feedburner.com/tedtalks_video",
        "color": "#E62B1E",
        "desc": "TED · 精彩演讲",
    },
]

MAX_PER_SOURCE = 5


def clean_html(raw):
    """去掉 HTML 标签，截断摘要"""
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", "", raw)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:200]


def parse_date(entry):
    """统一成 ISO 格式"""
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc).isoformat()
    return ""


def fetch_source(src):
    feed = feedparser.parse(src["url"])
    items = []
    for entry in feed.entries[:MAX_PER_SOURCE]:
        items.append({
            "source": src["name"],
            "sourceColor": src["color"],
            "sourceDesc": src["desc"],
            "title": entry.get("title", "").strip(),
            "link": entry.get("link", ""),
            "summary": clean_html(entry.get("summary", entry.get("description", ""))),
            "published": parse_date(entry),
        })
    return items


def main():
    all_items = []
    for src in SOURCES:
        try:
            all_items.extend(fetch_source(src))
            print(f"OK: {src['name']} fetched")
        except Exception as e:
            print(f"FAIL: {src['name']} - {e}")

    # 按发布时间倒序
    all_items.sort(key=lambda x: x["published"], reverse=True)

    output = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "count": len(all_items),
        "articles": all_items,
    }

    with open("articles.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"Done: {len(all_items)} articles written to articles.json")


if __name__ == "__main__":
    main()
