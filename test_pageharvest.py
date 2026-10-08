"""Offline integration tests using a real local HTTP server."""
from collections import Counter
import contextlib
import csv
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch

from pageharvest import Field, Scraper, export_csv, export_json, profile, scrape, ua
from pageharvest.cli import main
from pageharvest.extract import extract_html


class Handler(BaseHTTPRequestHandler):
    hits = Counter()

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.hits[self.path] += 1
        status, content_type, extra = 200, "text/html; charset=utf-8", {}
        body = "<html><title>Example</title><main><h1>Useful page</h1><p>Hello world.</p></main></html>"
        if self.path == "/robots.txt":
            content_type = "text/plain"
            body = "User-agent: *\nDisallow: /private\n"
        elif self.path == "/":
            body = '''<html><head><title>Catalog</title><meta name="description" content="A catalog">
                <script type="application/ld+json">{"@type":"Product","name":"Book"}</script>
                </head><body><nav>Navigation noise</nav><main><h1>Catalog</h1>
                <span class="price">$19.95</span><a href="/page2#first" rel="next">Next</a>
                <a href="/page2#second">Duplicate</a><a href="https://other.invalid/">External</a>
                <a href="mailto:test@example.com">Email</a></main><footer>Footer noise</footer></body></html>'''
        elif self.path == "/page2":
            body = '<main><h1>Second</h1><a href="/">Home</a><a href="/page3">Third</a></main>'
        elif self.path == "/retry":
            status = 503 if self.hits[self.path] == 1 else 200
            extra = {"Retry-After": "0"}
        elif self.path == "/slowdown":
            status, extra = 429, {"Retry-After": "9999"}
        elif self.path == "/denied":
            status = 403
        elif self.path == "/redirect":
            status, extra = 302, {"Location": "/page2"}
        elif self.path == "/offsite":
            status, extra = 302, {"Location": "http://offsite.invalid/"}
        elif self.path == "/auth-redirect":
            status, extra = 302, {"Location": f"http://localhost:{self.server.server_port}/auth-check"}
        elif self.path == "/auth-check":
            content_type = "application/json"
            body = json.dumps({"authorization": self.headers.get("Authorization"),
                               "cookie": self.headers.get("Cookie")})
        elif self.path == "/loop":
            status, extra = 302, {"Location": "/loop"}
        elif self.path == "/bad-redirect":
            status, extra = 302, {"Location": "file:///etc/passwd"}
        elif self.path == "/cookie-set":
            extra = {"Set-Cookie": "sample=yes; Path=/"}
        elif self.path == "/cookie-check":
            body = "<main>" + self.headers.get("Cookie", "missing") + "</main>"
        elif self.path == "/json":
            content_type, body = "application/json", '{"items":[{"name":"Book"}]}'
        elif self.path == "/bad-json":
            content_type, body = "application/json", "not json"
        elif self.path == "/binary":
            content_type, body = "application/octet-stream", "binary"
        elif self.path == "/huge":
            body = "x" * 10000
        elif self.path == "/js":
            body = '''<html><title>Dynamic</title><body><div id="root"></div>
                <script>setTimeout(() => { document.getElementById('root').innerHTML =
                '<h1 id="loaded">Rendered content</h1>'; }, 100);</script></body></html>'''
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        for key, value in extra.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(encoded)


class ScrapingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        Handler.hits.clear()

    def test_html_extraction_and_custom_fields(self):
        result = scrape(self.base, delay=0, fields={"price": ".price",
            "links": Field("a", attr="href", many=True), "heading": Field("h1", required=True)})
        self.assertTrue(result.ok)
        self.assertEqual(result.title, "Catalog")
        self.assertEqual(result.fields["price"], "$19.95")
        self.assertNotIn("Navigation noise", result.text)
        self.assertNotIn("Footer noise", result.text)
        self.assertEqual(result.metadata["description"], "A catalog")
        self.assertEqual(result.structured_data[0]["@type"], "Product")
        self.assertEqual(result.links, [self.base + "/page2", "https://other.invalid/"])
        self.assertEqual(result.next_url, self.base + "/page2")

    def test_required_fields_are_reported(self):
        result = scrape(self.base, delay=0, fields={"missing": Field(".absent", required=True)})
        self.assertFalse(result.ok)
        self.assertEqual(result.missing_fields, ["missing"])
        self.assertIsNone(result.fields["missing"])

    def test_robots_prevents_request(self):
        result = scrape(self.base + "/private", delay=0)
        self.assertEqual(result.error, "robots_disallowed_or_unavailable")
        self.assertEqual(Handler.hits["/private"], 0)

    def test_explicit_robots_override(self):
        self.assertTrue(scrape(self.base + "/private", respect_robots=False, delay=0).ok)
        self.assertEqual(Handler.hits["/robots.txt"], 0)

    def test_retry_and_retry_after(self):
        result = scrape(self.base + "/retry", delay=0)
        self.assertTrue(result.ok)
        self.assertEqual(result.attempts, 2)
        result = scrape(self.base + "/slowdown", delay=0)
        self.assertEqual(result.error, "retry_after_exceeds_limit")
        self.assertEqual(Handler.hits["/slowdown"], 1)

    def test_403_is_not_retried(self):
        result = scrape(self.base + "/denied", delay=0)
        self.assertEqual(result.error, "http_403")
        self.assertEqual(result.attempts, 1)

    def test_redirects_and_limits(self):
        result = scrape(self.base + "/redirect", delay=0)
        self.assertTrue(result.ok)
        self.assertEqual(result.url, self.base + "/page2")
        self.assertEqual(scrape(self.base + "/loop", delay=0).error, "too_many_redirects")
        self.assertEqual(scrape(self.base + "/bad-redirect", delay=0).error, "invalid_redirect")

    def test_crawl_stays_on_origin_and_deduplicates(self):
        with Scraper(delay=0) as scraper:
            results = list(scraper.crawl(self.base, max_pages=3))
        self.assertEqual([r.url for r in results], [self.base + "/", self.base + "/page2", self.base + "/page3"])
        self.assertEqual(Handler.hits["/page2"], 1)
        self.assertEqual(Handler.hits["/robots.txt"], 1)

    def test_crawl_blocks_off_origin_redirect_before_request(self):
        with Scraper(delay=0) as scraper:
            result = list(scraper.crawl(self.base + "/offsite"))[0]
        self.assertEqual(result.error, "off_origin_redirect")

    def test_cross_origin_redirect_strips_session_credentials(self):
        with Scraper(delay=0, respect_robots=False) as scraper:
            scraper.session.auth = ("test-user", "test-password")
            scraper.session.headers["Cookie"] = "manual=test"
            first = scraper.scrape(self.base + "/auth-check")
            self.assertTrue(first.data["authorization"])
            redirected = scraper.scrape(self.base + "/auth-redirect")
            self.assertEqual(redirected.data, {"authorization": None, "cookie": None})

    def test_pagination_and_depth_limits(self):
        with Scraper(delay=0) as scraper:
            self.assertEqual(len(list(scraper.crawl(self.base, pagination_only=True))), 2)
            self.assertEqual(len(list(scraper.crawl(self.base, max_depth=0))), 1)
            self.assertEqual(len(list(scraper.crawl(self.base, max_pages=1))), 1)

    def test_session_cookies_and_closed_lifecycle(self):
        with Scraper(delay=0) as scraper:
            scraper.scrape(self.base + "/cookie-set")
            self.assertIn("sample=yes", scraper.scrape(self.base + "/cookie-check").text)
        with self.assertRaises(RuntimeError):
            scraper.scrape(self.base)

    def test_json_and_unsupported_data(self):
        result = scrape(self.base + "/json", delay=0)
        self.assertTrue(result.ok)
        self.assertEqual(result.data, {"items": [{"name": "Book"}]})
        self.assertEqual(scrape(self.base + "/bad-json", delay=0).error, "invalid_json")
        self.assertEqual(scrape(self.base + "/binary", delay=0).error, "unsupported_content_type")

    def test_response_size_is_bounded(self):
        self.assertEqual(scrape(self.base + "/huge", max_bytes=100, delay=0).error, "response_too_large")

    def test_javascript_diagnostic_without_browser(self):
        result = scrape(self.base + "/js", delay=0)
        self.assertTrue(result.needs_render)
        self.assertFalse(result.rendered)

    def test_exports_and_formula_escaping(self):
        result = scrape(self.base, delay=0, fields={"price": ".price"})
        result.fields["formula"] = "=1+1"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            export_json([result], path / "data.json")
            self.assertTrue(json.loads((path / "data.json").read_text())[0]["ok"])
            export_csv([result], path / "data.csv")
            with (path / "data.csv").open(newline="") as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual(row["field:formula"], "'=1+1")
            self.assertEqual(row["field:price"], "$19.95")

    def test_cli_json_and_failure_exit_codes(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(main([self.base, "--delay", "0", "--field", "price=.price"]), 0)
        self.assertEqual(json.loads(stdout.getvalue())[0]["fields"]["price"], "$19.95")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main([self.base + "/denied", "--delay", "0"]), 1)
            self.assertEqual(main([self.base, "--field", "bad=["]), 2)

    def test_invalid_input_rejected_before_fetch(self):
        with Scraper(delay=0) as scraper:
            for url in ("file:///tmp/a", "http://user:password@localhost/", "ftp://example.com", "http://bad:port"):
                with self.assertRaises(ValueError):
                    scraper.scrape(url)
            with self.assertRaises(ValueError):
                scraper.scrape(self.base, fields={"bad": "["})
        self.assertEqual(sum(Handler.hits.values()), 0)

    @unittest.skipUnless(os.environ.get("PAGEHARVEST_TEST_RENDER") == "1", "Enable real browser integration explicitly")
    def test_real_browser_rendering(self):
        with Scraper(delay=0, render="auto", timeout=10) as scraper:
            result = scraper.scrape(self.base + "/js", fields={"heading": Field("#loaded", required=True)},
                                    wait_for="#loaded")
        self.assertTrue(result.ok, result.to_dict())
        self.assertTrue(result.rendered)
        self.assertEqual(result.fields["heading"], "Rendered content")


