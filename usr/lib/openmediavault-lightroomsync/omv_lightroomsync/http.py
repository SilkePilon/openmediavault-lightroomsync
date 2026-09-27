"""Minimal HTTP transport on top of urllib, so there is no dependency on
python3-requests. Tests replace `Transport` with a fake."""

import dataclasses
import urllib.error
import urllib.request
from typing import Dict, Optional

USER_AGENT = "openmediavault-lightroomsync"


@dataclasses.dataclass
class Response:
    status: int
    headers: Dict[str, str]
    body: bytes


class Transport:
    def __init__(self, timeout: float = 120.0):
        self.timeout = timeout

    def request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        data: Optional[bytes] = None,
    ) -> Response:
        headers = dict(headers or {})
        headers.setdefault("User-Agent", USER_AGENT)
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return Response(
                    resp.status,
                    {k.lower(): v for k, v in resp.headers.items()},
                    resp.read(),
                )
        except urllib.error.HTTPError as e:
            # Non-2xx responses are data, not exceptions; the callers decide.
            return Response(
                e.code,
                {k.lower(): v for k, v in (e.headers or {}).items()},
                e.read() or b"",
            )
