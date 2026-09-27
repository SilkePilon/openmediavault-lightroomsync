"""Client for the Adobe Lightroom API (https://lr.adobe.io).

Reference: https://developer.adobe.com/lightroom/lightroom-api-docs/
"""

import json
import logging
import re
import time
import urllib.parse
from typing import Iterator, List, Optional

from .auth import TokenStore
from .http import Response, Transport

API_BASE = "https://lr.adobe.io/v2/"
# Every JSON response is prefixed with this to prevent JSON hijacking.
_JSON_PREFIX = re.compile(rb"^\s*while\s*\(\s*1\s*\)\s*\{\s*\}\s*")
PAGE_LIMIT = 500
MAX_ATTEMPTS = 5

log = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def parse_json(body: bytes):
    return json.loads(_JSON_PREFIX.sub(b"", body, count=1) or b"{}")


class LightroomClient:
    def __init__(self, tokens: TokenStore, transport: Optional[Transport] = None,
                 sleep=time.sleep):
        self.tokens = tokens
        self.transport = transport or Transport()
        self.sleep = sleep

    def _request(self, method: str, url: str, headers: Optional[dict] = None,
                 ok=(200,)) -> Response:
        url = urllib.parse.urljoin(API_BASE, url)
        refreshed = False
        for attempt in range(1, MAX_ATTEMPTS + 1):
            hdrs = {
                "X-API-Key": self.tokens.client_id,
                "Authorization": "Bearer " + self.tokens.access_token(),
            }
            hdrs.update(headers or {})
            resp = self.transport.request(method, url, hdrs)
            if resp.status in ok:
                return resp
            if resp.status == 401 and not refreshed:
                self.tokens.access_token(force_refresh=True)
                refreshed = True
                continue
            if resp.status in (429, 500, 502, 503, 504) and attempt < MAX_ATTEMPTS:
                delay = min(2 ** attempt, 60)
                try:
                    delay = max(delay, int(resp.headers.get("retry-after", 0)))
                except ValueError:
                    pass
                log.debug("HTTP %d for %s, retrying in %ss", resp.status, url, delay)
                self.sleep(delay)
                continue
            return resp
        return resp

    def _get_json(self, url: str) -> dict:
        resp = self._request("GET", url)
        if resp.status != 200:
            raise ApiError(f"GET {url} failed with HTTP {resp.status}: "
                           f"{resp.body[:200].decode(errors='replace')}", resp.status)
        return parse_json(resp.body)

    def _paginate(self, url: str) -> Iterator[dict]:
        while url:
            page = self._get_json(url)
            yield from page.get("resources", [])
            nxt = page.get("links", {}).get("next", {}).get("href")
            # `next` is relative to the page's `base` (RFC 3986 resolution).
            url = urllib.parse.urljoin(page.get("base", API_BASE), nxt) if nxt else None

    # --- Account / catalog ---------------------------------------------

    def account(self) -> dict:
        return self._get_json("account")

    def catalog_id(self) -> str:
        return self._get_json("catalog")["id"]

    # --- Listing ---------------------------------------------------------

    def iter_assets(self, catalog_id: str, hide_stacked: bool = False) -> Iterator[dict]:
        params = {"subtype": "image", "limit": PAGE_LIMIT}
        if hide_stacked:
            params["hide_stacked_assets"] = "true"
        yield from self._paginate(
            f"catalogs/{catalog_id}/assets?" + urllib.parse.urlencode(params))

    def albums(self, catalog_id: str) -> List[dict]:
        params = {"subtype": "collection_set;collection", "limit": PAGE_LIMIT}
        return list(self._paginate(
            f"catalogs/{catalog_id}/albums?" + urllib.parse.urlencode(params)))

    def iter_album_assets(self, catalog_id: str, album_id: str) -> Iterator[dict]:
        params = {"subtype": "image", "embed": "asset", "limit": PAGE_LIMIT}
        for res in self._paginate(f"catalogs/{catalog_id}/albums/{album_id}/assets?"
                                  + urllib.parse.urlencode(params)):
            # With embed=asset the album-asset resource wraps the asset.
            asset = res.get("asset") or res
            if asset.get("subtype", "image") == "image":
                yield asset

    # --- Renditions --------------------------------------------------------

    def _rendition_url(self, catalog_id, asset_id, rtype=None):
        url = f"catalogs/{catalog_id}/assets/{asset_id}/renditions"
        return f"{url}/{rtype}" if rtype else url

    def download_rendition(self, catalog_id: str, asset_id: str,
                           rtype: str) -> Optional[bytes]:
        """Return the JPEG bytes, or None if the rendition does not exist."""
        url = self._rendition_url(catalog_id, asset_id, rtype)
        resp = self._request("GET", url, ok=(200, 404))
        if resp.status == 404:
            return None
        if resp.status != 200:
            raise ApiError(f"Downloading rendition {rtype} of {asset_id} failed "
                           f"with HTTP {resp.status}", resp.status)
        return resp.body

    def generate_renditions(self, catalog_id: str, asset_id: str,
                            rtypes: List[str]) -> int:
        """Ask the server to render `fullsize`/`2560`. Returns the HTTP status;
        202 means accepted, 403 usually means the API key lacks access."""
        resp = self._request(
            "POST", self._rendition_url(catalog_id, asset_id),
            {"X-Generate-Renditions": ",".join(rtypes)},
            ok=(200, 201, 202, 403, 404))
        return resp.status

    def rendition_ready(self, catalog_id: str, asset_id: str, rtype: str) -> bool:
        resp = self._request("HEAD", self._rendition_url(catalog_id, asset_id, rtype),
                             ok=(200, 404))
        return resp.status == 200
