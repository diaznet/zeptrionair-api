"""Shared test helpers."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from zeptrionair_api import ZeptrionDevice, events
from zeptrionair_api.testing import FakeChannel, FakeZeptrionDevice

FIXTURES = Path(__file__).parent / "fixtures"
DEVICE_NAMES = sorted(p.name for p in (FIXTURES / "devices").iterdir() if p.is_dir())


def device_responses(name: str) -> dict[str, dict[str, Any]]:
    data: dict[str, dict[str, Any]] = json.loads(
        (FIXTURES / "devices" / name / "responses.json").read_text(encoding="utf-8")
    )
    return data


def body(name: str, path: str) -> str:
    return str(device_responses(name)[path]["body"])


def ws_frames(name: str) -> list[dict[str, Any]]:
    lines = (FIXTURES / "ws" / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


@pytest.fixture(autouse=True)
def fast_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(events, "BACKOFF_MIN", 0.01)
    monkeypatch.setattr(events, "BACKOFF_MAX", 0.05)


@pytest.fixture
async def fake() -> AsyncIterator[FakeZeptrionDevice]:
    """Two lights, Smartfront with the observed 2-channel key layout."""
    async with FakeZeptrionDevice(
        channels=[FakeChannel("1", name="Light 1", group="Room A"), FakeChannel("1")],
        time_scale=0.01,
    ) as device:
        yield device


@pytest.fixture
async def fake_cover() -> AsyncIterator[FakeZeptrionDevice]:
    """One blind (+ unused channel), observed 1-channel cover key layout."""
    async with FakeZeptrionDevice(
        channels=[FakeChannel("5", name="Shade 1", travel_time=10), FakeChannel("-1")],
        btfu="0,0,1000,1000,0,0,8501,8602",
        time_scale=0.01,
    ) as device:
        yield device


@pytest.fixture
async def client(fake: FakeZeptrionDevice) -> AsyncIterator[ZeptrionDevice]:
    async with ZeptrionDevice(fake.host) as device:
        yield device
