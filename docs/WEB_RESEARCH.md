# Web research

`WebProvider` is a replaceable interface for search and page retrieval. The optional built-in DuckDuckGo HTML provider uses the standard library and needs no API key. Set `AEGIS_WEB_PROVIDER=duckduckgo` to register it. Network actions remain disabled unless `AEGIS_ALLOW_NETWORK=1`, `AEGIS_MAX_PERMISSION=NETWORK`, and the `web.search` / `web.fetch` tool IDs are included in `AEGIS_ALLOWED_TOOLS`.

Search returns provider-ranked HTTPS results with title and URL, deduplicated by host. Fetch accepts HTTPS pages only, rejects credentials and non-global resolved addresses, checks redirects, accepts HTML/plain text only, and caps the response at 1 MB and extracted text at 20 KB. Script, style, SVG, and noscript contents are omitted. No code, macro, or page instruction is executed. Results are labeled untrusted and enter CentralBrain as reference data.

The provider does not independently verify factual claims or guarantee ranking quality. DNS is checked before requests, but this application-level check is not a substitute for a network egress firewall. If the provider is unset or policy denies network access, research reports external search as unavailable; it does not fabricate web results. No API key is embedded in the repository.
