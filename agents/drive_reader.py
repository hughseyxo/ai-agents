"""Read-only Google Drive access for the job-hunt agent: find the newest CV and
return its plain text.

Uses its own token file (drive.readonly scope, created by
`scripts/setup_drive_oauth.py --readonly`) so it never widens or disturbs the
drive.file token the plant digest push relies on. Token refresh is shared
with agents/drive_client.py.
"""

import io
import json
import os
import re
import urllib.parse
import urllib.request
import zipfile
from xml.etree import ElementTree

from . import drive_client

TOKEN_FILE_RO = os.path.expanduser("~/.google_tokens_drive_ro.json")
SCOPE_RO = "https://www.googleapis.com/auth/drive.readonly"
GDOC_MIME = "application/vnd.google-apps.document"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
SUPPORTED_MIMES = (GDOC_MIME, DOCX_MIME)
CV_PREFIX = "Cian_Hughes_CV_General"

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _open(path: str, params: dict | None):
    token = drive_client.get_access_token(TOKEN_FILE_RO)
    url = f"{drive_client.API_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    return urllib.request.urlopen(req, timeout=drive_client.REQUEST_TIMEOUT)


def _get_json(path: str, params: dict | None = None) -> dict:
    with _open(path, params) as resp:
        return json.loads(resp.read())


def _get_bytes(path: str, params: dict | None = None) -> bytes:
    with _open(path, params) as resp:
        return resp.read()


def find_latest_cv(prefix: str = CV_PREFIX) -> dict | None:
    """Most recently modified Google Doc / .docx whose name contains prefix
    (PDFs are skipped — no stdlib text extraction). Keys: id, name, mimeType,
    modifiedTime."""
    q = (f"name contains '{drive_client._escape_query_value(prefix)}' "
         "and trashed = false")
    result = _get_json("/files", {
        "q": q,
        "orderBy": "modifiedTime desc",
        "pageSize": 20,
        "fields": "files(id,name,mimeType,modifiedTime)",
    })
    for f in result.get("files", []):
        if f.get("mimeType") in SUPPORTED_MIMES:
            return f
    return None


def docx_to_text(data: bytes) -> str:
    """Plain text of a .docx: one line per paragraph, tabs preserved."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as e:
        raise ValueError(f"Not a readable .docx: {e}") from e
    # stdlib parser is fine here: the input is the user's own CV from their own
    # Drive, and expat never resolves external entities.
    root = ElementTree.fromstring(xml)
    lines = []
    for para in root.iter(f"{_W}p"):
        parts = []
        for node in para.iter():
            if node.tag == f"{_W}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{_W}tab":
                parts.append("\t")
            elif node.tag in (f"{_W}br", f"{_W}cr"):
                parts.append("\n")
        line = "".join(parts).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def read_cv_text(file: dict) -> str:
    mime = file.get("mimeType")
    if mime == GDOC_MIME:
        raw = _get_bytes(f"/files/{file['id']}/export", {"mimeType": "text/plain"})
        text = raw.decode("utf-8-sig", errors="replace")
    elif mime == DOCX_MIME:
        text = docx_to_text(_get_bytes(f"/files/{file['id']}", {"alt": "media"}))
    else:
        raise ValueError(f"Unsupported CV mime type: {mime}")
    return re.sub(r"\r\n?", "\n", text).strip()
