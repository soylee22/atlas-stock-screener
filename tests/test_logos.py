import io
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as screener


@pytest.mark.parametrize("website,expected", [
    ("https://www.nvidia.com/en-us/", "nvidia.com"),
    ("apple.com", "apple.com"),
    ("https://127.0.0.1:1234", None),
    ("http://localhost", None),
    ("https://internal.local", None),
    (None, None),
])
def test_company_domain_normalises_public_hosts(website, expected):
    assert screener.company_domain(website) == expected


def icon_bytes():
    out = io.BytesIO()
    Image.new("RGBA", (16, 16), (0, 128, 0, 255)).save(out, format="PNG")
    return out.getvalue()


class IconResponse:
    url = "https://example-provider.test/cached-icon.png"

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def raise_for_status(self):
        pass

    def iter_content(self, size):
        yield icon_bytes()


def test_real_icon_bytes_are_stored_and_reused_across_listings(tmp_path, monkeypatch):
    cache = screener.LogoCache(screener.Store(tmp_path / "cache.sqlite"))
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return IconResponse()

    monkeypatch.setattr(screener.requests, "get", get)
    cache.fetch("nvidia.com")
    main = cache.decorate(dict(symbol="NVDA", website="https://www.nvidia.com"))
    secondary = cache.decorate(dict(symbol="NVDA.NE", website="https://nvidia.com/investors"))
    assert main["logo_url"] == secondary["logo_url"]
    assert main["logo_url"].startswith("/logos/") and main["logo_url"].endswith(".png")
    assert len(list(cache.root.iterdir())) == 1
    assert (cache.root / main["logo_url"].split("/")[-1]).read_bytes() == icon_bytes()
    assert cache.queue.empty()
    assert len(calls) == 1 and calls[0][1]["params"]["domain"] == "nvidia.com"
    assert cache.status()["cached"] == 1


def test_missing_or_deleted_icon_keeps_fallback_and_queues_repair(tmp_path, monkeypatch):
    cache = screener.LogoCache(screener.Store(tmp_path / "cache.sqlite"))
    assert cache.decorate(dict(symbol="UNKNOWN", website=None))["logo_url"] is None
    assert cache.queue.empty()
    monkeypatch.setattr(screener.requests, "get", lambda *a, **k: IconResponse())
    cache.fetch("nvidia.com")
    next(cache.root.iterdir()).unlink()
    assert cache.decorate(dict(symbol="NVDA", website="https://nvidia.com"))["logo_url"] is None
    assert not cache.queue.empty()


@pytest.mark.parametrize("content", [b"", b"<html>not an icon</html>", b'<svg xmlns="http://www.w3.org/2000/svg"><script>bad()</script></svg>', b"x" * 1_000_001])
def test_invalid_and_unsafe_icon_responses_are_rejected(content):
    with pytest.raises((ValueError, OSError)):
        screener.icon_extension(content)


def test_failed_provider_response_does_not_create_a_fake_logo(tmp_path, monkeypatch):
    cache = screener.LogoCache(screener.Store(tmp_path / "cache.sqlite"))

    class MissingResponse(IconResponse):
        def raise_for_status(self):
            raise screener.requests.HTTPError("404 missing icon")

    monkeypatch.setattr(screener.requests, "get", lambda *a, **k: MissingResponse())
    with pytest.raises(screener.requests.HTTPError):
        cache.fetch("missing.example.com")
    assert not list(cache.root.iterdir())
    assert cache.asset("missing.example.com") is None
