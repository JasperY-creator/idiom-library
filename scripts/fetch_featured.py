#!/usr/bin/env python3
"""
抓取 TED Talks RSS，调 DeepSeek 自动挑选 3 篇适合口语学习的演讲，
生成中文导读和重点表达，输出 featured.json 供 H5 渲染。
GitHub Actions 每两天跑一次。
"""
import feedparser
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from html import unescape

SOURCES = [
    {
        "name": "TED Talks",
        "url": "https://feeds.feedburner.com/tedtalks_video",
    },
]

MAX_PER_SOURCE = 10
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"


def clean_html(raw):
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", "", raw)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:300]


def parse_date(entry):
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc).isoformat()
    return ""


def fetch_source(src):
    feed = feedparser.parse(src["url"])
    items = []
    for entry in feed.entries[:MAX_PER_SOURCE]:
        link = entry.get("link", "")
        # 过滤掉播客/精选集（链接都是同一个首页，不是具体演讲）
        if "audiocollective.ted.com" in link:
            continue
        if not link.startswith("http"):
            continue
        items.append({
            "source": src["name"],
            "title": entry.get("title", "").strip(),
            "link": link,
            "summary": clean_html(entry.get("summary", entry.get("description", ""))),
            "published": parse_date(entry),
        })
    return items


def call_deepseek(api_key, prompt):
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "你是一个英语学习博主，擅长挑选有趣、有料的英文演讲，并从中提取能日常用到的地道表达。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        DEEPSEEK_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]


def pick_and_generate(all_articles, api_key):
    article_brief = "\n".join([
        f"{i+1}. {a['title']}\n   摘要: {a['summary'][:150]}\n   链接: {a['link']}"
        for i, a in enumerate(all_articles)
    ])

    prompt = f"""以下是最新的 {len(all_articles)} 个 TED 演讲。请从中挑选 3 个最值得看的：

{article_brief}

挑选标准：
1. 话题有趣、贴近生活（教育、成长、人际关系、科技、文化等）
2. 难度适中，不是太学术太硬核
3. 看完能有收获、有共鸣

对每个选中的演讲，生成以下内容：
- title: 英文标题
- source: 来源（就写 "TED"）
- link: 原文链接（必须从上面列表里原封不动复制，不要自己编）
- guide: 中文导读（2-3句话，简单说这个演讲讲了什么、适合聊什么话题的时候参考）
- points: 3个重点表达，每个包含 phrase（英文短语）和 example（一句中文例句）

注意：不要在导读里提"雅思"两个字，就是普通的英语学习分享语气。

请严格按 JSON 格式返回，结构如下：
{{
  "articles": [
    {{
      "title": "...",
      "source": "TED",
      "link": "...",
      "guide": "...",
      "points": [
        {{"phrase": "...", "example": "..."}}
      ]
    }}
  ]
}}

只返回 JSON，不要其他内容。"""

    result = call_deepseek(api_key, prompt)
    return json.loads(result)


def main():
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("ERROR: DEEPSEEK_API_KEY not set")
        sys.exit(1)

    # 1. 抓 RSS
    all_items = []
    for src in SOURCES:
        try:
            items = fetch_source(src)
            all_items.extend(items)
            print(f"OK: {src['name']} fetched {len(items)} articles")
        except Exception as e:
            print(f"FAIL: {src['name']} - {e}")

    all_items.sort(key=lambda x: x["published"], reverse=True)
    print(f"Total articles fetched: {len(all_items)}")

    # 2. 调 DeepSeek 挑选并生成
    try:
        result = pick_and_generate(all_items, api_key)
        featured = result.get("articles", [])
        print(f"AI selected {len(featured)} articles")
    except Exception as e:
        print(f"AI generation failed: {e}")
        featured = []

    # 3. 先把旧的 featured.json 备份到 featured/ 目录，按日期命名，留存历史
    import shutil
    os.makedirs("featured", exist_ok=True)
    today_str = datetime.now().strftime("%Y-%m-%d")
    backup_path = f"featured/{today_str}.json"
    if os.path.exists("featured.json"):
        shutil.copy("featured.json", backup_path)
        print(f"Old featured.json backed up to {backup_path}")

    # 4. 输出新的
    output = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "featured": featured,
        "allCount": len(all_items),
    }

    with open("featured.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"Done: {len(featured)} featured articles written to featured.json")


if __name__ == "__main__":
    main()
