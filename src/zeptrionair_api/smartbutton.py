"""Smartbutton programming payloads (Feller zApp API, section 5.2).

A Smartbutton stores up to a few HTTP requests and sends them when pressed, directly from the
zApp, also when Home Assistant is down. A request uses the keys ``req`` (method), ``loc`` (host or
IP), ``pth`` (path), ``bdy`` (body), and optionally ``typ`` (content type), ``hdr`` (extra header
line) and ``prt`` (port).

[PDF] The whole ``prgs`` request, HTTP header included, must not exceed 730 bytes. An attribute
equal to the one of the previous array element may be left out (it is inherited).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from .const import validate_command

SMARTBUTTON_PATH = "/zapi/smartbt"
MAX_REQUEST_BYTES = 730
REQUEST_KEYS = ("req", "loc", "prt", "pth", "typ", "hdr", "bdy")

SmartbuttonRequest = dict[str, str]


class SmartbuttonProgramTooLongError(ValueError):
    """The programme doesn't fit into the 730 bytes the zApp accepts."""

    def __init__(self, size: int) -> None:
        super().__init__(
            f"Smartbutton programme is {size} bytes, the zApp accepts at most {MAX_REQUEST_BYTES}"
        )
        self.size = size


def _split_host(host: str) -> tuple[str, str | None]:
    host = host.strip().removeprefix("http://").rstrip("/")
    if host.count(":") == 1:
        name, port = host.split(":")
        return name, (port if port and port != "80" else None)
    return host, None


def channel_request(host: str, channel: int, command: str) -> SmartbuttonRequest:
    """A request that sends ``command`` to ``channel`` of the zApp at ``host``."""
    if not 1 <= channel <= 8:
        raise ValueError(f"invalid channel number {channel}")
    validate_command(command)
    location, port = _split_host(host)
    request: SmartbuttonRequest = {"req": "POST", "loc": location}
    if port:
        request["prt"] = port
    request["pth"] = "/zrap/chctrl"
    request["bdy"] = f"cmd{channel}={command}"
    return request


def _is_channel_request(request: Mapping[str, str]) -> bool:
    return (
        request.get("pth") == "/zrap/chctrl"
        and request.get("req", "POST") == "POST"
        and not request.get("typ")
        and not request.get("hdr")
    )


def merge(requests: Sequence[Mapping[str, str]]) -> list[SmartbuttonRequest]:
    """Combine channel commands for the same zApp into one request (``cmd1=..&cmd2=..``)."""
    merged: list[SmartbuttonRequest] = []
    for request in requests:
        unknown = set(request) - set(REQUEST_KEYS)
        if unknown:
            raise ValueError(f"unknown Smartbutton request keys: {sorted(unknown)}")
        current = {key: str(request[key]) for key in REQUEST_KEYS if key in request}
        target = next(
            (
                existing
                for existing in merged
                if _is_channel_request(existing)
                and _is_channel_request(current)
                and existing.get("loc") == current.get("loc")
                and existing.get("prt") == current.get("prt")
                and not {p.split("=")[0] for p in existing["bdy"].split("&")}
                & {p.split("=")[0] for p in current.get("bdy", "").split("&")}
            ),
            None,
        )
        if target is not None:
            target["bdy"] = f"{target['bdy']}&{current['bdy']}"
        else:
            merged.append(current)
    return merged


def compact(requests: Sequence[Mapping[str, str]]) -> list[SmartbuttonRequest]:
    """Leave out attributes equal to the previous element's (the zApp inherits them).

    An element is only compacted if it has every key the previous element had; otherwise it
    would inherit a key it doesn't want, so it is written in full.
    """
    result: list[SmartbuttonRequest] = []
    previous: Mapping[str, str] | None = None
    for request in requests:
        current = dict(request)
        if previous is not None and set(previous) <= set(current):
            current = {k: v for k, v in current.items() if previous.get(k) != v}
            if not current:  # identical request: keep the body so the element isn't empty
                current = {"bdy": request["bdy"]}
        result.append(current)
        previous = request
    return result


def encode(requests: Sequence[Mapping[str, str]]) -> str:
    """The JSON body for ``prgs``: merged, compacted, without spaces."""
    if not requests:
        raise ValueError("a Smartbutton programme needs at least one request")
    return json.dumps(compact(merge(requests)), separators=(",", ":"), ensure_ascii=False)


def request_size(host: str, body: str) -> int:
    """Size of the ``prgs`` request as this library sends it (minimal headers)."""
    length = len(body.encode("utf-8"))
    header = (
        f"POST {SMARTBUTTON_PATH}/prgs HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {length}\r\n\r\n"
    )
    return len(header.encode("utf-8")) + length
