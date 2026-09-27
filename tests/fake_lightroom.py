"""In-memory fake of the Adobe IMS and Lightroom endpoints."""

import io
import json
import urllib.parse

from omv_lightroomsync.http import Response

PREFIX = b"while (1) {}\n"


def jpeg(width=64, height=48, color=(200, 30, 30)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, "JPEG")
    return buf.getvalue()


def make_asset(asset_id, filename="IMG_0001.CR3", capture="2024-03-05T14:15:16",
               updated="2024-03-06T00:00:00Z"):
    return {
        "id": asset_id, "type": "asset", "subtype": "image", "updated": updated,
        "created": "2024-03-06T00:00:00Z",
        "payload": {"captureDate": capture,
                    "importSource": {"fileName": filename}},
    }


class FakeLightroom:
    def __init__(self, page_size=2):
        self.assets = []            # ordered list of asset dicts
        self.albums = {}            # id -> {"name", "parent", "assets": [ids]}
        self.renditions = {}        # (asset_id, type) -> bytes
        self.generatable = set()    # types the server will generate on POST
        self.deny_generation = False
        self.page_size = page_size
        self.requests = []
        self.token_counter = 0
        self.fail_once = {}         # url substring -> status to return once

    def add(self, asset, sizes=("2048", "1280", "640"), data=None):
        self.assets.append(asset)
        for s in sizes:
            self.renditions[(asset["id"], s)] = data or jpeg()

    # --- transport interface ---

    def request(self, method, url, headers=None, data=None):
        self.requests.append((method, url, dict(headers or {})))
        for needle, status in list(self.fail_once.items()):
            if needle in url:
                del self.fail_once[needle]
                return Response(status, {"retry-after": "0"}, b"")
        parts = urllib.parse.urlsplit(url)
        if parts.netloc == "ims-na1.adobelogin.com":
            return self._token(urllib.parse.parse_qs(data.decode()))
        assert headers.get("X-API-Key") == "cid", headers
        assert headers.get("Authorization", "").startswith("Bearer at-"), headers
        path = parts.path[len("/v2/"):].split("/")
        query = urllib.parse.parse_qs(parts.query)
        if path == ["account"]:
            return self._json({"email": "me@example.com",
                               "entitlement": {"status": "subscriber"}})
        if path == ["catalog"]:
            return self._json({"id": "cat1"})
        if path[2] == "assets" and len(path) == 3:
            return self._page(self.assets, query, "catalogs/cat1/assets")
        if path[2] == "albums" and len(path) == 3:
            albums = [{"id": aid, "payload": {"name": a["name"],
                       **({"parent": {"id": a["parent"]}} if a.get("parent") else {})}}
                      for aid, a in self.albums.items()]
            return self._page(albums, query, "catalogs/cat1/albums")
        if path[2] == "albums" and path[4] == "assets":
            ids = self.albums[path[3]]["assets"]
            res = [{"id": "aa-" + i, "asset": a} for i in ids
                   for a in self.assets if a["id"] == i]
            return self._page(res, query, f"catalogs/cat1/albums/{path[3]}/assets")
        if path[2] == "assets" and path[4] == "renditions":
            asset_id = path[3]
            if method == "POST":
                if self.deny_generation:
                    return Response(403, {}, b"")
                for t in headers["X-Generate-Renditions"].split(","):
                    if t in self.generatable:
                        self.renditions[(asset_id, t)] = jpeg(400, 300)
                return Response(202, {}, b"")
            body = self.renditions.get((asset_id, path[5]))
            if body is None:
                return Response(404, {}, b"")
            return Response(200, {"content-type": "image/jpeg"},
                            b"" if method == "HEAD" else body)
        raise AssertionError(f"unexpected request {method} {url}")

    def _token(self, form):
        self.token_counter += 1
        if form["grant_type"] == ["authorization_code"]:
            assert form["code"] == ["the-code"], form
        body = {"access_token": f"at-{self.token_counter}", "expires_in": 86399,
                "refresh_token": "rt-1"}
        return Response(200, {}, json.dumps(body).encode())

    def _json(self, obj):
        return Response(200, {}, PREFIX + json.dumps(obj).encode())

    def _page(self, items, query, rel):
        start = int(query.get("offset", ["0"])[0])
        chunk = items[start:start + self.page_size]
        body = {"base": "https://lr.adobe.io/v2/", "resources": chunk, "links": {}}
        if start + self.page_size < len(items):
            body["links"]["next"] = {
                "href": f"{rel}?offset={start + self.page_size}"}
        return self._json(body)
