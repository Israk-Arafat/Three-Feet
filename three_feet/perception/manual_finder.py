"""Find a heater's owner's manual from its name and read its clearance rule.

Muse Spark searches the web for the manual PDF. We download it and Muse Wonder reads it, exactly like an
uploaded manual. If the site blocks our download, Muse Spark reads the PDF from its URL on Meta's servers
instead. Either way the rule cites a quote and page from the real document, and nothing is enforced until a
person approves it.
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import ssl
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlsplit

import certifi
import httpx
import openai

from ..config import MANUALS_DIR, get_settings
from .meta_client import token_cost
from .wonder import CLEARANCE_SCHEMA, EXTRACT_PROMPT, WonderClient

MAX_PDF_BYTES = 50_000_000  # Meta's inline file limit
MAX_REDIRECTS = 5
MAX_CANDIDATES = 5
MAX_READS = 3  # documents read by a model before giving up; each read costs money
USER_AGENT = "Mozilla/5.0 (compatible; ThreeFeet/0.1; +heater manual lookup)"

SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "identified": {"type": "boolean", "description": "True if you identified a specific heater model"},
        "heater_model": {"type": "string", "description": "Manufacturer and model number"},
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Direct link to the owner's manual PDF"},
                    "title": {"type": "string"},
                    "official": {"type": "boolean", "description": "Hosted by the heater's manufacturer"},
                },
                "required": ["url", "title", "official"],
            },
        },
        "notes": {"type": "string"},
    },
    "required": ["identified", "heater_model", "candidates", "notes"],
}

SEARCH_PROMPT = (
    "Find direct links to the owner's manual PDF for this space heater. Prefer the manufacturer's own "
    "website; otherwise use any copy of the original manual. Use as few searches as you need (usually 1-3). "
    "Return at most 5 links, best first. Only return URLs you saw in search results or opened pages; never "
    "construct or guess a URL. If the name is ambiguous or not a space heater, set identified=false, return "
    "no links, and say in notes what extra detail would help.\n\nHeater: "
)


class ManualNotFound(Exception):
    """No manual could be read. `candidates` lists the links that were tried and why each failed."""

    def __init__(self, message: str, candidates: list[dict] | None = None) -> None:
        super().__init__(message)
        self.candidates = candidates or []


class ManualFetchError(Exception):
    pass


@dataclass
class ManualReading:
    extraction: dict
    url: str
    title: str
    heater_model: str
    read_by: str
    path: Path | None = None  # local copy, when we could download it
    tried: list[dict] = field(default_factory=list)


def clean_heater_name(name: str) -> str:
    return " ".join("".join(ch if ch.isprintable() else " " for ch in name).split())[:120]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "heater"


def _trust() -> ssl.SSLContext:
    # The OS store as well as certifi, so downloads work behind TLS-inspecting networks.
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(certifi.where())
    return ctx


def _require_public_host(url: str, resolve: Callable = socket.getaddrinfo) -> None:
    """Refuse anything but http(s) to public addresses, so a search result can't reach the home network."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ManualFetchError("not an http(s) link")
    try:
        infos = resolve(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80),
                        type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise ManualFetchError(f"could not resolve {parts.hostname}") from exc
    for info in infos:
        ip = ipaddress.ip_address(str(info[4][0]).split("%")[0])
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global:
            raise ManualFetchError(f"{parts.hostname} is not a public address")


def download_pdf(url: str, *, http: httpx.Client | None = None, resolve: Callable = socket.getaddrinfo,
                 max_bytes: int = MAX_PDF_BYTES) -> bytes:
    """Download a PDF, re-checking every redirect hop and capping the size."""
    own = http is None
    http = http or httpx.Client(timeout=30.0, verify=_trust(), headers={"User-Agent": USER_AGENT})
    try:
        for _ in range(MAX_REDIRECTS + 1):
            _require_public_host(url, resolve)
            with http.stream("GET", url, follow_redirects=False) as r:
                if r.is_redirect and "location" in r.headers:
                    url = urljoin(url, r.headers["location"])
                    continue
                if r.status_code != 200:
                    raise ManualFetchError(f"HTTP {r.status_code}")
                if int(r.headers.get("content-length") or 0) > max_bytes:
                    raise ManualFetchError("file is too large")
                data = bytearray()
                for chunk in r.iter_bytes():
                    data.extend(chunk)
                    if len(data) > max_bytes:
                        raise ManualFetchError("file is too large")
                if b"%PDF-" not in bytes(data[:1024]):
                    raise ManualFetchError("not a PDF")
                return bytes(data)
        raise ManualFetchError("too many redirects")
    except httpx.HTTPError as exc:
        raise ManualFetchError(f"download failed ({type(exc).__name__})") from exc
    finally:
        if own:
            http.close()


