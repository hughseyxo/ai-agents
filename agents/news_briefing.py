"""News Briefing Agent.

Python handles: RSS fetching, parsing, dedup, selection, HTML/markdown building,
and sending the built email directly via gmail_client.send_email().
LLM CLI handles only genuine synthesis: Dutch translation and relevance scoring.
"""

import html as html_lib
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

import requests

from .base import BaseAgent, REPO_ROOT
from .gmail_client import send_email


class NewsBriefingAgent(BaseAgent):
    name = "news-briefing"
    schedule = "0 4 * * *"
    model = "claude-haiku-4-5"
    # Claude primary, Antigravity fallback (default PROVIDERS is Antigravity-first)
    providers = list(reversed(BaseAgent.PROVIDERS))
    # Prompts embed untrusted RSS content; belt-and-braces with the Claude-first
    # providers override above (untrusted_input alone would hard-exclude agy).
    untrusted_input = True

    FEEDS = {
        "International": [
            ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
            ("The Guardian", "https://www.theguardian.com/world/rss"),
            ("AP News", "https://feeds.apnews.com/rss/apf-topnews"),
        ],
        "Ireland": [
            ("RTE", "https://www.rte.ie/feeds/rss/?index=/news"),
            ("TheJournal", "https://www.thejournal.ie/feed/"),
            ("Irish Times", "https://news.google.com/rss/search?q=site:irishtimes.com&hl=en"),
        ],
        "Netherlands": [
            ("DutchNews", "https://www.dutchnews.nl/feed/"),
            ("NL Times", "https://nltimes.nl/feed"),
            ("NOS (Dutch)", "https://feeds.nos.nl/nosnieuwsbinnenland"),
        ],
        "Leiden": [
            ("Google News Leiden", "https://news.google.com/rss/search?q=Leiden+Netherlands&hl=en"),
        ],
        "Mullingar": [
            ("Google News Mullingar", "https://news.google.com/rss/search?q=Mullingar+Westmeath&hl=en"),
        ],
        "Tech": [
            ("The Verge", "https://news.google.com/rss/search?q=site:theverge.com&hl=en"),
            ("TechCrunch", "https://techcrunch.com/feed/"),
            ("Hacker News", "https://hnrss.org/frontpage?count=10&points=100"),
            ("Ars Technica", "https://news.google.com/rss/search?q=site:arstechnica.com&hl=en"),
            ("The Register", "https://news.google.com/rss/search?q=site:theregister.com&hl=en"),
        ],
        "Gaming": [
            ("Polygon", "https://news.google.com/rss/search?q=site:polygon.com+gaming&hl=en"),
        ],
        "SRE": [
            ("HN SRE", "https://hnrss.org/frontpage?q=kubernetes+OR+linux+OR+incident+OR+postmortem+OR+observability+OR+distributed+systems+OR+storage+OR+reliability+OR+infrastructure&points=200"),
        ]
    }

    # Section display order and CSS styles (bg_color, border_color) — used in <style> block
    SECTION_STYLES = {
        "International":        ("#f5f0ff", "#8e44ad"),
        "Ireland":              ("#f0fff4", "#2ecc71"),
        "Netherlands":          ("#fff8f0", "#e67e22"),
        "Leiden & Local":       ("#f0f4ff", "#4a6fa5"),
        "Mullingar":            ("#f0fff8", "#16a085"),
        "Tech":                 ("#f0f7ff", "#3498db"),
        "Gaming":               ("#f5f0ff", "#9b59b6"),
        "SRE / Infrastructure": ("#fff5f5", "#e74c3c"),
    }

    SECTION_CSS_CLASS = {
        "International":        "c-intl",
        "Ireland":              "c-ie",
        "Netherlands":          "c-nl",
        "Leiden & Local":       "c-leiden",
        "Mullingar":            "c-mullingar",
        "Tech":                 "c-tech",
        "Gaming":               "c-gaming",
        "SRE / Infrastructure": "c-sre",
    }

    # Sources that publish in Dutch and need LLM translation
    DUTCH_LANGUAGE_SOURCES = {"NOS (Dutch)"}

    # Sections with source attribution in output
    SOURCE_SECTIONS = {"Ireland", "Netherlands", "Tech", "Gaming", "SRE / Infrastructure"}

    def plan(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return {
            "today": today,
        }

    def steps(self):
        return [
            {"name": "fetch_news",      "fn": self._fetch_news},
            {"name": "score_relevance", "fn": self._score_relevance},
            {"name": "translate_dutch", "fn": self._translate_dutch_step},
            {"name": "news_briefing",   "fn": self._run_briefing, "side_effects": True},
        ]

    def report(self) -> str:
        today = self.context["plan"]["today"]
        result = self.context.get("news_briefing", {})
        if result and result.get("skipped"):
            return f"News briefing for {today} skipped — {result['reason']}"
        return f"News briefing for {today} complete"

    def _fetch_news(self):
        """Fetch and parse all RSS feeds in Python."""
        all_articles = []
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        for category, feeds in self.FEEDS.items():
            for source_name, url in feeds:
                for attempt in range(2):
                    try:
                        resp = requests.get(url, headers=headers, timeout=15)
                        if resp.status_code == 200:
                            articles = self._parse_rss(resp.text, source_name, category)
                            all_articles.extend(articles)
                            break
                        elif resp.status_code >= 500 and attempt == 0:
                            time.sleep(3)
                            continue
                        else:
                            print(f"[{self.name}] Failed to fetch {source_name}: {resp.status_code}", file=sys.stderr)
                            break
                    except Exception as e:
                        print(f"[{self.name}] Error fetching {source_name}: {e}", file=sys.stderr)
                        break

        selected = self._select_news(all_articles)
        self.context["news_candidates"] = selected
        return {"count": sum(len(items) for items in selected.values())}

    def _translate_dutch_step(self):
        """Translate Dutch-language article titles and descriptions to English."""
        news_data = self.context.get("news_data", {})
        nl_articles = news_data.get("Netherlands", [])

        dutch = [(i, a) for i, a in enumerate(nl_articles) if a.get("source") in self.DUTCH_LANGUAGE_SOURCES]
        if not dutch:
            return {"translated": 0}

        batch = [{"i": i, "title": a["title"], "description": a.get("description", "")} for i, a in dutch]
        prompt = (
            "Translate these Dutch news headlines and summaries to English. "
            "Return ONLY a JSON array: [{\"i\": 0, \"title\": \"...\", \"description\": \"...\"}]. "
            "Keep titles concise.\n\n" + json.dumps(batch)
        )
        for attempt in range(2):
            try:
                result = self.synthesize(prompt)
                text = result.strip()
                if text.startswith("```"):
                    text = re.sub(r'^```[a-z]*\n?', '', text)
                    text = re.sub(r'\n?```\s*$', '', text).strip()
                match = re.search(r'\[[\s\S]*\]', text)
                if match:
                    text = match.group(0)
                translations = json.loads(text)
                articles_copy = list(nl_articles)
                for t in translations:
                    idx = t["i"]
                    articles_copy[idx] = {**articles_copy[idx], "title": t["title"], "description": t["description"]}
                news_data["Netherlands"] = articles_copy
                break
            except Exception as e:
                if attempt == 0:
                    print(f"[{self.name}] Dutch translation attempt 1 failed: {e}, retrying", file=sys.stderr)
                else:
                    print(f"[{self.name}] Dutch translation failed after 2 attempts, keeping originals", file=sys.stderr)

        return {"translated": len(dutch)}

    def _score_relevance(self):
        """Rank candidate articles by newsworthiness using LLM, then finalize news_data."""
        # Final article counts per section after scoring
        final_counts = {
            "International": 5, "Ireland": 7, "Netherlands": 8,
            "Leiden & Local": 5, "Mullingar": 5, "Tech": 6,
            "Gaming": 4, "SRE / Infrastructure": 999,
        }

        candidates = self.context.get("news_candidates", {})
        if not candidates:
            self.context["news_data"] = {}
            return {"scored": 0}

        payload = {
            section: [{"i": i, "title": a["title"]} for i, a in enumerate(arts)]
            for section, arts in candidates.items()
            if arts
        }

        prompt = (
            "Rank these news articles by newsworthiness — lead with the most impactful story for each region. "
            "Consider significance, scale of impact, and relevance to people in that region. "
            "Return ONLY a JSON object mapping each section name to an array of article indices "
            "ordered from most to least important. Include all provided indices.\n\n"
            + json.dumps(payload)
        )

        rankings = {}
        try:
            result = self.synthesize(prompt)
            text = result.strip()
            if text.startswith("```"):
                text = re.sub(r'^```[a-z]*\n?', '', text)
                text = re.sub(r'\n?```\s*$', '', text).strip()
            match = re.search(r'\{[\s\S]*\}', text)
            if match:
                text = match.group(0)
            rankings = json.loads(text)
        except Exception as e:
            print(f"[{self.name}] Relevance scoring failed, using recency order: {e}", file=sys.stderr)

        news_data = {}
        for section, articles in candidates.items():
            n = final_counts.get(section, 5)
            if section in rankings and isinstance(rankings[section], list):
                valid = [i for i in rankings[section] if isinstance(i, int) and 0 <= i < len(articles)]
                # Append any indices the LLM omitted so no article is lost from fallback
                seen = set(valid)
                for i in range(len(articles)):
                    if i not in seen:
                        valid.append(i)
                ranked = [articles[i] for i in valid[:n]]
            else:
                ranked = articles[:n]

            for a in ranked:
                a.pop('pub_datetime', None)
            news_data[section] = ranked

        self.context["news_data"] = news_data
        return {"scored": len(rankings)}

    def _parse_rss(self, xml_content: str, source_name: str, category: str) -> List[Dict]:
        try:
            root = ET.fromstring(xml_content)
            channel = root.find('channel')
            if channel is None:
                return []

            items = []
            for item in channel.findall('item'):
                title = item.find('title').text if item.find('title') is not None else ""
                link = item.find('link').text if item.find('link') is not None else ""
                description = item.find('description').text if item.find('description') is not None else ""
                pub_date_str = item.find('pubDate').text if item.find('pubDate') is not None else ""

                description = description.replace('&nbsp;', ' ')
                description = re.sub('<[^<]+?>', '', description)
                description = re.sub(r'\s+', ' ', description).strip()
                # Drop Google News echo descriptions: just title text + source attribution
                title_text = re.sub(r'\s*-\s*[^-]+$', '', title).strip()
                if len(title_text) >= 15 and description.startswith(title_text[:20]):
                    description = ""
                elif len(description) > 250:
                    description = description[:247] + "..."

                pub_date = self._parse_date(pub_date_str)

                items.append({
                    "title": title,
                    "link": link,
                    "description": description,
                    "pubDate": pub_date_str,
                    "pub_datetime": pub_date,
                    "source": source_name,
                    "category": category
                })
            return items
        except Exception as e:
            print(f"[news-briefing] Failed to parse RSS from {source_name}: {e}", file=sys.stderr)
            return []

    def _parse_date(self, date_str: str) -> datetime:
        if not date_str:
            return None
        formats = [
            "%a, %d %b %Y %H:%M:%S %z",
            "%a, %d %b %Y %H:%M:%S %Z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%a, %d %b %Y %H:%M:%S GMT"
        ]
        for fmt in formats:
            try:
                dt = datetime.strptime(date_str.replace('GMT', '+0000'), fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except:
                continue
        return None

    def _select_news(self, articles: List[Dict]) -> Dict[str, List[Dict]]:
        now = datetime.now(timezone.utc)

        def is_breaking(a):
            dt = a.get('pub_datetime')
            if not dt: return False
            return (now - dt) < timedelta(hours=3)

        def dedup(items):
            seen = set()
            unique = []
            for a in items:
                # Strip trailing " - Publisher" (Google News RSS format) before comparing
                clean = re.sub(r'\s*-\s*[^-]+$', '', a['title'])
                key = re.sub(r'\W+', '', clean.lower())
                if key not in seen:
                    seen.add(key)
                    unique.append(a)
            return unique

        international = [a for a in articles if a['category'] == "International"]
        international = sorted(international, key=lambda x: x['pub_datetime'] or now, reverse=True)[:10]

        ireland = [a for a in articles if a['category'] == "Ireland"]
        ireland = dedup(sorted(ireland, key=lambda x: x['pub_datetime'] or now, reverse=True))[:14]

        netherlands = [a for a in articles if a['category'] == "Netherlands"]
        netherlands = dedup(sorted(netherlands, key=lambda x: x['pub_datetime'] or now, reverse=True))[:16]

        leiden = [a for a in articles if a['category'] == "Leiden"][:10]

        mullingar = [a for a in articles if a['category'] == "Mullingar"][:10]

        tech_all = [a for a in articles if a['category'] in ["Tech", "Gaming"]]

        def is_gaming(a):
            if a['category'] == "Gaming": return True
            gaming_keywords = ['gaming', 'game', 'nintendo', 'playstation', 'xbox', 'steam', 'valve', 'rpg', 'fps', 'mmo']
            text = (a['title'] + " " + a['description']).lower()
            return any(k in text for k in gaming_keywords)

        tech_items = [a for a in tech_all if not is_gaming(a)]
        gaming_items = [a for a in tech_all if is_gaming(a)]

        verge_tech = [a for a in tech_items if a['source'] == 'The Verge']
        other_tech = [a for a in tech_items if a['source'] != 'The Verge']
        verge_tech = sorted(verge_tech, key=lambda x: x['pub_datetime'] or now, reverse=True)
        other_tech = sorted(other_tech, key=lambda x: x['pub_datetime'] or now, reverse=True)
        # Cap Verge at 2 candidates to prevent source dominance in the final output
        selected_tech = verge_tech[:2]
        remaining_tech = sorted(verge_tech[2:] + other_tech, key=lambda x: x['pub_datetime'] or now, reverse=True)
        selected_tech += remaining_tech[:(10 - len(selected_tech))]

        selected_gaming = dedup(sorted(gaming_items, key=lambda x: x['pub_datetime'] or now, reverse=True))[:8]

        sre_candidates = [a for a in articles if a['category'] == "SRE"]
        selected_sre = []
        for a in sre_candidates:
            points_match = re.search(r'Points: (\d+)', a['title'])
            points = int(points_match.group(1)) if points_match else 0
            if points > 200 or any(k in a['title'].lower() for k in ['incident', 'postmortem', 'outage', 'reliability', 'sre']):
                selected_sre.append(a)

        report_data = {
            "International": international,
            "Ireland": ireland,
            "Netherlands": netherlands,
            "Leiden & Local": leiden,
            "Mullingar": mullingar,
            "Tech": selected_tech,
            "Gaming": selected_gaming,
            "SRE / Infrastructure": selected_sre
        }

        for section in report_data.values():
            for a in section:
                a['breaking'] = is_breaking(a)

        return report_data

    def _build_markdown(self, news_data: Dict[str, List[Dict]], today: str) -> str:
        """Build the markdown report from pre-selected news data."""
        lines = [f"# News Briefing — {today}", ""]
        optional_skip = {"Mullingar", "SRE / Infrastructure"}

        for section, articles in news_data.items():
            if not articles and section in optional_skip:
                continue
            lines.append(f"## {section}")
            if not articles:
                lines.append("_No items._")
            else:
                for a in articles:
                    breaking = "[BREAKING] " if a.get("breaking") else ""
                    title = a.get("title", "")
                    link = a.get("link", "")
                    desc = a.get("description", "")
                    source = a.get("source", "")
                    source_tag = f" ({source})" if section in self.SOURCE_SECTIONS and source else ""
                    summary = f" — {desc}" if desc else ""
                    lines.append(f"- **{breaking}{title}**{source_tag}{summary}")
                    lines.append(f"  {link}")
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"

    def _build_html_email(self, news_data: Dict[str, List[Dict]], today: str) -> str:
        """Build the full HTML email from pre-selected news data."""
        optional_skip = {"Mullingar", "SRE / Infrastructure"}

        # Build section-specific card colours from SECTION_STYLES
        color_rules = ""
        for section, (bg, border) in self.SECTION_STYLES.items():
            cls = self.SECTION_CSS_CLASS.get(section, "c-default")
            color_rules += f".{cls}{{background:{bg};border-left-color:{border}}}.{cls} .src{{color:{border}}}"

        css = (
            "body{margin:0;padding:0;background:#f4f4f4;font-family:Arial,sans-serif}"
            "table.wrap{background:#f4f4f4;padding:24px 0}"
            "table.inner{background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08)}"
            ".hdr{background:#1a1a2e;padding:28px 32px}"
            ".hdr-lbl{margin:0;color:#a0a8c0;font-size:13px;letter-spacing:1px;text-transform:uppercase}"
            ".hdr-date{margin:4px 0 0;color:#fff;font-size:24px}"
            ".sec{padding:20px 32px 0}"
            ".sec-h{margin:0 0 12px;font-size:13px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#1a1a2e}"
            ".card{margin:0 0 10px;padding:10px 14px;border-left:3px solid;border-radius:4px;font-size:14px;color:#333}"
            ".card a{color:#1a1a2e;text-decoration:none;font-weight:700}"
            ".src{font-size:11px;font-weight:700}"
            ".desc{color:#666;font-size:13px}"
            ".ftr{padding:24px 32px;border-top:1px solid #eee}"
            ".ftr p{margin:0;color:#aaa;font-size:12px;text-align:center}"
            ".c-default{background:#f9f9f9;border-left-color:#999}"
            + color_rules
        )

        def article_html(a: Dict, css_class: str, show_source: bool) -> str:
            breaking = "[BREAKING] " if a.get("breaking") else ""
            title = html_lib.escape(a.get("title", ""))
            link = html_lib.escape(a.get("link", ""))
            desc = html_lib.escape(a.get("description", ""))
            source = html_lib.escape(a.get("source", ""))
            source_tag = f'<span class="src"> {source}</span>' if show_source and source else ""
            desc_tag = f'<br/><span class="desc">{desc}</span>' if desc else ""
            return f'<p class="card {css_class}"><a href="{link}">{breaking}{title}</a>{source_tag}{desc_tag}</p>'

        sections_html = ""
        for section, articles in news_data.items():
            if not articles and section in optional_skip:
                continue
            css_class = self.SECTION_CSS_CLASS.get(section, "c-default")
            show_source = section in self.SOURCE_SECTIONS
            articles_html = "".join(article_html(a, css_class, show_source) for a in articles)
            sections_html += (
                f'<tr><td class="sec">'
                f'<h2 class="sec-h">{html_lib.escape(section)}</h2>'
                f'{articles_html}</td></tr>'
            )

        return (
            f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>{css}</style></head>'
            f'<body><table class="wrap" width="100%" cellpadding="0" cellspacing="0">'
            f'<tr><td align="center"><table class="inner" width="600" cellpadding="0" cellspacing="0">'
            f'<tr><td class="hdr"><p class="hdr-lbl">News Briefing</p>'
            f'<h1 class="hdr-date">{html_lib.escape(today)}</h1></td></tr>'
            f'{sections_html}'
            f'<tr><td class="ftr"><p>Generated by your News Briefing Agent</p></td></tr>'
            f'</table></td></tr></table></body></html>'
        )

    def _run_briefing(self):
        """Build email/report in Python, then invoke LLM only to send the email."""
        today = self.context["plan"]["today"]

        if self.is_duplicate("email_sent", today):
            print(f"[{self.name}] Email already sent today, skipping", file=sys.stderr)
            return {"skipped": True, "reason": "already_sent"}

        news_data = self.context.get("news_data", {})
        if not any(news_data.values()):
            print(f"[{self.name}] No news items found, skipping", file=sys.stderr)
            return {"skipped": True, "reason": "no_news"}

        markdown = self._build_markdown(news_data, today)
        html_email = self._build_html_email(news_data, today)

        output_path = REPO_ROOT / "output" / f"daily-news-briefing-{today}.md"
        output_path.write_text(markdown)

        # Let send_email() failures propagate: BaseAgent._execute_step records the
        # step error and marks the run partial_failure. Dedup key stays unmarked on
        # failure (we never reach mark_seen), so it retries next run.
        send_email(os.environ.get("AGENT_EMAIL_TO", "cianohughes@gmail.com"),
                   f"📰 News Briefing — {today}", html_email)

        self.mark_seen("email_sent", today)
        return {"sent": True, "output_path": str(output_path),
                "articles": sum(len(v) for v in news_data.values())}
