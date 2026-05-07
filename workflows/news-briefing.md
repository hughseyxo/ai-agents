# Workflow: Daily News Briefing

## Purpose
Generate a daily news digest covering international headlines, Irish news, Netherlands/Leiden news, tech, gaming, and optionally SRE/infrastructure. Email the digest via Gmail.

## Steps

### 1. Import tools
Use ToolSearch to load required tools before proceeding:
- Search `"gmail gmail_send"` to import the local Gmail connector
- Search `"WebFetch"` to import the web fetcher

### 2. Get today's date
Note today's date in YYYY-MM-DD format and as a readable string (e.g. "Friday, 2 May 2026"). Also note the timestamp for 3 hours ago (for [BREAKING] detection in tech/gaming sections).

### 3. Fetch RSS feeds
Make parallel `WebFetch` calls:

**Feed A — RTE News:**
- URL: `https://www.rte.ie/feeds/rss/?model=news`

**Feed B — TheJournal.ie:**
- URL: `https://www.thejournal.ie/feed/`

**Feed C — Irish Times:**
- URL: `https://www.irishtimes.com/cmlink/the-irish-times-news-1.1319192`

**Feed D — BBC World News:**
- URL: `https://feeds.bbci.co.uk/news/world/rss.xml`

**Feed E — DutchNews.nl:**
- URL: `https://www.dutchnews.nl/feed/`

**Feed F — NL Times:**
- URL: `https://nltimes.nl/feed`
- If this returns 403, try fallback: `https://news.google.com/rss/search?q=Netherlands&hl=en` (general Netherlands news from Google News)

**Feed G — Google News (Leiden):**
- URL: `https://news.google.com/rss/search?q=Leiden+Netherlands&hl=en`

**Feed H — The Verge (via Google News):**
- URL: `https://news.google.com/rss/search?q=site:theverge.com&hl=en`

**Feed I — TechCrunch:**
- URL: `https://techcrunch.com/feed/`

**Feed J — Hacker News (frontpage, high score):**
- URL: `https://hnrss.org/frontpage?count=10&points=100`

**Feed K — Polygon (via Google News):**
- URL: `https://news.google.com/rss/search?q=site:polygon.com+gaming&hl=en`

**Feed L — Ars Technica (via Google News):**
- URL: `https://news.google.com/rss/search?q=site:arstechnica.com&hl=en`

**Feed M — The Register (via Google News):**
- URL: `https://news.google.com/rss/search?q=site:theregister.com&hl=en`

**Feed N — Hacker News (SRE/infra topics):**
- URL: `https://hnrss.org/frontpage?q=kubernetes+OR+linux+OR+incident+OR+postmortem+OR+observability+OR+distributed+systems+OR+storage+OR+reliability+OR+infrastructure&points=200`

If any feed fails, note the failure and continue with the remaining feeds.

### 4. Parse and select articles
From each feed's XML/Atom response, extract recent items. For each item collect:
- **title** — the article headline
- **link** — the article URL
- **pubDate** — the publication date (check `<pubDate>`, `<published>`, or `<updated>` depending on feed format)
- **source** — which feed it came from (for attribution)
- **description** — strip HTML tags, take the first 2-3 sentences (max ~250 characters) as a summary. Aim for two full lines of context per article.

**Deduplication:** Before selecting articles, deduplicate across all feeds. If the same story appears in multiple sources (very similar titles or same URL), keep one and note all sources.

**[BREAKING] flag:** If an article's pubDate is within the last 3 hours, prefix its headline with `[BREAKING]` (applies to tech and gaming sections only).

**International section:** From Feed D (BBC World), select the **5 most recent** items covering distinct stories.

**Ireland section:** Merge articles from Feeds A (RTE), B (TheJournal.ie), and C (Irish Times). Deduplicate — if two sources cover the same story, keep one and note both sources. Sort by date (newest first). Cap at 7 items. Attribute each article to its source.

**Netherlands section:** Merge DutchNews.nl and NL Times articles. If two headlines cover the same story, keep only one and note both sources. Sort by date (newest first). Cap at 8 items.

**Leiden & Local section:** From Feed G (Google News Leiden). If no results, note it.

