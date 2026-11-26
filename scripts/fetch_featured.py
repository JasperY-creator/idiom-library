#!/usr/bin/env python3
"""
抓取 TED Talks RSS，调 DeepSeek 自动挑选 3 篇适合口语学习的演讲，
生成中文导读和重点表达，输出 featured.json 供 H5 渲染。
GitHub Actions 每两天跑一次。
"""
import feedparser
import time
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

MAX_PER_SOURCE = 20     # 候选池大小，AI 再从里面挑 3 条
MAX_SCAN = 80           # RSS 前 80 条里筛，很多是播客或短链失效
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

# TED RSS 给的是 go.ted.com 短链（会经 http 中转，微信/国内常打不开），
# 这里统一解析成 https://www.ted.com/talks/<slug>，并额外记下官方播放器地址。
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"


def http_ok(url, timeout=15):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA}, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 400
    except Exception:
        return False


def talk_slug(link):
    m = re.search(r"ted\.com/talks/([^/?#\"]+)", link or "")
    return m.group(1) if m else ""


def resolve_talk(link):
    """短链 -> (正式页链接, 播放器链接)；解析不到或没有视频就返回 ("'', "'')"""
    slug = talk_slug(link)
    if not slug and "go.ted.com" in (link or ""):
        try:
            req = urllib.request.Request(link, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=25) as r:
                slug = talk_slug(r.geturl())
        except Exception as e:
            print(f"  短链解析失败 {link}: {e}")
            return "", ""
    if not slug:
        return "", ""
    embed = f"https://embed.ted.com/talks/{slug}"
    if not http_ok(embed):
        print(f"  跳过，没有可播放视频: {slug}")
        return "", ""
    return f"https://www.ted.com/talks/{slug}", embed


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


def fetch_feed(url):
    """自己拉 RSS，带 UA；feedparser 直连有时拿不到内容"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=40) as r:
            return feedparser.parse(r.read())
    except Exception as e:
        print(f"  直连失败 {url}: {e}，改用 feedparser 默认方式")
        return feedparser.parse(url)


def fetch_source(src):
    feed = fetch_feed(src["url"])
    print(f"  RSS entries: {len(feed.entries)}")
    items = []
    for entry in feed.entries[:MAX_SCAN]:
        if len(items) >= MAX_PER_SOURCE:
            break
        link = entry.get("link", "")
        # 过滤掉播客/精选集（链接都是同一个首页，不是具体演讲）
        if "audiocollective.ted.com" in link:
            continue
        if not link.startswith("http"):
            continue
        page, embed = resolve_talk(link)
        if not page:
            time.sleep(0.4)   # 别把 TED 请求打太快，容易 403
            page, embed = resolve_talk(link)
        if not page:
            continue
        items.append({
            "source": src["name"],
            "title": entry.get("title", "").strip(),
            "link": page,
            "embed": embed,
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
- embed: 视频播放链接（固定格式 https://embed.ted.com/talks/<slug>，slug 取自上面链接的最后一段）
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
      "embed": "...",
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


def load_seen_slugs():
    """读出历史各期里出现过的演讲 slug"""
    import glob
    seen = set()
    for path in glob.glob("featured/*.json"):
        if path.endswith("index.json"):
            continue
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        for a in data.get("featured", []):
            slug = talk_slug(a.get("link", ""))
            if slug:
                seen.add(slug)
    return seen


def write_index():
    """把 featured/ 里所有历史文件汇总成 index.json，供前端「往期」列表使用"""
    import glob
    os.makedirs("featured", exist_ok=True)
    entries = []
    seen = set()
    for path in sorted(glob.glob("featured/*.json"), reverse=True):
        if path.endswith("index.json"):
            continue
        date = os.path.basename(path).replace(".json", "")
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        items = data.get("featured", [])
        if not items:
            continue
        slugs = [talk_slug(a.get("link", "")) for a in items]
        titles = [a.get("title", "") for a in items]
        # 每条演讲只在往期里出现一次：更近期已经出现过的，这里就不再重复
        keep = [(t, s) for t, s in zip(titles, slugs) if s and s not in seen]
        if not keep:
            continue
        seen.update(s for _, s in keep)
        entries.append({
            "date": date,
            "count": len(keep),
            "titles": [t for t, _ in keep],
            "slugs": [s for _, s in keep],
        })
    with open("featured/index.json", "w", encoding="utf-8") as f:
        json.dump({"updatedAt": datetime.now(timezone.utc).isoformat(), "issues": entries}, f, ensure_ascii=False, indent=2)
    print(f"index.json: {len(entries)} 期")


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

    # 跳过以前推过的演讲，保证每期都是新内容
    seen = load_seen_slugs()
    fresh = [a for a in all_items if talk_slug(a["link"]) not in seen]
    print(f"历史上已推过 {len(seen)} 个，去掉重复后剩 {len(fresh)} 个")
    if len(fresh) >= 6:
        all_items = fresh

    # 2. 调 DeepSeek 挑选并生成
    try:
        result = pick_and_generate(all_items, api_key)
        featured = result.get("articles", [])
        print(f"AI selected {len(featured)} articles")

        # 逐个校验链接可用性，坏的直接丢掉
        bad = []
        for a in featured:
            page = a.get("link", "")
            if "go.ted.com" in page or not talk_slug(page):
                page, embed = resolve_talk(page)
                a["link"], a["embed"] = page, embed
            elif not a.get("embed"):
                a["embed"] = "https://embed.ted.com/talks/" + talk_slug(page)
            if not a.get("link") or not a.get("embed"):
                bad.append(a)
        if bad:
            print(f"WARN: {len(bad)} 条链接无效已移除: {[a.get('title', '')[:30] for a in bad]}")
            featured = [a for a in featured if a.get("link") and a.get("embed")]
    except Exception as e:
        print(f"AI generation failed: {e}")
        featured = []

    if len(featured) < 2:
        print(f"WARN: 只拿到 {len(featured)} 条有效演讲，保留上一版 featured.json 不动")
        write_index()
        return

    # 3. 先把旧的 featured.json 备份到 featured/ 目录，按日期命名，留存历史
    import shutil
    os.makedirs("featured", exist_ok=True)
    today_str = datetime.now().strftime("%Y-%m-%d")
    old_date = today_str
    if os.path.exists("featured.json"):
        try:
            old_updated = json.load(open("featured.json", encoding="utf-8")).get("updatedAt", "")
            if old_updated:
                old_date = old_updated[:10]
        except Exception:
            pass
        backup_path = f"featured/{old_date}.json"
        # 归档按「这一期是哪天发布的」命名，同一天不重复归档
        if not os.path.exists(backup_path):
            shutil.copy("featured.json", backup_path)
            print(f"旧的一期已归档: {backup_path}")

    # 4. 输出新的
    output = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "featured": featured,
        "allCount": len(all_items),
    }

    with open("featured.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"Done: {len(featured)} featured articles written to featured.json")
    write_index()


if __name__ == "__main__":
    main()
