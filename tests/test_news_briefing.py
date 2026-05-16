import pytest
from unittest.mock import MagicMock, patch
from agents.news_briefing import NewsBriefingAgent

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