class ProfileTests(unittest.TestCase):
    def test_browser_tokens_and_headers(self):
        for browser, token, version in (("chrome", "Chrome/130.0.0.0", "130.0.1000.1"),
                                        ("edge", "Edg/130.0.1000.1", "130.0.1000.1"),
                                        ("firefox", "Firefox/130.0", "130.0.1"),
                                        ("safari", "Version/18.6", "18.6")):
            os_type = "mac" if browser == "safari" else "windows"
            result = profile(os_type, browser=browser, version=version)
            self.assertIn(token, result.user_agent)
            self.assertEqual(ua(os_type, browser=browser, version=version), result.user_agent)
            hints = result.headers(client_hints=True)
            if browser in ("safari", "firefox"):
                self.assertEqual(len(hints), 1)
            elif browser == "edge":
                self.assertIn("Microsoft Edge", hints["Sec-CH-UA"])
                self.assertNotIn("Google Chrome", hints["Sec-CH-UA"])

    def test_mobile_profiles(self):
        self.assertIn("Android 15; Mobile; rv:", ua("android", browser="firefox", offline=True))
        self.assertIn("Mobile/15E148 Safari/604.1", ua("ios", browser="safari", offline=True))
        self.assertIn("CriOS/", ua("ios", offline=True))

    def test_unsupported_pairs_and_bad_versions(self):
        for options in ({"browser": "safari", "os_type": "windows"}, {"os_type": "ios", "browser": "firefox"},
                        {"browser": "bad"}, {"browser": "edge", "version": "130.0"},
                        {"browser": "firefox", "version": "bad\n"}):
            with self.assertRaises(ValueError):
                profile(**options)

    def test_malformed_json_ld_does_not_lose_page(self):
        result = extract_html('<main>Hello</main><script type="application/ld+json">invalid</script>',
                              "https://example.com/", {})
        self.assertEqual(result["text"], "Hello")
        self.assertTrue(result["warnings"])

    def test_bad_links_do_not_crash_extraction(self):
        result = extract_html('<base href="http://["><a href="http://[">Bad</a>'
                              '<a href="/good">Good</a><main>Text</main>',
                              "https://example.com/", {})
        self.assertEqual(result["links"], ["https://example.com/good"])


if __name__ == "__main__":
    unittest.main()
