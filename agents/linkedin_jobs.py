"""LinkedIn public guest-jobs client: search, card parsing, description fetch,
and sanitisation of the third-party text it returns.

The guest endpoints need no auth/cookie (verified live 2026-08-06). They are
unofficial, so every parser fails soft: changed markup yields no jobs rather
than an exception. Everything returned here is untrusted free text — callers
must only ever pass it to tool-less LLM prompts (see JobHuntAgent).
"""

import html as html_lib
import re
import sys
import time
from datetime import datetime
from html.parser import HTMLParser
from typing import Dict, List, Optional

import requests

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
POSTING_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
WORKPLACE = {"remote": 2, "hybrid": 3}
DESCRIPTION_CAP = 6000
REQUEST_TIMEOUT = 15

_LINKEDIN_JOB_URL_RE = re.compile(r"^https://[a-z]{2,3}\.linkedin\.com/jobs/view/[a-z0-9\-]+$")
_STRIP_CHARS_RE = re.compile(r"[<>&{}\[\]|`]")
_WHITESPACE_RE = re.compile(r"\s+")
# Not a security boundary by itself — one more layer that catches the obvious
# case of a title/company written as an instruction rather than a job posting.
_INJECTION_MARKERS_RE = re.compile(
    r"\b(ignore|disregard|forget)\s+(previous|prior|all|above|the)\b"
    r"|\b(system|assistant)\s*:"
    r"|\byou\s+(are|must|should)\s+now\b"
    r"|\bnew\s+instructions?\b",
    re.IGNORECASE,
)


class RateLimitedError(RuntimeError):
    pass


def looks_like_injection(*texts: str) -> bool:
    """True if any string contains instruction-shaped phrasing aimed at an LLM
    rather than ordinary job-posting text."""
    return any(_INJECTION_MARKERS_RE.search(t or "") for t in texts)


def sanitize_text(text: Optional[str], cap: int = 120) -> str:
    """Collapse to one line, drop markup metacharacters, cap length. Reduces
    (does not eliminate) the injection surface; looks_like_injection() is the
    separate check that rejects obviously instruction-shaped text."""
    text = (text or "").replace("\n", " ").replace("\r", " ")
    text = _STRIP_CHARS_RE.sub("", text)
    return _WHITESPACE_RE.sub(" ", text).strip()[:cap]


def sanitize_url(url: str) -> Optional[str]:
    """Strip the query string; None unless it's a genuine linkedin.com/jobs/view/ URL."""
    base = (url or "").split("?", 1)[0].strip()
    return base if _LINKEDIN_JOB_URL_RE.fullmatch(base) else None


def validate_posted_date(posted: Optional[str]) -> Optional[str]:
    try:
        datetime.strptime(posted, "%Y-%m-%d")
        return posted
    except (ValueError, TypeError):
        return None


def normalise_job(raw: Dict) -> Optional[Dict]:
    """Sanitise a parsed card. None if the URL/date is invalid, a required field
    is empty after sanitising, or the text looks like a prompt injection."""
    url = sanitize_url(raw.get("url", ""))
    posted = validate_posted_date(raw.get("posted"))
    title = sanitize_text(raw.get("title"))
    company = sanitize_text(raw.get("company"))
    if not (url and posted and title and company):
        return None
    if looks_like_injection(title, company):
        return None
    return {
        "job_id": str(raw["job_id"]),
        "url": url,
        "title": title,
        "company": company,
        "location": sanitize_text(raw.get("location"), cap=80) or "Netherlands",
        "posted": posted,
    }


class _JobCardParser(HTMLParser):
    """Extracts (job_id, title, company, location, posted, url) from a single
    search-result <li> fragment. Malformed cards yield no result."""

    def __init__(self):
        super().__init__()
        self.job_id: Optional[str] = None
        self.url: Optional[str] = None
        self.title: Optional[str] = None
        self.company: Optional[str] = None
        self.location: Optional[str] = None
        self.posted: Optional[str] = None
        self._in_title = False
        self._in_company = False
        self._in_location = False

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        classes = attrs_d.get("class", "") or ""

        if tag == "div" and "data-entity-urn" in attrs_d and self.job_id is None:
            m = re.search(r"jobPosting:(\d+)", attrs_d["data-entity-urn"] or "")
            if m:
                self.job_id = m.group(1)
        if tag == "a" and "base-card__full-link" in classes and self.url is None:
            self.url = attrs_d.get("href", "")
        if tag == "h3" and "base-search-card__title" in classes:
            self._in_title = True
        if tag == "h4" and "base-search-card__subtitle" in classes:
            self._in_company = True
        if tag == "span" and "job-search-card__location" in classes:
            self._in_location = True
        if tag == "time" and "datetime" in attrs_d:
            self.posted = attrs_d["datetime"]

    def handle_endtag(self, tag):
        if tag == "h3":
            self._in_title = False
        if tag == "h4":
            self._in_company = False
        if tag == "span":
            self._in_location = False

    def handle_data(self, data):
        text = data.strip()
        if not text:
            return
        if self._in_title and not self.title:
            self.title = html_lib.unescape(text)
        elif self._in_company and not self.company:
            self.company = html_lib.unescape(text)
        elif self._in_location and not self.location:
            self.location = html_lib.unescape(text)

    def result(self) -> Optional[Dict]:
        if not (self.job_id and self.url and self.title and self.company):
            return None
        return {
            "job_id": self.job_id,
            "url": self.url,
            "title": self.title,
            "company": self.company,
            "location": self.location or "",
            "posted": self.posted or "",
        }


