"""A deliberately polite HTTP client.

These are public-health websites run by small state teams. The collector
identifies itself, honors robots.txt, spaces out requests to the same host,
and backs off on errors.
"""
from __future__ import annotations

import logging
import time
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)

USER_AGENT = ("CoastWatch/0.1 (mid-Atlantic beach water quality aggregator; "
              "contact: CHANGE-ME@example.com)")


class RobotsDisallowed(Exception):
    pass


class ClientError(Exception):
    """4xx response: the request itself is wrong (e.g. page moved); retrying won't help."""
    def __init__(self, url: str, status: int):
        super().__init__(f"HTTP {status} for {url}")
        self.url, self.status = url, status


@dataclass
class Response:
    url: str
    status: int
    content_type: str
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self):
        import json
        return json.loads(self.body)


class PoliteClient:
    def __init__(self, min_interval_s: float = 2.0, timeout_s: float = 30.0,
                 retries: int = 3, respect_robots: bool = True):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.min_interval_s = min_interval_s
        self.timeout_s = timeout_s
        self.retries = retries
        self.respect_robots = respect_robots
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def _allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        p = urlparse(url)
        root = f"{p.scheme}://{p.netloc}"
        if root not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self.session.get(root + "/robots.txt", timeout=self.timeout_s)
                if r.status_code == 200:
                    rp.parse(r.text.splitlines())
                    self._robots[root] = rp
                else:
                    self._robots[root] = None  # no robots.txt => allowed
            except requests.RequestException:
                self._robots[root] = None
        rp = self._robots[root]
        return True if rp is None else rp.can_fetch(USER_AGENT, url)

    def _throttle(self, host: str) -> None:
        wait = self.min_interval_s - (time.monotonic() - self._last_hit.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        self._last_hit[host] = time.monotonic()

    def get(self, url: str, params: dict | None = None) -> Response:
        if not self._allowed(url):
            raise RobotsDisallowed(url)
        host = urlparse(url).netloc
        last_err: Exception | None = None
        for attempt in range(1, self.retries + 1):
            self._throttle(host)
            try:
                r = self.session.get(url, params=params, timeout=self.timeout_s)
                if r.status_code >= 500 or r.status_code == 429:
                    raise requests.HTTPError(f"HTTP {r.status_code}")
                if r.status_code >= 400:
                    raise ClientError(r.url, r.status_code)  # not retried
                return Response(r.url, r.status_code,
                                r.headers.get("Content-Type", ""), r.content)
            except requests.RequestException as e:
                last_err = e
                log.warning("GET %s failed (attempt %d/%d): %s", url, attempt, self.retries, e)
                time.sleep(2 ** attempt)
        raise last_err  # type: ignore[misc]
