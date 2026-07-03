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