def parse_job_cards(html: str) -> List[Dict]:
    """Split a search response into per-<li> fragments and parse each
    independently, so one malformed card can't break the rest."""
    jobs = []
    for card in re.split(r"(?=<li>)", html or ""):
        if "data-entity-urn" not in card:
            continue
        parser = _JobCardParser()
        try:
            parser.feed(card)
        except Exception:
            continue
        job = parser.result()
        if job:
            jobs.append(job)
    return jobs


class _TextExtractor(HTMLParser):
    """Collects visible text, optionally only inside the first element whose
    class contains `target_class` (tracked by tag depth)."""

    _SKIP = {"script", "style", "noscript"}
    _VOID = {"br", "img", "hr", "meta", "link", "input", "source", "wbr"}

    def __init__(self, target_class: Optional[str] = None):
        super().__init__()
        self.target_class = target_class
        self.parts: List[str] = []
        self._depth = 0 if target_class else 1  # >0 means "capturing"
        self._done = False
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1
            return
        if tag in self._VOID:
            return
        if self._depth:
            if self.target_class:
                self._depth += 1
            return
        classes = dict(attrs).get("class", "") or ""
        if not self._done and self.target_class in classes:
            self._depth = 1

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self._skip = max(0, self._skip - 1)
            return
        if tag in self._VOID or not self.target_class or not self._depth:
            return
        self._depth -= 1
        if not self._depth:
            self._done = True

    def handle_data(self, data):
        if self._depth and not self._skip and data.strip():
            self.parts.append(data.strip())

    def text(self) -> str:
        return _WHITESPACE_RE.sub(" ", " ".join(self.parts)).strip()


def _extract_text(html: str, target_class: Optional[str] = None) -> str:
    parser = _TextExtractor(target_class)
    try:
        parser.feed(html or "")
    except Exception:
        pass
    return parser.text()


def parse_description(html: str) -> str:
    """Job description text from a guest jobPosting page: the
    `.show-more-less-html__markup` block, else all visible page text."""
    text = _extract_text(html, "show-more-less-html__markup") or _extract_text(html)
    return text[:DESCRIPTION_CAP]


def _get(url: str, params: Optional[Dict] = None) -> Optional[str]:
    """GET with one retry on 5xx. Raises RateLimitedError on 429; None on any
    other failure."""
    headers = {"User-Agent": USER_AGENT}
    for attempt in range(2):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as e:
            print(f"[linkedin_jobs] fetch error: {e}", file=sys.stderr)
            return None
        if resp.status_code == 200:
            return resp.text
        if resp.status_code == 429:
            raise RateLimitedError(f"LinkedIn returned 429 for {url}")
        if resp.status_code >= 500 and attempt == 0:
            time.sleep(3)
            continue
        print(f"[linkedin_jobs] fetch failed: HTTP {resp.status_code}", file=sys.stderr)
        return None
    return None


def fetch_search(query: str, wt: int, start: int = 0) -> List[Dict]:
    """One page (10 cards) of NL search results for a workplace type
    (2=remote, 3=hybrid), posted in the last 48h. Raw, unsanitised cards."""
    params = {
        "keywords": query,
        "location": "Netherlands",
        "f_TPR": "r172800",
        "f_WT": wt,
        "start": start,
    }
    return parse_job_cards(_get(SEARCH_URL, params) or "")


def fetch_description(job_id: str) -> Optional[str]:
    """Description text for a job id; None on any failure (incl. 429), so the
    caller can retry on a later run instead of scoring on the title alone."""
    if not str(job_id).isdigit():
        return None
    try:
        html = _get(POSTING_URL.format(job_id=job_id))
    except RateLimitedError as e:
        print(f"[linkedin_jobs] {e}", file=sys.stderr)
        return None
    return parse_description(html) if html else None