**Tech section:** Merge articles from Feeds H (The Verge), I (TechCrunch), J (Hacker News frontpage), L (Ars Technica), and M (The Register). Exclude gaming-focused articles from these feeds. **Prefer The Verge as a primary source** — when multiple sources cover the same story, favour The Verge's version. Ensure at least 2 Verge articles appear in the final selection if available. Sort by date (newest first). Select 5-6 of the most significant stories. Attribute each article to its source.

**Gaming section:** From Feed K (Polygon), plus any gaming-focused articles from Feeds H and L. Sort by date (newest first). Select 3-4 stories. Attribute each article to its source.

**Total Tech + Gaming combined: 8-10 stories.**

**SRE/Infra section (conditional):** From Feed N (Hacker News SRE/infra). Only include items that meet at least one of:
- A major incident or postmortem from a recognisable company
- A significant release or architectural shift in a tool relevant to SRE work (e.g. kernel, storage systems, Prometheus, Terraform, container runtimes)
- Score > 200 on Hacker News (already filtered by feed URL)

If no items meet the threshold, omit this section entirely. Do not pad with marginal items.

### 5. Format the markdown report
Save to `output/daily-news-briefing-YYYY-MM-DD.md`:

```
# News Briefing — [DATE]

## International
- **[Headline]** — [1-line summary]
  [link]

## Ireland
- **[Headline]** ([Source]) — [1-line summary]
  [link]

## Netherlands
- **[Headline]** ([Source]) — [1-line summary]
  [link]

## Leiden & Local
- **[Headline]** — [1-line summary]
  [link]

## Tech
- **[BREAKING] [Headline]** ([Source]) — [2-3 sentence summary]
  [link]

## Gaming
- **[Headline]** ([Source]) — [2-3 sentence summary]
  [link]

## SRE / Infrastructure
- **[Headline]** ([Source]) — [2-3 sentence summary]
  [link]
```

If the SRE section has no qualifying items, omit it from both the markdown file and the email.

### 6. Build the HTML email
Construct the email body as HTML with inline CSS only:

