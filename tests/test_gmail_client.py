import base64

from agents import gmail_client


def test_send_email_posts_base64_mime(monkeypatch):
    monkeypatch.setattr(gmail_client, "get_access_token", lambda: "tok")
    monkeypatch.setattr(gmail_client, "_get_sender_address", lambda: "me@x.ie")
    captured = {}

    def fake_request(method, path, params=None, body=None):
        captured.update(method=method, path=path, body=body)
        return {"id": "sent-1"}

    monkeypatch.setattr(gmail_client, "gmail_request", fake_request)
    out = gmail_client.send_email("to@x.ie", "Subj", "<b>hi</b>")
    assert out == {"id": "sent-1"}
    assert captured["method"] == "POST" and captured["path"] == "/users/me/messages/send"
    raw = base64.urlsafe_b64decode(captured["body"]["raw"] + "==")
    assert b"Subject: Subj" in raw and b"to@x.ie" in raw


def test_gmail_request_sets_urlopen_timeout(monkeypatch):
    monkeypatch.setattr(gmail_client, "get_access_token", lambda: "tok")
    captured = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{}"

    def fake_urlopen(req, timeout=None):
        captured["timeout"] = timeout
        return FakeResp()

    monkeypatch.setattr(gmail_client.urllib.request, "urlopen", fake_urlopen)
    gmail_client.gmail_request("GET", "/users/me/profile")
    assert captured["timeout"] is not None and captured["timeout"] > 0
