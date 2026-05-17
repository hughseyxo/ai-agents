"""News Briefing Agent.

Python handles: RSS fetching, parsing, dedup, and selection.
LLM CLI (with MCP access) handles: Formatting the final report and sending the email.
"""

import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

import requests

from .base import BaseAgent, REPO_ROOT


class NewsBriefingAgent(BaseAgent):
    name = "news-briefing"
    schedule = "0 5 * * *"
    model = "claude-haiku-4-5"

    FEEDS = {
        "International": [
            ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
        ],
        "Ireland": [
            ("RTE", "https://www.rte.ie/feeds/rss/?index=/news"),
            ("TheJournal", "https://www.thejournal.ie/feed/"),
            ("Irish Times", "https://www.irishtimes.com/cmlink/the-irish-times-news-1.1319192"),
        ],
        "Netherlands": [
            ("DutchNews", "https://www.dutchnews.nl/feed/"),
            ("NL Times", "https://news.google.com/rss/search?q=Netherlands&hl=en"),
        ],
        "Leiden": [
            ("Google News Leiden", "https://news.google.com/rss/search?q=Leiden+Netherlands&hl=en"),
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

    def plan(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return {
            "today": today,
        }

    def steps(self):
        return [
            {"name": "fetch_news", "fn": self._fetch_news},
            {"name": "news_briefing", "fn": self._run_briefing, "side_effects": True},
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
                try:
                    resp = requests.get(url, headers=headers, timeout=15)
                    if resp.status_code == 200:
                        articles = self._parse_rss(resp.text, source_name, category)
                        all_articles.extend(articles)
                    else:
                        print(f"[{self.name}] Failed to fetch {source_name}: {resp.status_code}", file=sys.stderr)
                except Exception as e:
                    print(f"[{self.name}] Error fetching {source_name}: {e}", file=sys.stderr)

        # Process and select news
        selected = self._select_news(all_articles)
        self.context["news_data"] = selected
        return {"count": sum(len(items) for items in selected.values())}

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
                
                # Clean HTML from description
                description = re.sub('<[^<]+?>', '', description)
                description = description.replace('\n', ' ').strip()
                if len(description) > 250:
                    description = description[:247] + "..."
                
                # Parse date
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
        except Exception:
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
            seen_titles = set()
            unique = []
            for a in items:
                normalized_title = re.sub(r'\W+', '', a['title'].lower())
                if normalized_title not in seen_titles:
                    seen_titles.add(normalized_title)
                    unique.append(a)
            return unique

        # 1. International
        international = [a for a in articles if a['category'] == "International"]
        international = sorted(international, key=lambda x: x['pub_datetime'] or now, reverse=True)[:5]

        # 2. Ireland
        ireland = [a for a in articles if a['category'] == "Ireland"]
        ireland = dedup(sorted(ireland, key=lambda x: x['pub_datetime'] or now, reverse=True))[:7]

        # 3. Netherlands
        netherlands = [a for a in articles if a['category'] == "Netherlands"]
        netherlands = dedup(sorted(netherlands, key=lambda x: x['pub_datetime'] or now, reverse=True))[:8]

        # 4. Leiden
        leiden = [a for a in articles if a['category'] == "Leiden"][:15] # Cap Leiden to 15 to keep prompt size sane

        # 5. Tech & Gaming
        tech_all = [a for a in articles if a['category'] in ["Tech", "Gaming"]]
        
        def is_gaming(a):
            if a['category'] == "Gaming": return True
            gaming_keywords = ['gaming', 'game', 'nintendo', 'playstation', 'xbox', 'steam', 'valve', 'rpg', 'fps', 'mmo']
            text = (a['title'] + " " + a['description']).lower()
            return any(k in text for k in gaming_keywords)

        tech_items = [a for a in tech_all if not is_gaming(a)]
        gaming_items = [a for a in tech_all if is_gaming(a)]

        # Tech selection (prefer Verge)
        verge_tech = [a for a in tech_items if a['source'] == 'The Verge']
        other_tech = [a for a in tech_items if a['source'] != 'The Verge']
        verge_tech = sorted(verge_tech, key=lambda x: x['pub_datetime'] or now, reverse=True)
        other_tech = sorted(other_tech, key=lambda x: x['pub_datetime'] or now, reverse=True)
        selected_tech = verge_tech[:2]
        remaining_tech = sorted(verge_tech[2:] + other_tech, key=lambda x: x['pub_datetime'] or now, reverse=True)
        selected_tech += remaining_tech[:(6 - len(selected_tech))]

        # Gaming selection
        selected_gaming = dedup(sorted(gaming_items, key=lambda x: x['pub_datetime'] or now, reverse=True))[:4]

        # 6. SRE
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
            "Tech": selected_tech,
            "Gaming": selected_gaming,
            "SRE / Infrastructure": selected_sre
        }

        # Add flags and remove datetime
        for section in report_data.values():
            for a in section:
                a['breaking'] = is_breaking(a)
                if 'pub_datetime' in a: del a['pub_datetime']

        return report_data

    def _run_briefing(self):
        """Invoke Claude CLI to format and send email with pre-parsed data."""
        today = self.context["plan"]["today"]

        if self.is_duplicate("email_sent", today):
            print(f"[{self.name}] Email already sent today, skipping", file=sys.stderr)
            return {"skipped": True, "reason": "already_sent"}

        news_data = self.context.get("news_data", {})
        if not any(news_data.values()):
            print(f"[{self.name}] No news items found, skipping", file=sys.stderr)
            return {"skipped": True, "reason": "no_news"}

        prompt_path = REPO_ROOT / "agents" / "prompts" / "news_briefing.md"
        base_prompt = prompt_path.read_text()
        
        # Remove Steps 2 & 3 (fetching/parsing) from prompt since we did it in Python
        prompt = re.sub(r'## Step 2: Fetch RSS feeds.*?## Step 4: Send email', '## Step 2: Format and Send email', base_prompt, flags=re.DOTALL)
        # Remove RSS Feeds section
        prompt = re.sub(r'# RSS Feeds.*?# Section Rules', '# Section Rules', prompt, flags=re.DOTALL)
        
        prompt = prompt.replace("{{TODAY}}", today)
        prompt += f"\n\n## PRE-PARSED NEWS DATA (JSON)\n{json.dumps(news_data, indent=2)}\n"
        prompt += "\n**INSTRUCTIONS:** Use the provided JSON data to build the report and send the email. Do NOT fetch any feeds yourself. Follow all section rules and formatting instructions."

        output = self.synthesize(prompt)

        output_path = REPO_ROOT / "output" / f"daily-news-briefing-{today}.md"
        output_path.write_text(output)

        self.mark_seen("email_sent", today)
        return {"sent": True, "output_path": str(output_path)}
