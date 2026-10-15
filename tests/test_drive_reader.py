import json
import subprocess

import pytest

from agents import drive_reader
from agents.drive_reader import DOCX_MIME, GDOC_MIME, READ_TOOL, SEARCH_TOOL, fetch_latest_cv, parse_stream

FILES = [
    {"id": "pdf", "title": "Cian_Hughes_CV_General_v6.pdf", "mimeType": "application/pdf",
     "modifiedTime": "2026-10-01T00:00:00Z"},
    {"id": "gdoc", "title": "Cian_Hughes_CV_General_v5", "mimeType": GDOC_MIME,
     "modifiedTime": "2026-08-27T18:19:55Z"},
    {"id": "docx", "title": "Cian_Hughes_CV_General_v5.docx", "mimeType": DOCX_MIME,
     "modifiedTime": "2026-09-29T07:39:05Z"},
    {"id": "other", "title": "Holiday plan", "mimeType": GDOC_MIME,
     "modifiedTime": "2026-10-10T00:00:00Z"},
]


def _events(read_id="docx", content="CIAN HUGHES\nSenior SRE", search_error=False, list_content=False):
    def use(uid, name, inp):
        return {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": uid, "name": name, "input": inp}]}}

    def result(uid, text, is_error=False):
        body = [{"type": "text", "text": text}] if list_content else text
        return {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": uid, "content": body, "is_error": is_error}]}}

    events = [
        {"type": "system", "subtype": "init"},
        use("t1", SEARCH_TOOL, {"query": "x"}),
        result("t1", "boom" if search_error else json.dumps({"files": FILES}), is_error=search_error),
    ]
    if read_id:
        events += [use("t2", READ_TOOL, {"fileId": read_id}),
                   result("t2", json.dumps({"fileContent": content}))]
    events.append({"type": "result", "subtype": "success", "result": "DONE"})
    return "\n".join(json.dumps(e) for e in events) + "\nnot json noise\n"


def test_parse_stream_returns_newest_supported_cv():
    cv = parse_stream(_events())
    assert cv == {"id": "docx", "name": "Cian_Hughes_CV_General_v5.docx",
                  "modifiedTime": "2026-09-29T07:39:05Z", "text": "CIAN HUGHES\nSenior SRE"}


def test_parse_stream_accepts_list_shaped_tool_results():
    assert parse_stream(_events(list_content=True))["id"] == "docx"


def test_parse_stream_rejects_wrong_file_read():
    with pytest.raises(RuntimeError, match="newest"):
        parse_stream(_events(read_id="gdoc"))


def test_parse_stream_missing_read_result():
    with pytest.raises(RuntimeError):
        parse_stream(_events(read_id=None))


def test_parse_stream_search_error():
    with pytest.raises(RuntimeError):
        parse_stream(_events(search_error=True))


def test_parse_stream_empty_content():
    with pytest.raises(RuntimeError, match="empty"):
        parse_stream(_events(content="   "))


def test_parse_stream_no_cv_files():
    stream = _events().replace("Cian_Hughes_CV_General", "Something_Else")
    with pytest.raises(RuntimeError, match="No CV"):
        parse_stream(stream)


def test_fetch_latest_cv_invokes_confined_cli(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return subprocess.CompletedProcess(cmd, 0, stdout=_events(), stderr="")

    monkeypatch.setattr(drive_reader.subprocess, "run", fake_run)
    assert fetch_latest_cv()["id"] == "docx"
    cmd = seen["cmd"]
    assert "--dangerously-skip-permissions" not in cmd
    allowed = cmd[cmd.index("--allowedTools") + 1:]
    assert set(allowed) == {"ToolSearch", SEARCH_TOOL, READ_TOOL}
    assert "Cian_Hughes_CV_General" in seen["kw"]["input"]
    assert seen["kw"]["timeout"] > 0


def test_fetch_latest_cv_cli_failure(monkeypatch):
    monkeypatch.setattr(drive_reader.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="auth expired"))
    with pytest.raises(RuntimeError, match="auth expired"):
        fetch_latest_cv()


def test_fetch_latest_cv_timeout(monkeypatch):
    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)
    monkeypatch.setattr(drive_reader.subprocess, "run", boom)
    with pytest.raises(RuntimeError, match="timed out"):
        fetch_latest_cv()
