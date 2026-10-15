import io
import zipfile

import pytest

from agents import drive_reader
from agents.drive_reader import DOCX_MIME, GDOC_MIME, docx_to_text, find_latest_cv, read_cv_text


def _docx(paragraphs):
    body = "".join(
        f'<w:p><w:r><w:t>{p[0]}</w:t></w:r>'
        + "".join(f'<w:r><w:tab/><w:t xml:space="preserve">{x}</w:t></w:r>' for x in p[1:])
        + "</w:p>"
        for p in paragraphs
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def test_docx_to_text_paragraphs_and_tabs():
    data = _docx([["Cian Hughes"], ["Skills", "Kubernetes"], ["SRE &amp; on-call"]])
    assert docx_to_text(data) == "Cian Hughes\nSkills\tKubernetes\nSRE & on-call"


def test_docx_to_text_rejects_non_docx():
    with pytest.raises(ValueError):
        docx_to_text(b"not a zip")


def test_docx_to_text_rejects_dtd():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>')
    with pytest.raises(ValueError):
        docx_to_text(buf.getvalue())


def test_find_latest_cv_prefers_supported_newest(monkeypatch):
    captured = {}

    def fake_json(path, params):
        captured.update(params)
        return {"files": [
            {"id": "pdf", "name": "Cian_Hughes_CV_General_v6.pdf", "mimeType": "application/pdf",
             "modifiedTime": "2026-10-01T00:00:00Z"},
            {"id": "docx", "name": "Cian_Hughes_CV_General_v5.docx", "mimeType": DOCX_MIME,
             "modifiedTime": "2026-09-29T07:39:05Z"},
            {"id": "gdoc", "name": "Cian_Hughes_CV_General_v5", "mimeType": GDOC_MIME,
             "modifiedTime": "2026-08-27T18:19:55Z"},
        ]}

    monkeypatch.setattr(drive_reader, "_get_json", fake_json)
    cv = find_latest_cv()
    assert cv["id"] == "docx"
    assert "name contains 'Cian_Hughes_CV_General'" in captured["q"]
    assert "'me' in owners" in captured["q"]
    assert captured["orderBy"] == "modifiedTime desc"


def test_find_latest_cv_none(monkeypatch):
    monkeypatch.setattr(drive_reader, "_get_json", lambda p, q: {"files": []})
    assert find_latest_cv() is None


def test_read_cv_text_gdoc_exports_plain_text(monkeypatch):
    calls = []
    monkeypatch.setattr(drive_reader, "_get_bytes",
                        lambda path, params=None: calls.append((path, params)) or b"\xef\xbb\xbfHello CV\r\n")
    assert read_cv_text({"id": "g1", "mimeType": GDOC_MIME}) == "Hello CV"
    assert calls == [("/files/g1/export", {"mimeType": "text/plain"})]


def test_read_cv_text_docx_downloads(monkeypatch):
    calls = []
    monkeypatch.setattr(drive_reader, "_get_bytes",
                        lambda path, params=None: calls.append((path, params)) or _docx([["Hi"]]))
    assert read_cv_text({"id": "d1", "mimeType": DOCX_MIME}) == "Hi"
    assert calls == [("/files/d1", {"alt": "media"})]


def test_read_cv_text_unsupported_mime():
    with pytest.raises(ValueError):
        read_cv_text({"id": "p", "mimeType": "application/pdf"})


def test_requests_use_readonly_token(monkeypatch):
    seen = {}
    monkeypatch.setattr(drive_reader.drive_client, "get_access_token",
                        lambda token_file=None: seen.setdefault("tf", token_file) and "tok")

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"files": []}'

    monkeypatch.setattr(drive_reader.urllib.request, "urlopen", lambda req, timeout: FakeResp())
    assert drive_reader._get_json("/files", {"q": "x"}) == {"files": []}
    assert seen["tf"] == drive_reader.TOKEN_FILE_RO
