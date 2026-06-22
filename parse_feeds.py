import xml.etree.ElementTree as ET
import os
import sys
import json
import re
from datetime import datetime, timezone, timedelta

def parse_rss(file_path, source_name):
    if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
        return []
    
    try:
        tree = ET.parse(file_path)
        root = tree.getroot()
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
            pub_date = None
            if pub_date_str:
                formats = [
                    "%a, %d %b %Y %H:%M:%S %z",
                    "%a, %d %b %Y %H:%M:%S %Z",
                    "%Y-%m-%dT%H:%M:%S%z",
                    "%Y-%m-%dT%H:%M:%S.%f%z",
                    "%a, %d %b %Y %H:%M:%S GMT"
                ]
                for fmt in formats:
                    try:
                        pub_date = datetime.strptime(pub_date_str.replace('GMT', '+0000'), fmt)
                        if pub_date.tzinfo is None:
                            pub_date = pub_date.replace(tzinfo=timezone.utc)
                        break
                    except (ValueError, TypeError):
                        continue

            items.append({
                "title": title,
                "link": link,
                "description": description,
                "pubDate": pub_date_str,
                "pub_datetime": pub_date,
                "source": source_name
            })
        return items
    except (ET.ParseError, OSError) as e:
        print(f"[parse_feeds] failed to parse {source_name} ({file_path}): {e}", file=sys.stderr)
        return []

feeds = {
    "rte.xml": "RTE",
    "thejournal.xml": "TheJournal",
    "irishtimes.xml": "Irish Times",
    "bbc.xml": "BBC World",
    "dutchnews.xml": "DutchNews",
    "nltimes.xml": "NL Times",
    "leiden.xml": "Google News Leiden",
    "theverge.xml": "The Verge",
    "techcrunch.xml": "TechCrunch",
    "hn.xml": "Hacker News",
    "polygon.xml": "Polygon",
    "ars.xml": "Ars Technica",
    "register.xml": "The Register",
    "hn_sre.xml": "HN SRE"
}

all_data = {}
for file, name in feeds.items():
    all_data[name] = parse_rss(os.path.join("/tmp/news-briefing", file), name)

now = datetime.now(timezone.utc)

def is_breaking(dt):
    if not dt: return False
    return (now - dt) < timedelta(hours=3)

def dedup(articles):
    seen_titles = set()
    unique = []
    for a in articles:
        normalized_title = re.sub(r'\W+', '', a['title'].lower())
        if normalized_title not in seen_titles:
            seen_titles.add(normalized_title)
            unique.append(a)
    return unique

# 1. International (BBC)
international = sorted(all_data["BBC World"], key=lambda x: x['pub_datetime'] or now, reverse=True)[:5]

# 2. Ireland
ireland_sources = all_data["RTE"] + all_data["TheJournal"] + all_data["Irish Times"]
ireland = dedup(sorted(ireland_sources, key=lambda x: x['pub_datetime'] or now, reverse=True))[:7]

# 3. Netherlands
nl_sources = all_data["DutchNews"] + all_data["NL Times"]
netherlands = dedup(sorted(nl_sources, key=lambda x: x['pub_datetime'] or now, reverse=True))[:8]

# 4. Leiden
leiden = all_data["Google News Leiden"]

# 5. Tech & Gaming
tech_sources = all_data["The Verge"] + all_data["TechCrunch"] + all_data["Hacker News"] + all_data["Ars Technica"] + all_data["The Register"]
gaming_sources = all_data["Polygon"]

def is_gaming(a):
    gaming_keywords = ['gaming', 'game', 'nintendo', 'playstation', 'xbox', 'steam', 'valve', 'rpg', 'fps', 'mmo']
    title_lower = a['title'].lower()
    desc_lower = a['description'].lower()
    return any(k in title_lower for k in gaming_keywords) or any(k in desc_lower for k in gaming_keywords)

tech_articles = [a for a in tech_sources if not is_gaming(a)]
gaming_articles = [a for a in gaming_sources] + [a for a in tech_sources if is_gaming(a)]

# Sort tech, prefer Verge
verge_tech = [a for a in tech_articles if a['source'] == 'The Verge']
other_tech = [a for a in tech_articles if a['source'] != 'The Verge']

verge_tech = sorted(verge_tech, key=lambda x: x['pub_datetime'] or now, reverse=True)
other_tech = sorted(other_tech, key=lambda x: x['pub_datetime'] or now, reverse=True)

selected_tech = verge_tech[:2]
remaining_tech = sorted(verge_tech[2:] + other_tech, key=lambda x: x['pub_datetime'] or now, reverse=True)
selected_tech += remaining_tech[:(6 - len(selected_tech))]

# Gaming
selected_gaming = dedup(sorted(gaming_articles, key=lambda x: x['pub_datetime'] or now, reverse=True))[:4]

# SRE
sre_candidates = all_data["HN SRE"]
selected_sre = []
for a in sre_candidates:
    # Check for HN points > 200 in description or title if possible
    # hn_sre.xml has points in the title usually " (Points: 245)"
    points_match = re.search(r'Points: (\d+)', a['title'])
    points = int(points_match.group(1)) if points_match else 0
    
    if points > 200 or any(k in a['title'].lower() for k in ['incident', 'postmortem', 'outage', 'reliability', 'sre']):
        selected_sre.append(a)

report_data = {
    "international": international,
    "ireland": ireland,
    "netherlands": netherlands,
    "leiden": leiden,
    "tech": selected_tech,
    "gaming": selected_gaming,
    "sre": selected_sre
}

# Add BREAKING flag
for section in report_data.values():
    for a in section:
        if is_breaking(a['pub_datetime']):
            a['breaking'] = True
        else:
            a['breaking'] = False
        # Remove datetime objects before serialization
        if 'pub_datetime' in a:
            del a['pub_datetime']

print(json.dumps(report_data))
