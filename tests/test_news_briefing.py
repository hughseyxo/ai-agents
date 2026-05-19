import pytest
from unittest.mock import MagicMock, patch
from agents.news_briefing import NewsBriefingAgent
from agents.base import BaseAgent

@pytest.fixture
def agent(tmp_path):
    db_path = tmp_path / "test_agents.db"
    return NewsBriefingAgent(db_path=db_path)

def test_parse_rss_basic(agent):
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
        <channel>
            <item>
                <title>Test Title</title>
                <link>https://example.com/test</link>
                <description>Test Description</description>
                <pubDate>Sat, 16 May 2026 05:00:00 +0000</pubDate>
            </item>
        </channel>
    </rss>"""
    articles = agent._parse_rss(xml, "Test Source", "International")
    assert len(articles) == 1
    assert articles[0]["title"] == "Test Title"
    assert articles[0]["source"] == "Test Source"
    assert articles[0]["category"] == "International"

def test_select_news_filtering(agent):
    articles = [
        {"title": "International 1", "link": "l1", "description": "d1", "source": "S1", "category": "International", "pub_datetime": None},
        {"title": "Ireland 1", "link": "l2", "description": "d2", "source": "S2", "category": "Ireland", "pub_datetime": None},
        {"title": "Tech 1", "link": "l3", "description": "d3", "source": "S3", "category": "Tech", "pub_datetime": None},
        {"title": "Gaming 1", "link": "l4", "description": "d4", "source": "S4", "category": "Gaming", "pub_datetime": None},
    ]
    selected = agent._select_news(articles)
    assert len(selected["International"]) == 1
    assert len(selected["Ireland"]) == 1
    assert len(selected["Tech"]) == 1
    assert len(selected["Gaming"]) == 1

@patch("agents.news_briefing.requests.get")
def test_fetch_retries_on_5xx(mock_get, agent):
    """A 502/503 on first attempt should trigger one retry."""
    ok_response = MagicMock()
    ok_response.status_code = 200
    ok_response.text = """<?xml version="1.0"?>
    <rss version="2.0"><channel><item><title>Retry News</title><link>url</link></item></channel></rss>"""
    fail_response = MagicMock()
    fail_response.status_code = 502

    # First call for each URL: some will return 502, second call returns 200
    call_count = [0]
    def side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return fail_response
        return ok_response

    mock_get.side_effect = side_effect
    agent.context["plan"] = {"today": "2026-05-19"}
    result = agent._fetch_news()
    # Should have retried and got articles
    assert mock_get.call_count >= 2


@patch("agents.news_briefing.requests.get")
def test_fetch_does_not_retry_on_4xx(mock_get, agent):
    """A 429 should NOT trigger a retry (rate limits won't clear immediately)."""
    fail_response = MagicMock()
    fail_response.status_code = 429
    mock_get.return_value = fail_response
    agent.context["plan"] = {"today": "2026-05-19"}
    agent._fetch_news()
    # One call per feed, no retries
    feed_count = sum(len(feeds) for feeds in agent.FEEDS.values())
    assert mock_get.call_count == feed_count


def test_irish_times_uses_google_news_feed(agent):
    """Irish Times should use Google News RSS to avoid 429s."""
    ireland_feeds = dict(agent.FEEDS.get("Ireland", []))
    it_url = ireland_feeds.get("Irish Times", "")
    assert "news.google.com" in it_url


def test_hn_sre_feed_present(agent):
    sre_feeds = agent.FEEDS.get("SRE", [])
    assert len(sre_feeds) > 0


def test_nl_times_feed_is_nltimes_nl(agent):
    """NL Times should use the real NL Times RSS, not a Google News search."""
    nl_feeds = dict(agent.FEEDS.get("Netherlands", []))
    nl_url = nl_feeds.get("NL Times", "")
    assert nl_url.startswith("https://nltimes.nl"), f"Expected nltimes.nl feed, got: {nl_url}"


def test_nos_feed_present(agent):
    """NOS Binnenland (Dutch domestic news) should be in Netherlands feeds."""
    nl_feeds = dict(agent.FEEDS.get("Netherlands", []))
    nos_url = nl_feeds.get("NOS (Dutch)", "")
    assert "feeds.nos.nl" in nos_url


def test_dedup_strips_google_news_source_suffix(agent):
    """Same headline from 3 Google News outlets should collapse to 1."""
    articles = [
        {"title": "Hantavirus ship docks in Netherlands - KSLA", "link": "u1", "description": "d", "source": "NL Times", "category": "Netherlands", "pub_datetime": None},
        {"title": "Hantavirus ship docks in Netherlands - Live 5 News", "link": "u2", "description": "d", "source": "NL Times", "category": "Netherlands", "pub_datetime": None},
        {"title": "Hantavirus ship docks in Netherlands - WIS News 10", "link": "u3", "description": "d", "source": "NL Times", "category": "Netherlands", "pub_datetime": None},
    ]
    selected = agent._select_news(articles)
    assert len(selected["Netherlands"]) == 1


def test_dedup_preserves_genuinely_different_titles(agent):
    articles = [
        {"title": "Dutch government raises taxes - NOS", "link": "u1", "description": "d", "source": "NOS", "category": "Netherlands", "pub_datetime": None},
        {"title": "Amsterdam floods reported - DutchNews", "link": "u2", "description": "d", "source": "DutchNews", "category": "Netherlands", "pub_datetime": None},
    ]
    selected = agent._select_news(articles)
    assert len(selected["Netherlands"]) == 2


@patch("agents.news_briefing.NewsBriefingAgent.synthesize")
def test_translate_dutch_step_updates_articles(mock_synth, agent):
    """Dutch articles should have titles/descriptions replaced with translation."""
    import json as _json
    mock_synth.return_value = _json.dumps([
        {"i": 0, "title": "Amsterdam floods", "description": "Flooding in city centre."}
    ])
    agent.context["news_data"] = {
        "Netherlands": [
            {"title": "Amsterdam overstroomt", "description": "Overstromingen in het centrum.", "source": "NOS (Dutch)", "link": "u1", "breaking": False},
            {"title": "English story", "description": "English desc.", "source": "DutchNews", "link": "u2", "breaking": False},
        ]
    }
    agent._translate_dutch_step()
    nl = agent.context["news_data"]["Netherlands"]
    assert nl[0]["title"] == "Amsterdam floods"
    assert nl[0]["description"] == "Flooding in city centre."
    assert nl[1]["title"] == "English story"  # unchanged


@patch("agents.news_briefing.NewsBriefingAgent.synthesize")
def test_translate_dutch_step_falls_back_on_bad_json(mock_synth, agent):
    """If LLM returns unparseable JSON, original articles are preserved."""
    mock_synth.return_value = "oops, not json"
    original_title = "Amsterdam overstroomt"
    agent.context["news_data"] = {
        "Netherlands": [
            {"title": original_title, "description": "desc", "source": "NOS (Dutch)", "link": "u1", "breaking": False},
        ]
    }
    agent._translate_dutch_step()
    assert agent.context["news_data"]["Netherlands"][0]["title"] == original_title


def test_translate_dutch_step_no_op_when_no_dutch(agent):
    """If no Dutch-source articles, step is a no-op (no synthesize call)."""
    agent.context["news_data"] = {
        "Netherlands": [
            {"title": "English story", "description": "desc", "source": "DutchNews", "link": "u1", "breaking": False},
        ]
    }
    # Should not raise even without mocking synthesize
    agent._translate_dutch_step()
    assert agent.context["news_data"]["Netherlands"][0]["title"] == "English story"


@patch("agents.news_briefing.requests.get")
def test_fetch_news_integration(mock_get, agent):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel><item><title>News</title><link>url</link></item></channel></rss>"""
    mock_get.return_value = mock_response

    # Mock context plan
    agent.context["plan"] = {"today": "2026-05-16"}

    result = agent._fetch_news()
    assert result["count"] > 0
    assert "news_data" in agent.context


# --- Builder tests ---

SAMPLE_NEWS = {
    "International": [
        {"title": "World Event", "link": "https://bbc.com/1", "description": "A world event happened.", "source": "BBC World", "breaking": False},
        {"title": "Global Crisis", "link": "https://bbc.com/2", "description": "Crisis ongoing.", "source": "BBC World", "breaking": True},
    ],
    "Ireland": [
        {"title": "Irish News", "link": "https://rte.ie/1", "description": "Irish thing happened.", "source": "RTE", "breaking": False},
        {"title": "Dublin Update", "link": "https://thejournal.ie/1", "description": "Dublin news.", "source": "TheJournal", "breaking": False},
    ],
    "Netherlands": [
        {"title": "Dutch Story", "link": "https://dutchnews.nl/1", "description": "Dutch event.", "source": "DutchNews", "breaking": False},
    ],
    "Leiden & Local": [
        {"title": "Leiden Event", "link": "https://news.google.com/1", "description": "Local Leiden news.", "source": "Google News Leiden", "breaking": False},
    ],
    "Mullingar": [],
    "Tech": [
        {"title": "Tech Story", "link": "https://theverge.com/1", "description": "Tech happened.", "source": "The Verge", "breaking": False},
    ],
    "Gaming": [
        {"title": "Game Launch", "link": "https://polygon.com/1", "description": "New game out.", "source": "Polygon", "breaking": True},
    ],
    "SRE / Infrastructure": [],
}

TODAY = "2026-05-19"


class TestBuildMarkdown:
    def test_starts_with_news_briefing_header(self, agent):
        md = agent._build_markdown(SAMPLE_NEWS, TODAY)
        assert md.startswith(f"# News Briefing — {TODAY}")

    def test_includes_all_non_empty_sections(self, agent):
        md = agent._build_markdown(SAMPLE_NEWS, TODAY)
        for section in ["International", "Ireland", "Netherlands", "Leiden & Local", "Tech", "Gaming"]:
            assert f"## {section}" in md

    def test_skips_empty_mullingar(self, agent):
        md = agent._build_markdown(SAMPLE_NEWS, TODAY)
        assert "## Mullingar" not in md

    def test_skips_empty_sre(self, agent):
        md = agent._build_markdown(SAMPLE_NEWS, TODAY)
        assert "## SRE" not in md

    def test_includes_mullingar_when_populated(self, agent):
        data = {**SAMPLE_NEWS, "Mullingar": [
            {"title": "Mullingar Fest", "link": "https://news.google.com/m", "description": "Festival.", "source": "Google News Mullingar", "breaking": False},
        ]}
        md = agent._build_markdown(data, TODAY)
        assert "## Mullingar" in md
        assert "Mullingar Fest" in md

    def test_includes_article_titles_and_links(self, agent):
        md = agent._build_markdown(SAMPLE_NEWS, TODAY)
        assert "World Event" in md
        assert "https://bbc.com/1" in md
        assert "Irish News" in md

    def test_breaking_flag_appears(self, agent):
        md = agent._build_markdown(SAMPLE_NEWS, TODAY)
        assert "[BREAKING]" in md

    def test_source_shown_for_multi_source_sections(self, agent):
        md = agent._build_markdown(SAMPLE_NEWS, TODAY)
        assert "RTE" in md
        assert "The Verge" in md


class TestBuildHtmlEmail:
    def test_is_valid_html_document(self, agent):
        html = agent._build_html_email(SAMPLE_NEWS, TODAY)
        assert "<!DOCTYPE html>" in html
        assert "<html>" in html
        assert "</html>" in html

    def test_includes_date_in_header(self, agent):
        html = agent._build_html_email(SAMPLE_NEWS, TODAY)
        assert TODAY in html

    def test_includes_section_headings(self, agent):
        html = agent._build_html_email(SAMPLE_NEWS, TODAY)
        for section in ["International", "Ireland", "Tech"]:
            assert section in html

    def test_skips_empty_mullingar(self, agent):
        html = agent._build_html_email(SAMPLE_NEWS, TODAY)
        assert "Mullingar" not in html

    def test_skips_empty_sre(self, agent):
        html = agent._build_html_email(SAMPLE_NEWS, TODAY)
        assert "SRE" not in html

    def test_includes_article_links_and_titles(self, agent):
        html = agent._build_html_email(SAMPLE_NEWS, TODAY)
        assert 'href="https://bbc.com/1"' in html
        assert "World Event" in html

    def test_breaking_flag_appears(self, agent):
        html = agent._build_html_email(SAMPLE_NEWS, TODAY)
        assert "[BREAKING]" in html

    def test_escapes_html_in_titles(self, agent):
        data = {**SAMPLE_NEWS, "International": [
            {"title": "Event <b>Bold</b> & More", "link": "https://bbc.com/x", "description": "Desc.", "source": "BBC", "breaking": False},
        ]}
        html = agent._build_html_email(data, TODAY)
        assert "&lt;b&gt;" in html
        assert "&amp;" in html
        assert "<b>Bold</b>" not in html

    def test_section_colors_applied(self, agent):
        html = agent._build_html_email(SAMPLE_NEWS, TODAY)
        assert "#8e44ad" in html  # International
        assert "#2ecc71" in html  # Ireland


class TestProvidersOverride:
    def test_claude_is_primary_provider(self, agent):
        assert agent.providers is not None
        assert agent.providers[0]["name"] == "claude"

    def test_gemini_is_fallback(self, agent):
        assert len(agent.providers) == 2
        assert agent.providers[1]["name"] == "gemini"
