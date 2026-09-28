"""Newsletter Bot : flux RSS -> cartes d'articles -> public/index.html

Sans clé API, les cartes utilisent l'extrait fourni par le flux RSS.
Avec ANTHROPIC_API_KEY, Claude écrit un petit résumé neutre par article.
"""

import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import feedparser

MODEL = "claude-haiku-4-5-20251001"
BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config", "feeds.json")
TEMPLATE_PATH = os.path.join(BASE, "template.html")
OUTPUT_PATH = os.path.join(BASE, "public", "index.html")

MAX_AGE_HOURS = 48
MAX_PER_FEED = 10
LABELS = {
    "faits-divers": "Faits divers",
    "actualite-generale": "Actualité générale",
    "jeux-video": "Jeux vidéo",
}


def label(cat):
    return LABELS.get(cat, cat.replace("-", " ").capitalize())


def clean(text, n=220):
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "…"


def find_image(entry):
    for key in ("media_thumbnail", "media_content"):
        for m in entry.get(key, []) or []:
            if m.get("url") and m.get("medium") != "video" and not m.get("type", "").startswith("video"):
                return m["url"]
    for enc in entry.get("enclosures", []) or []:
        if enc.get("type", "").startswith("image") and enc.get("href"):
            return enc["href"]
    blob = entry.get("summary", "") or ""
    for c in entry.get("content", []) or []:
        blob += c.get("value", "")
    m = re.search(r'<img[^>]+src=["\']([^"\']+)', blob)
    return m.group(1) if m else ""


def fetch_category(cat, urls, seen):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
    articles = []
    for url in urls:
        try:
            parsed = feedparser.parse(url)
        except Exception as exc:
            print(f"  [!] erreur sur {url} : {exc}", file=sys.stderr)
            continue
        if not parsed.entries:
            print(f"  [!] flux vide ou illisible, ignoré : {url}", file=sys.stderr)
            continue
        source = parsed.feed.get("title", url)
        for e in parsed.entries[:MAX_PER_FEED]:
            title = clean(e.get("title", ""), 200)
            link = e.get("link", "")
            if not title or not link or title.lower() in seen:
                continue
            pub = e.get("published_parsed") or e.get("updated_parsed")
            date = ""
            if pub:
                dt = datetime(*pub[:6], tzinfo=timezone.utc)
                if dt < cutoff:
                    continue
                date = dt.isoformat()
            seen.add(title.lower())
            excerpt = clean(e.get("summary", ""))
            articles.append({
                "category": cat, "title": title, "link": link, "source": source,
                "date": date, "image": find_image(e), "excerpt": excerpt, "summary": excerpt,
            })
    return articles


def add_summaries(client, cat, articles):
    if not client or not articles:
        return
    listing = "\n".join(f"{i}. {a['title']} : {a['excerpt']}" for i, a in enumerate(articles))
    prompt = (
        f"Voici {len(articles)} articles de la catégorie \"{label(cat)}\" :\n\n{listing}\n\n"
        "Pour chaque article, écris UN résumé factuel et neutre en français (25 mots maximum), "
        "sans opinion et sans rien inventer. Réponds uniquement par un tableau JSON de "
        f"{len(articles)} chaînes, dans le même ordre, sans aucun autre texte."
    )
    try:
        r = client.messages.create(model=MODEL, max_tokens=2000, messages=[{"role": "user", "content": prompt}])
        text = re.sub(r"^```(?:json)?|```$", "", r.content[0].text.strip(), flags=re.M).strip()
        result = json.loads(text)
        if isinstance(result, list) and len(result) == len(articles):
            for a, s in zip(articles, result):
                if isinstance(s, str) and s.strip():
                    a["summary"] = s.strip()
    except Exception as exc:
        print(f"  [!] résumés IA impossibles, extraits RSS utilisés : {exc}", file=sys.stderr)


def make_client():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("[i] Pas de clé API : extraits RSS utilisés à la place des résumés.")
        return None
    from anthropic import Anthropic
    return Anthropic(api_key=key)


def main():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        feeds = json.load(f)
    client = make_client()
    seen, articles = set(), []
    for cat, urls in feeds.items():
        print(f"[*] Catégorie : {cat}")
        found = fetch_category(cat, urls, seen)
        print(f"    {len(found)} article(s)")
        add_summaries(client, cat, found)
        articles += found
    articles.sort(key=lambda a: a["date"], reverse=True)

    generated = datetime.now(timezone.utc).strftime("%d/%m/%Y à %H:%M UTC")
    data = {"categories": {c: label(c) for c in feeds}, "articles": articles}
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    blob = blob.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")

    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        page = f.read().replace("{{GENERATED_AT}}", generated).replace("{{ARTICLES_JSON}}", blob)
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"[+] Page générée : {OUTPUT_PATH} ({len(articles)} articles)")


if __name__ == "__main__":
    main()
