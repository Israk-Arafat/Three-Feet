import json
import socket
from types import SimpleNamespace

import httpx
import openai
import pytest

from three_feet.perception.manual_finder import (
    MAX_READS,
    ManualFetchError,
    ManualFinder,
    ManualNotFound,
    download_pdf,
    rank_candidates,
)

PDF = b"%PDF-1.7\n...\n%%EOF"


def resolver(table):
    def resolve(host, port, type=None):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (table[host], port))]
    return resolve


PUBLIC = resolver({"maker.example": "93.184.216.34", "cdn.example": "93.184.216.35", "evil.example": "192.168.1.63"})


def http_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_download_follows_public_redirects_and_returns_pdf():
    def handler(request):
        if request.url.host == "maker.example":
            return httpx.Response(302, headers={"location": "https://cdn.example/m.pdf"})
        return httpx.Response(200, content=PDF)

    assert download_pdf("https://maker.example/manual", http=http_client(handler), resolve=PUBLIC) == PDF


@pytest.mark.parametrize("url", ["http://127.0.0.1/m.pdf", "http://10.0.0.5/m.pdf", "http://[::1]/m.pdf",
                                 "file:///etc/passwd", "ftp://maker.example/m.pdf"])
def test_download_refuses_non_public_targets(url):
    with pytest.raises(ManualFetchError):
        download_pdf(url, http=http_client(lambda r: httpx.Response(200, content=PDF)))


def test_download_refuses_redirect_into_home_network():
    seen = []

    def handler(request):
        seen.append(request.url.host)
        return httpx.Response(302, headers={"location": "http://evil.example/plug"})

    with pytest.raises(ManualFetchError, match="not a public address"):
        download_pdf("https://maker.example/m.pdf", http=http_client(handler), resolve=PUBLIC)
    assert seen == ["maker.example"]


@pytest.mark.parametrize("response,error", [
    (httpx.Response(200, content=b"<html>login</html>"), "not a PDF"),
    (httpx.Response(403), "HTTP 403"),
    (httpx.Response(200, content=PDF + b"x" * 100), "too large"),
])
def test_download_rejects_bad_responses(response, error):
    with pytest.raises(ManualFetchError, match=error):
        download_pdf("https://maker.example/m.pdf", http=http_client(lambda r: response), resolve=PUBLIC,
                     max_bytes=50)



def test_rank_puts_manufacturer_links_first_and_drops_bad_links():
    ranked = rank_candidates([
        {"url": "https://shop.example/m.pdf", "title": "shop", "official": False},
        {"url": "https://maker.example/m.pdf", "title": "maker", "official": True},
        {"url": "https://shop.example/m.pdf", "title": "dupe", "official": False},
        {"url": "javascript:alert(1)", "title": "bad", "official": True},
        {"url": "https://mirror.example/m.pdf", "title": "mirror", "official": False},
    ])
    assert [c["url"] for c in ranked] == ["https://maker.example/m.pdf", "https://shop.example/m.pdf",
                                          "https://mirror.example/m.pdf"]


def message(text):
    return {"type": "message", "content": [{"type": "output_text", "text": text}]}


class FakeResponses:
    """Muse Spark on the Responses API: web search when tools are given, otherwise reading a PDF by URL."""

    def __init__(self, search_answer, url_readings):
        self.search_answer, self.url_readings, self.calls = search_answer, url_readings, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("tools"):
            output = [message("I'll search the web."), {"type": "web_search_call", "status": "completed"},
                      message(json.dumps(self.search_answer))]
        else:
            url = kwargs["input"][0]["content"][1]["file_url"]
            reading = self.url_readings.get(url)
            if reading is None:
                raise openai.APIConnectionError(request=httpx.Request("POST", "https://api.example"))
            output = [message(json.dumps(reading))]
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=1000, output_tokens=100),
                               model_dump=lambda: {"output": output})


class FakeClient:
    def __init__(self, responses):
        self.responses = responses

    def with_options(self, **kwargs):
        return self


class FakeWonder:
    model = "muse-wonder-1.0"
    meter = None

    def __init__(self, responses, readings):
        self.client, self.readings, self.read = FakeClient(responses), readings, []

    def extract_clearance(self, path):
        self.read.append(path)
        return self.readings.pop(0)


