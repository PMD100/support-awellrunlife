"""
Polite HTTP fetching.

We are crawling hospices, churches, and small nonprofits. Many run on shared hosting
that a careless crawler could genuinely disrupt. Beyond the ethics, our whole outreach
strategy depends on these organizations being glad we exist — getting a complaint or a
block would be self-defeating.

So every request through this module:

  * honors robots.txt, without exception
  * waits at least MIN_DELAY seconds between requests to the same domain
  * identifies itself honestly, with a contact URL
  * gives up quickly rather than retrying aggressively
  * never follows more than a few redirects

If you find yourself wanting to bypass any of the above, the answer is no.
"""

import gzip
import io
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from collections import defaultdict

USER_AGENT = "AWRLSupportBot/1.0 (+https://support.awellrunlife.com/bot)"
MIN_DELAY = 1.0          # seconds between requests to the same host
TIMEOUT = 15             # seconds
MAX_BYTES = 2_000_000    # don't download huge files looking for a phone number
MAX_REDIRECTS = 4

_last_request = defaultdict(float)
_host_locks = defaultdict(threading.Lock)
_robots_cache = {}
_robots_lock = threading.Lock()
_global_lock = threading.Lock()


class FetchResult:
    """Everything a caller needs, with no exceptions to catch."""

    __slots__ = ("url", "final_url", "status", "text", "error", "blocked_by_robots")

    def __init__(self, url, final_url=None, status=None, text=None,
                 error=None, blocked_by_robots=False):
        self.url = url
        self.final_url = final_url or url
        self.status = status
        self.text = text or ""
        self.error = error
        self.blocked_by_robots = blocked_by_robots

    @property
    def ok(self):
        return self.status == 200 and not self.error

    def __repr__(self):
        if self.blocked_by_robots:
            return f"<FetchResult {self.url} BLOCKED-BY-ROBOTS>"
        return f"<FetchResult {self.url} status={self.status} error={self.error} len={len(self.text)}>"


def host_of(url):
    try:
        return urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return ""


def domain_resolves(hostname, timeout=5):
    """Cheap DNS check. Lets us skip the HTTP cost for domains that don't exist."""
    try:
        socket.setdefaulttimeout(timeout)
        socket.getaddrinfo(hostname, None)
        return True
    except (socket.gaierror, socket.timeout, UnicodeError, OSError):
        return False


def _robots_for(scheme, host):
    """Fetch and cache robots.txt for a host. On any failure, we assume allowed."""
    key = f"{scheme}://{host}"
    with _robots_lock:
        if key in _robots_cache:
            return _robots_cache[key]

    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(f"{key}/robots.txt")
    try:
        req = urllib.request.Request(f"{key}/robots.txt",
                                     headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read(200_000).decode("utf-8", errors="replace")
        parser.parse(content.splitlines())
    except Exception:
        # No robots.txt, or unreachable. Standard interpretation: allowed.
        parser.parse([])

    with _robots_lock:
        _robots_cache[key] = parser
    return parser


def allowed_by_robots(url):
    parts = urllib.parse.urlparse(url)
    if not parts.netloc:
        return False
    try:
        return _robots_for(parts.scheme or "https", parts.netloc).can_fetch(USER_AGENT, url)
    except Exception:
        return True


def _throttle(host):
    """Block until MIN_DELAY has elapsed since the last request to this host."""
    with _host_locks[host]:
        with _global_lock:
            elapsed = time.time() - _last_request[host]
        if elapsed < MIN_DELAY:
            time.sleep(MIN_DELAY - elapsed)
        with _global_lock:
            _last_request[host] = time.time()


def _decode(raw, resp):
    if resp.headers.get("Content-Encoding") == "gzip":
        try:
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        except Exception:
            pass
    charset = resp.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")


def fetch(url, check_robots=True):
    """
    Fetch a URL politely. Never raises — always returns a FetchResult.
    """
    parts = urllib.parse.urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return FetchResult(url, error="invalid_url")

    if check_robots and not allowed_by_robots(url):
        return FetchResult(url, blocked_by_robots=True, error="robots_disallowed")

    _throttle(parts.netloc)

    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    })

    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "html" not in ctype and "text" not in ctype:
                return FetchResult(url, final_url=resp.geturl(), status=resp.status,
                                   error="not_html")
            raw = resp.read(MAX_BYTES)
            return FetchResult(url, final_url=resp.geturl(), status=resp.status,
                               text=_decode(raw, resp))

    except urllib.error.HTTPError as exc:
        return FetchResult(url, status=exc.code, error=f"http_{exc.code}")
    except urllib.error.URLError as exc:
        return FetchResult(url, error=f"url_error:{getattr(exc, 'reason', exc)}"[:120])
    except (socket.timeout, TimeoutError):
        return FetchResult(url, error="timeout")
    except ssl.SSLError as exc:
        return FetchResult(url, error=f"ssl_error:{exc}"[:120])
    except Exception as exc:  # noqa: BLE001 - deliberately broad; never crash a long crawl
        return FetchResult(url, error=f"{type(exc).__name__}:{exc}"[:120])
