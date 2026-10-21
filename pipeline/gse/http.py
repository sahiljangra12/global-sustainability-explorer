"""Shared HTTP session with retries and polite defaults."""

from __future__ import annotations

import logging
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

USER_AGENT = (
    "global-sustainability-explorer/0.1 "
    "(+https://github.com/sahiljangra12/global-sustainability-explorer)"
)


def session(retries: int = 6, backoff: float = 2) -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=backoff,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "POST"),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers["User-Agent"] = USER_AGENT
    return s


def get_json(s: requests.Session, url: str, params: dict | None = None, timeout: int = 120):
    """GET a URL and decode JSON, with one extra retry on decode errors.

    Some APIs occasionally return an HTML error page with status 200.
    """
    for attempt in range(3):
        r = s.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        try:
            return r.json()
        except ValueError:
            log.warning("Non-JSON response from %s (attempt %d)", r.url, attempt + 1)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"Could not decode JSON from {url}")