```html
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f4f4f4;font-family:Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f4;padding:24px 0;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08);">

        <!-- Header -->
        <tr><td style="background:#1a1a2e;padding:28px 32px;">
          <p style="margin:0;color:#a0a8c0;font-size:13px;letter-spacing:1px;text-transform:uppercase;">News Briefing</p>
          <h1 style="margin:4px 0 0;color:#ffffff;font-size:24px;">[WEEKDAY, DATE]</h1>
        </td></tr>

        <!-- International Section -->
        <tr><td style="padding:24px 32px 0;">
          <h2 style="margin:0 0 12px;font-size:13px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#1a1a2e;">International</h2>
          [For each article:
          <p style="margin:0 0 10px;padding:10px 14px;background:#f5f0ff;border-left:3px solid #8e44ad;border-radius:4px;font-size:14px;color:#333;">
            <a href="[LINK]" style="color:#1a1a2e;text-decoration:none;font-weight:700;">[HEADLINE]</a><br/>
            <span style="color:#666;font-size:13px;">[SUMMARY]</span>
          </p>]
        </td></tr>

        <!-- Ireland Section -->
        <tr><td style="padding:20px 32px 0;">
          <h2 style="margin:0 0 12px;font-size:13px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#1a1a2e;">Ireland</h2>
          [For each article:
          <p style="margin:0 0 10px;padding:10px 14px;background:#f0fff4;border-left:3px solid #2ecc71;border-radius:4px;font-size:14px;color:#333;">
            <a href="[LINK]" style="color:#1a1a2e;text-decoration:none;font-weight:700;">[HEADLINE]</a>
            <span style="color:#2ecc71;font-size:11px;font-weight:700;"> [SOURCE]</span><br/>
            <span style="color:#666;font-size:13px;">[SUMMARY]</span>
          </p>]
        </td></tr>

        <!-- Netherlands Section -->
        <tr><td style="padding:20px 32px 0;">
          <h2 style="margin:0 0 12px;font-size:13px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#1a1a2e;">Netherlands</h2>
          [For each article:
          <p style="margin:0 0 10px;padding:10px 14px;background:#fff8f0;border-left:3px solid #e67e22;border-radius:4px;font-size:14px;color:#333;">
            <a href="[LINK]" style="color:#1a1a2e;text-decoration:none;font-weight:700;">[HEADLINE]</a>
            <span style="color:#e67e22;font-size:11px;font-weight:700;"> [SOURCE]</span><br/>
            <span style="color:#666;font-size:13px;">[SUMMARY]</span>
          </p>]
        </td></tr>

        <!-- Leiden & Local Section -->
        <tr><td style="padding:20px 32px 0;">
          <h2 style="margin:0 0 12px;font-size:13px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#1a1a2e;">Leiden & Local</h2>
          [For each article:
          <p style="margin:0 0 10px;padding:10px 14px;background:#f0f4ff;border-left:3px solid #4a6fa5;border-radius:4px;font-size:14px;color:#333;">
            <a href="[LINK]" style="color:#1a1a2e;text-decoration:none;font-weight:700;">[HEADLINE]</a><br/>
            <span style="color:#666;font-size:13px;">[SUMMARY]</span>
          </p>]
          [If no Leiden results: <p style="margin:0;color:#999;font-size:14px;font-style:italic;">No Leiden-specific news today</p>]
        </td></tr>

        <!-- Tech Section -->
        <tr><td style="padding:20px 32px 0;">
          <h2 style="margin:0 0 12px;font-size:13px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#1a1a2e;">Tech</h2>
          [For each article:
          <p style="margin:0 0 10px;padding:10px 14px;background:#f0f7ff;border-left:3px solid #3498db;border-radius:4px;font-size:14px;color:#333;">
            <a href="[LINK]" style="color:#1a1a2e;text-decoration:none;font-weight:700;">[BREAKING] [HEADLINE]</a>
            <span style="color:#3498db;font-size:11px;font-weight:700;"> [SOURCE]</span><br/>
            <span style="color:#666;font-size:13px;">[SUMMARY]</span>
          </p>]
        </td></tr>

        <!-- Gaming Section -->
        <tr><td style="padding:20px 32px 0;">
          <h2 style="margin:0 0 12px;font-size:13px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#1a1a2e;">Gaming</h2>
          [For each article:
          <p style="margin:0 0 10px;padding:10px 14px;background:#f5f0ff;border-left:3px solid #9b59b6;border-radius:4px;font-size:14px;color:#333;">
            <a href="[LINK]" style="color:#1a1a2e;text-decoration:none;font-weight:700;">[HEADLINE]</a>
            <span style="color:#9b59b6;font-size:11px;font-weight:700;"> [SOURCE]</span><br/>
            <span style="color:#666;font-size:13px;">[SUMMARY]</span>
          </p>]
        </td></tr>

        <!-- SRE / Infrastructure Section (CONDITIONAL — only include if items qualify) -->
        <tr><td style="padding:20px 32px 0;">
          <h2 style="margin:0 0 12px;font-size:13px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#1a1a2e;">SRE / Infrastructure</h2>
          [For each article:
          <p style="margin:0 0 10px;padding:10px 14px;background:#fff5f5;border-left:3px solid #e74c3c;border-radius:4px;font-size:14px;color:#333;">
            <a href="[LINK]" style="color:#1a1a2e;text-decoration:none;font-weight:700;">[HEADLINE]</a>
            <span style="color:#e74c3c;font-size:11px;font-weight:700;"> [SOURCE]</span><br/>
            <span style="color:#666;font-size:13px;">[SUMMARY]</span>
          </p>]
        </td></tr>

        <!-- Footer -->
        <tr><td style="padding:24px 32px;border-top:1px solid #eee;">
          <p style="margin:0;color:#aaa;font-size:12px;text-align:center;">Generated by your News Briefing Agent</p>
        </td></tr>

      </table>
    </td></tr>
  </table>
</body>
</html>
```

If the SRE/Infra section has no qualifying items, omit that entire `<tr>` block from the HTML. Do not include an empty section or a "no items" placeholder.

### 7. Send via Gmail MCP
Call `mcp__gmail__gmail_send` with:
- to: `cianohughes@gmail.com`
- subject: `News Briefing — [WEEKDAY, DATE]`
- body: the full HTML from step 6
- mimeType: `text/html`

If Gmail fails, note it in the saved file but do not stop.

### 8. Confirm completion
Output: `Daily briefing saved to output/daily-news-briefing-YYYY-MM-DD.md and email sent.`

## Constraints
- Do NOT run any git commands
- If a feed fetch fails, skip that source and continue — do not abort
- Keep summaries to 2-3 sentences (~2 lines) per article — enough context to decide whether to click through
- Total Tech + Gaming stories: 8-10. SRE/Infra is additive (only if items qualify)
- Only include the SRE section if items genuinely meet the threshold — do not pad
