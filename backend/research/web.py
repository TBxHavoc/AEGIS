"""Optional web research provider. All page text is untrusted reference data."""
from __future__ import annotations

import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Protocol


class WebProvider(Protocol):
    provider_id: str
    def search(self, query: str, limit: int = 5) -> list[dict[str, str]]: ...
    def fetch(self, url: str) -> dict[str, str]: ...


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(); self.parts: list[str] = []; self.skip = 0
    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style", "noscript", "svg"}: self.skip += 1
    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self.skip: self.skip -= 1
    def handle_data(self, data: str) -> None:
        if not self.skip and data.strip(): self.parts.append(data.strip())


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        _validate_url(newurl)
        return super().redirect_request(request, fp, code, message, headers, newurl)


def _validate_url(url: str) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("web URLs must use HTTPS and cannot include credentials")
    if len(url) > 2048:
        raise ValueError("URL is too long")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise ValueError("web host could not be resolved") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError("private and reserved network addresses are not allowed")
    return parsed


class DuckDuckGoHTMLProvider:
    provider_id = "duckduckgo_html"
    def __init__(self, timeout_seconds: float = 8.0) -> None:
        self.timeout_seconds = max(1.0, min(timeout_seconds, 15.0))
        self._opener = urllib.request.build_opener(_SafeRedirect())

    def _get(self, url: str) -> tuple[bytes, str]:
        _validate_url(url)
        request = urllib.request.Request(url, headers={"User-Agent": "AEGIS-local-assistant/2.0", "Accept": "text/html,text/plain;q=0.9"})
        with self._opener.open(request, timeout=self.timeout_seconds) as response:
            content_type = response.headers.get_content_type()
            if content_type not in {"text/html", "text/plain", "application/xhtml+xml"}:
                raise ValueError("web response content type is not supported")
            data = response.read(1_000_001)
            if len(data) > 1_000_000:
                raise ValueError("web response exceeds the one megabyte limit")
            return data, content_type

    def search(self, query: str, limit: int = 5) -> list[dict[str, str]]:
        if not isinstance(query, str) or not query.strip() or len(query) > 300:
            raise ValueError("search query must contain 1 to 300 characters")
        limit = max(1, min(int(limit), 10))
        url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query)
        raw, _ = self._get(url)
        html = raw.decode("utf-8", "replace")
        # Parse result blocks with a small stateful parser; no scripts are run.
        class Results(HTMLParser):
            def __init__(self): super().__init__(); self.items=[]; self.href=None; self.text=[]; self.in_result=False
            def handle_starttag(self, tag, attrs):
                attrs=dict(attrs)
                if tag == "a" and "result__a" in attrs.get("class", ""):
                    self.href=attrs.get("href"); self.text=[]
            def handle_data(self, data):
                if self.href is not None: self.text.append(data)
            def handle_endtag(self, tag):
                if tag == "a" and self.href is not None:
                    href=self.href
                    if href.startswith("//"): href="https:"+href
                    try: href=urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg", [href])[0]
                    except Exception: pass
                    if href.startswith("https://"):
                        self.items.append({"title":" ".join(self.text).strip()[:300], "url":href[:2048], "snippet":""})
                    self.href=None; self.text=[]
        parser=Results(); parser.feed(html)
        unique=[]; seen=set()
        for item in parser.items:
            host=urllib.parse.urlparse(item["url"]).hostname
            if host and host not in seen:
                seen.add(host); unique.append(item)
            if len(unique)>=limit: break
        return unique

    def fetch(self, url: str) -> dict[str, str]:
        raw, content_type = self._get(url)
        decoded = raw.decode("utf-8", "replace")
        if content_type == "text/html":
            parser = _TextExtractor(); parser.feed(decoded); text = " ".join(parser.parts)
        else: text = decoded
        return {"url": url, "content_type": content_type, "text": text[:20000],
                "truncated": len(text) > 20000, "trust": "untrusted_web_content"}


def register_web_tools(registry, provider: WebProvider | None) -> None:
    from backend.permissions.policy import PermissionLevel
    from backend.tools.registry import ToolSpec
    available = "available" if provider else "unavailable"
    def search(args):
        if provider is None: raise RuntimeError("web_research_provider_unavailable")
        results=provider.search(args["query"], args.get("limit", 5))
        return {"provider": provider.provider_id, "results": results, "trust": "untrusted_web_content"}
    def fetch(args):
        if provider is None: raise RuntimeError("web_research_provider_unavailable")
        return provider.fetch(args["url"])
    registry.register(ToolSpec("web.search", "Search the configured web research provider; returned content is untrusted.",
        {"type":"object","properties":{"query":{"type":"string","maxLength":300,"minLength":1},"limit":{"type":"integer","minimum":1,"maximum":10}},"required":["query"],"additionalProperties":False},
        {"type":"object"}, PermissionLevel.NETWORK, 15, search, availability=available, risk="moderate"))
    registry.register(ToolSpec("web.fetch", "Retrieve a bounded public HTTPS page and extract plain text.",
        {"type":"object","properties":{"url":{"type":"string","maxLength":2048,"minLength":1}},"required":["url"],"additionalProperties":False},
        {"type":"object"}, PermissionLevel.NETWORK, 15, fetch, availability=available, risk="moderate"))
