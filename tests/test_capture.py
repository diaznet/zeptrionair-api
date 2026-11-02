"""Fixture capture and anonymisation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zeptrionair_api import capture as capture_module
from zeptrionair_api.capture import Anonymiser, capture, main, summarise
from zeptrionair_api.testing import FakeChannel, FakeZeptrionDevice


def test_anonymiser() -> None:
    anon = Anonymiser()
    assert anon.serial("20050094") == "99990001"
    assert anon.serial("20050094") == "99990001"
    assert anon.serial("18500184") == "99990002"
    body = anon.chdes(
        "<chdes><ch1><name>Terrasse</name><group>Garden</group><cat>1</cat></ch1>"
        "<ch2><name>Spots</name><group>Garden</group><cat>3</cat></ch2>"
        "<ch3><name>Store</name><group>Bedroom</group><cat>5</cat></ch3>"
        "<ch4><name>zApp-20050094 (4)</name><group></group><cat>-1</cat></ch4></chdes>"
    )
    text = anon.text(body + "<mac>20:F8:5E:A1:BA:FE</mac><mac>20:f8:5e:a1:ba:fe</mac>")
    assert "Terrasse" not in text
    assert "Garden" not in text
    assert "20050094" not in text
    assert "<name>Light 1</name><group>Room A</group>" in text
    assert "<name>Dimmer 1</name><group>Room A</group>" in text
    assert "<name>Shade 1</name><group>Room B</group>" in text
    assert "<name>zApp-99990001 (4)</name><group></group>" in text
    assert text.count("02:00:00:00:00:01") == 2


async def test_capture_against_fake(tmp_path: Path) -> None:
    async with (
        FakeZeptrionDevice(
            serial="12345678",
            mac="20:f8:5e:00:00:01",
            channels=[FakeChannel("5", name="Living room", group="Ground floor")],
        ) as one,
        FakeZeptrionDevice(serial="87654321", btfu=None, websocket=False) as two,
    ):
        index = await capture(tmp_path, [one.host, two.host])
    assert index["device_01"]["categories"] == ["5"]
    assert index["device_01"]["btfu"] == "0,0,1000,1000,8501,8602,4501,4602"
    assert index["device_02"]["smartfront_type"] is None
    assert index["device_02"]["status"]["/zapi/smartfront/id"] == 404

    written = "".join(p.read_text(encoding="utf-8") for p in tmp_path.rglob("*.json"))
    for secret in ("12345678", "87654321", "20:f8:5e", "Living room", "Ground floor", "127.0.0.1"):
        assert secret not in written
    data = json.loads((tmp_path / "device_01" / "responses.json").read_text(encoding="utf-8"))
    assert "<sn>99990001</sn>" in data["/zrap/id"]["body"]


async def test_capture_rejects_non_zapp(tmp_path: Path) -> None:
    async with FakeZeptrionDevice() as fake:
        fake.fail_next = [500]  # /zrap/id fails -> no serial
        with pytest.raises(ValueError, match="no serial number"):
            await capture(tmp_path, [fake.host])


async def test_capture_unreachable(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no serial"):
        await capture(tmp_path, ["127.0.0.1:1"])


def test_summarise_handles_bad_json() -> None:
    summary = summarise({"/zapi/smartfront/id": {"status": 200, "body": "{bad"}})
    assert summary["btfu"] is None
    assert summary["type"] is None


def test_main(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2

    async def fake_capture(outdir: Path, hosts: list[str]) -> dict[str, object]:
        return {"device_01": {"type": "3340-2-B", "categories": ["1"]}}

    monkeypatch.setattr(capture_module, "capture", fake_capture)
    assert main(["out", "host"]) == 0
    assert "device_01: 3340-2-B" in capsys.readouterr().out
