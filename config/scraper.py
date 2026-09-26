"""
Newsletter Bot — génère une page HTML quotidienne à partir de flux RSS,
résumée de façon objective par l'API Claude.

Usage :
    export ANTHROPIC_API_KEY="sk-ant-..."
    python scraper.py

Sortie : public/index.html
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import feedparser
from anthropic import Anthropic

# Modèle utilisé pour la synthèse. Haiku suffit largement pour résumer
# des titres/chapôs d'articles — rapide et peu coûteux pour un job quotidien.
MODEL = "claude-haiku-4-5-20251001"

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config", "feeds.json")
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "public", "index.html")
TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "template.html")

MAX_ARTICLE_AGE_HOURS = 30  # fenêtre glissante pour un digest "quotidien"
MAX_ARTICLES_PER_FEED = 10


def load_feeds():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def fetch_category_articles(feed_urls):
    """Récupère et dédoublonne les articles récents d'une liste de flux RSS."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_ARTICLE_AGE_HOURS)
    seen_titles = set()
    articles = []

    for url in feed_urls:
        try:
            parsed = feedparser.parse(url)
            if parsed.bozo and not parsed.entries:
                print(f"  [!] flux illisible, ignoré : {url}", file=sys.stderr)
                continue
        except Exception as exc:
            print(f"  [!] erreur sur {url} : {exc}", file=sys.stderr)
            continue

        source_name = parsed.feed.get("title", url)

        for entry in parsed.entries[:MAX_ARTICLES_PER_FEED]:
            title = entry.get("title", "").strip()
            if not title or title.lower() in seen_titles:
                continue

            # Filtre par date quand elle est disponible ; sinon on garde
            # l'article (certains flux n'exposent pas de date fiable).
            published = entry.get("published_parsed")
            if published:
                pub_dt = datetime(*published[:6], tzinfo=timezone.utc)
                if pub_dt < cutoff:
                    continue

            seen_titles.add(title.lower())
            articles.append(
                {
                    "title": title,
                    "summary": (entry.get("summary", "") or "")[:500],
                    "link": entry.get("link", ""),
                    "source": source_name,
                }
            )

    return articles


def summarize_category(client, category_name, articles):
    """Demande à Claude une synthèse factuelle et neutre d'une catégorie."""
    if not articles:
        return "Aucune actualité récente trouvée pour cette catégorie."

    articles_text = "\n\n".join(
        f"- [{a['source']}] {a['title']}\n  {a['summary']}"
        for a in articles
    )

    prompt = f"""Voici une liste brute d'articles récents dans la catégorie "{category_name}", \
issus de plusieurs médias :

{articles_text}

Rédige une synthèse courte et factuelle de l'actualité du jour dans cette catégorie, en français.
Consignes :
- Reste strictement factuel et neutre, sans ton éditorial ni opinion.
- Regroupe les sujets qui se recoupent entre plusieurs sources.
- Format : une liste à puces, une puce par sujet distinct, 1-2 phrases par puce.
- N'invente aucune information absente des articles fournis.
- Ne mets aucun préambule, seulement la liste."""

    response = client.messages.create(
        model=MODEL,
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def render_html(sections, generated_at):
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        template = f.read()

    sections_html = ""
    for category, content_md in sections.items():
        bullets = "\n".join(
            f"<li>{line.lstrip('-').strip()}</li>"
            for line in content_md.splitlines()
            if line.strip().startswith("-")
        ) or f"<li>{content_md}</li>"

        sections_html += f"""
        <section class="category">
          <h2>{category.replace('-', ' ').capitalize()}</h2>
          <ul>{bullets}</ul>
        </section>
        """

    html = template.replace("{{SECTIONS}}", sections_html)
    html = html.replace("{{GENERATED_AT}}", generated_at)
    return html


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("Erreur : la variable d'environnement ANTHROPIC_API_KEY n'est pas définie.")

    client = Anthropic(api_key=api_key)
    feeds_by_category = load_feeds()

    sections = {}
    for category, urls in feeds_by_category.items():
        print(f"[*] Catégorie : {category}")
        articles = fetch_category_articles(urls)
        print(f"    {len(articles)} article(s) trouvé(s)")
        sections[category] = summarize_category(client, category, articles)

    generated_at = datetime.now(timezone.utc).strftime("%d/%m/%Y à %H:%M UTC")
    html = render_html(sections, generated_at)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"[+] Page générée : {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
