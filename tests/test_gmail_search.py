import base64

from agents import gmail_client


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def test_search_messages_paginates_and_caps(monkeypatch):
    pages = {
        None: {"messages": [{"id": "1", "threadId": "t1"}, {"id": "2", "threadId": "t2"}],
               "nextPageToken": "p2"},
        "p2": {"messages": [{"id": "3", "threadId": "t3"}]},
    }
    calls = []

    def fake_request(method, path, params=None, body=None):
        calls.append(params)
        return pages[params.get("pageToken")]

    monkeypatch.setattr(gmail_client, "gmail_request", fake_request)
    assert [m["id"] for m in gmail_client.search_messages("q", max_results=10)] == ["1", "2", "3"]
    assert calls[0]["q"] == "q"
    assert [m["id"] for m in gmail_client.search_messages("q", max_results=2)] == ["1", "2"]


def test_search_messages_empty(monkeypatch):
    monkeypatch.setattr(gmail_client, "gmail_request", lambda *a, **k: {"resultSizeEstimate": 0})
    assert gmail_client.search_messages("q") == []


def test_get_message_requests_full_format(monkeypatch):
    seen = {}
    monkeypatch.setattr(gmail_client, "gmail_request",
                        lambda m, p, params=None, body=None: seen.update(path=p, params=params) or {"id": "x"})
    gmail_client.get_message("abc")
    assert seen == {"path": "/users/me/messages/abc", "params": {"format": "full"}}


def test_extract_message_prefers_plain_text():
    msg = {
        "id": "m1", "threadId": "t1", "internalDate": "1760000000000", "snippet": "snip",
        "payload": {
            "headers": [{"name": "Subject", "value": "Your application"},
                        {"name": "From", "value": "Adyen <jobs@adyen.com>"}],
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/html", "body": {"data": _b64("<p>HTML <b>body</b></p>")}},
                {"mimeType": "text/plain", "body": {"data": _b64("Plain   body\n\nthanks")}},
            ],
        },
    }
    out = gmail_client.extract_message(msg)
    assert out["id"] == "m1" and out["thread_id"] == "t1"
    assert out["subject"] == "Your application"
    assert out["sender"] == "Adyen <jobs@adyen.com>"
    assert out["date"].startswith("2025-10-09")
    assert out["body"] == "Plain body thanks"


def test_extract_message_html_only_and_cap():
    html = "<style>.x{}</style><div>Hello " + ("word " * 1000) + "</div>"
    msg = {"id": "m", "threadId": "t", "payload": {
        "headers": [], "mimeType": "text/html", "body": {"data": _b64(html)}}}
    out = gmail_client.extract_message(msg)
    assert out["body"].startswith("Hello word")
    assert ".x" not in out["body"]
    assert len(out["body"]) <= 1500
    assert out["subject"] == "" and out["date"] == ""


def test_extract_message_falls_back_to_snippet():
    msg = {"id": "m", "threadId": "t", "snippet": "just a snippet",
           "payload": {"headers": [], "mimeType": "multipart/mixed", "parts": []}}
    assert gmail_client.extract_message(msg)["body"] == "just a snippet"