FOUND = {"found": True, "front_in": 36, "back_in": 12, "sides_in": 12, "quote": "3 feet", "page": 2}
MISSING = {"found": False, "quote": "", "page": None}


def finder_for(tmp_path, candidates, fetch, wonder_readings=(), url_readings=None, heater_model="Lasko 754200"):
    answer = {"identified": bool(candidates), "heater_model": heater_model, "notes": "Which brand?",
              "candidates": [{"url": u, "title": u[-5:], "official": True} for u in candidates]}
    responses = FakeResponses(answer, url_readings or {})
    wonder = FakeWonder(responses, list(wonder_readings))
    return ManualFinder(wonder, model="muse-spark-1.3", manuals_dir=tmp_path, fetch=fetch), responses, wonder


def test_find_searches_the_web_then_wonder_reads_the_downloaded_pdf(tmp_path):
    finder, responses, wonder = finder_for(tmp_path, ["https://maker.example/a.pdf"], lambda url: PDF, [FOUND])
    reading = finder.find("Lasko\n754200")
    search = responses.calls[0]
    assert search["tools"][0]["type"] == "web_search" and '"Lasko 754200"' in search["input"]
    assert search["text"]["format"]["type"] == "json_schema"
    assert reading.extraction["page"] == 2 and reading.read_by == "muse-wonder-1.0"
    assert reading.path == tmp_path / "lasko-754200.pdf" and reading.path.read_bytes() == PDF
    assert wonder.read == [reading.path] and len(responses.calls) == 1


def test_find_lets_meta_read_the_url_when_the_site_blocks_downloads(tmp_path):
    def fetch(url):
        raise ManualFetchError("HTTP 403")

    finder, responses, wonder = finder_for(
        tmp_path, ["https://blocked.example/a.pdf"], fetch, url_readings={"https://blocked.example/a.pdf": FOUND})
    reading = finder.find("Lasko 754200")
    assert reading.read_by == "muse-spark-1.3" and reading.path is None and reading.extraction["found"]
    assert responses.calls[1]["input"][0]["content"][1] == {"type": "input_file",
                                                            "file_url": "https://blocked.example/a.pdf"}
    assert wonder.read == [] and not list(tmp_path.iterdir())


def test_find_skips_documents_without_a_clearance_distance(tmp_path):
    finder, _, _ = finder_for(tmp_path, ["https://x.example/brochure.pdf", "https://x.example/manual.pdf"],
                              lambda url: PDF, [MISSING, FOUND])
    reading = finder.find("Lasko 754200")
    assert reading.url == "https://x.example/manual.pdf" and reading.extraction["found"]
    assert reading.tried[0]["error"] == "no clearance distance in this document"


def test_find_returns_a_not_found_reading_rather_than_nothing(tmp_path):
    finder, _, _ = finder_for(tmp_path, ["https://x.example/a.pdf"], lambda url: PDF, [MISSING])
    reading = finder.find("Lasko 754200")
    assert reading.extraction["found"] is False and reading.url == "https://x.example/a.pdf"


def test_find_caps_how_many_documents_it_reads(tmp_path):
    urls = [f"https://x.example/{i}.pdf" for i in range(5)]
    finder, _, wonder = finder_for(tmp_path, urls, lambda url: PDF, [MISSING] * 5)
    finder.find("Lasko 754200")
    assert len(wonder.read) == MAX_READS


def test_find_explains_when_nothing_is_found(tmp_path):
    finder, _, _ = finder_for(tmp_path, [], lambda url: PDF, heater_model="")
    with pytest.raises(ManualNotFound, match="Which brand?"):
        finder.find("heater")


def test_find_lists_links_when_none_can_be_opened(tmp_path):
    def fetch(url):
        raise ManualFetchError("HTTP 403")

    finder, _, _ = finder_for(tmp_path, ["https://maker.example/a.pdf"], fetch)
    with pytest.raises(ManualNotFound) as exc:
        finder.find("Vornado VH200")
    assert exc.value.candidates[0]["error"] == "HTTP 403; Meta couldn't read it either"
