"""Exceptions raised by zeptrionair_api."""

from __future__ import annotations


class ZeptrionError(Exception):
    """Base class for all errors raised by this library."""


class ZeptrionConnectionError(ZeptrionError):
    """The device could not be reached (network error, refused connection, DNS failure)."""


class ZeptrionTimeoutError(ZeptrionConnectionError):
    """The device did not answer in time."""


class ZeptrionResponseError(ZeptrionError):
    """The device answered with an unexpected HTTP status."""

    def __init__(self, status: int, path: str, body: str = "") -> None:
        super().__init__(f"HTTP {status} for {path}")
        self.status = status
        self.path = path
        self.body = body


class ZeptrionNotFoundError(ZeptrionResponseError):
    """The endpoint does not exist on this device (HTTP 404), e.g. no Smartfront fitted."""


class ZeptrionParseError(ZeptrionError):
    """A response could not be understood."""