def _final_text(response: dict) -> str:
    """The last assistant message. Earlier messages are progress narration between searches."""
    for item in reversed(response.get("output") or []):
        if item.get("type") == "message":
            return "".join(b.get("text") or "" for b in item.get("content") or [] if b.get("type") == "output_text")
    return ""


def rank_candidates(candidates: list[dict]) -> list[dict]:
    """The model's order, manufacturer copies first; drops duplicates and non-web links."""
    out: dict[str, dict] = {}
    for c in sorted(candidates, key=lambda c: not c.get("official")):
        url = str(c.get("url", "")).strip()
        if url.startswith(("http://", "https://")) and url not in out:
            out[url] = {"url": url, "title": str(c.get("title", "")), "official": bool(c.get("official"))}
    return list(out.values())[:MAX_CANDIDATES]


class ManualFinder:
    def __init__(self, wonder: WonderClient, model: str | None = None, manuals_dir: Path = MANUALS_DIR,
                 fetch: Callable[[str], bytes] = download_pdf, timeout_s: float = 150.0,
                 search_context_size: str = "medium") -> None:
        self.wonder = wonder
        self.meter = wonder.meter
        self.model = model or get_settings().spark_model
        self.manuals_dir = Path(manuals_dir)
        self.fetch = fetch
        self.timeout_s = timeout_s
        self.search_context_size = search_context_size

    def _respond(self, **kwargs) -> dict:
        response = self.wonder.client.with_options(timeout=self.timeout_s).responses.create(model=self.model, **kwargs)
        if self.meter and response.usage:
            self.meter.add(self.model, token_cost(
                self.model, response.usage.input_tokens, response.usage.output_tokens))
        return response.model_dump()

    def search(self, heater_name: str) -> dict:
        response = self._respond(
            input=SEARCH_PROMPT + json.dumps(heater_name),
            tools=[{"type": "web_search", "search_context_size": self.search_context_size}],
            text={"format": {"type": "json_schema", "name": "ManualSearch", "schema": SEARCH_SCHEMA}},
        )
        try:
            result = json.loads(_final_text(response) or "{}")
        except json.JSONDecodeError as exc:
            raise ManualNotFound("Muse Spark's search answer was not readable. Try again.") from exc
        result["candidates"] = rank_candidates(result.get("candidates") or [])
        return result

    def read_url(self, url: str) -> dict:
        """Meta's servers fetch the PDF, for sites that block direct downloads."""
        response = self._respond(
            input=[{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": EXTRACT_PROMPT},
                {"type": "input_file", "file_url": url},
            ]}],
            text={"format": {"type": "json_schema", "name": "ClearanceExtraction", "schema": CLEARANCE_SCHEMA}},
        )
        return json.loads(_final_text(response) or "{}")

    def _read(self, c: dict, model: str) -> ManualReading:
        try:
            data = self.fetch(c["url"])
        except ManualFetchError as exc:
            try:
                extraction = self.read_url(c["url"])
            except (openai.OpenAIError, json.JSONDecodeError) as api_exc:
                raise ManualFetchError(f"{exc}; Meta couldn't read it either") from api_exc
            return ManualReading(extraction, c["url"], c["title"], model, self.model)
        self.manuals_dir.mkdir(parents=True, exist_ok=True)
        path = self.manuals_dir / f"{_slug(model)}.pdf"
        path.write_bytes(data)
        return ManualReading(self.wonder.extract_clearance(path), c["url"], c["title"], model,
                             self.wonder.model, path)

    def find(self, heater_name: str) -> ManualReading:
        """Search, then read candidates in order until one states a clearance distance."""
        name = clean_heater_name(heater_name)
        result = self.search(name)
        model = str(result.get("heater_model") or name)
        if not result["candidates"]:
            raise ManualNotFound(result.get("notes") or f"No manual found for {name}. Check the model number.")
        tried: list[dict] = []
        first_read: ManualReading | None = None
        reads = 0
        for c in result["candidates"]:
            if reads >= MAX_READS:
                break
            try:
                reading = self._read(c, model)
            except ManualFetchError as exc:
                tried.append({**c, "error": str(exc)})
                continue
            reads += 1
            if reading.extraction.get("found"):
                reading.tried = tried
                return reading
            tried.append({**c, "error": "no clearance distance in this document"})
            first_read = first_read or reading
        if first_read is not None:
            first_read.tried = tried
            return first_read
        raise ManualNotFound(
            f"Found links to the {model} manual but couldn't open any of them. "
            "Open one, save the PDF, and upload it instead.", tried)
