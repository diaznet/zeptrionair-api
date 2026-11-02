"""``zeptrion`` command-line tool (install with ``pip install zeptrionair-api[cli]``).

Exit codes: 0 success, 1 device or communication error, 2 usage error.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import sys
from collections.abc import Awaitable, Callable, Coroutine
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

try:
    import typer
except ImportError:  # pragma: no cover - depends on installed extras
    sys.exit("The CLI needs extra packages: pip install 'zeptrionair-api[cli]'")

from . import __version__
from .capture import capture
from .const import scene_command
from .device import ZeptrionDevice
from .exceptions import ZeptrionError
from .models import ChannelUpdate, ConnectionEvent, Event, KeyEvent

app = typer.Typer(
    name="zeptrion",
    help="Control and inspect Feller zeptrionAir devices on the local network.",
    no_args_is_help=True,
    add_completion=False,
)


HostArg = Annotated[str, typer.Argument(help="Hostname or IP address of the zApp device.")]
ChannelArg = Annotated[int, typer.Argument(min=1, max=8, help="Channel number.")]
JsonOpt = Annotated[bool, typer.Option("--json", help="Machine-readable JSON output.")]


def _run[T](factory: Callable[[], Coroutine[Any, Any, T]]) -> T:
    try:
        return asyncio.run(factory())
    except ZeptrionError as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(1) from err
    except ValueError as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(2) from err


def _with_device[T](host: str, action: Callable[[ZeptrionDevice], Awaitable[T]]) -> T:
    async def runner() -> T:
        async with ZeptrionDevice(host) as device:
            return await action(device)

    return _run(runner)


def _dump_json(data: Any) -> None:
    def default(value: Any) -> Any:
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            return dataclasses.asdict(value)
        if isinstance(value, (set, frozenset)):
            return sorted(value)
        return str(value)

    typer.echo(json.dumps(data, indent=2, default=default, ensure_ascii=False))


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit


@app.callback()
def _main(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version."),
    ] = False,
) -> None:
    """Control and inspect Feller zeptrionAir devices on the local network."""


# ------------------------------------------------------------------ discovery


@app.command()
def discover(
    timeout: Annotated[float, typer.Option(help="Seconds to listen.")] = 5.0,
    as_json: JsonOpt = False,
) -> None:
    """Find zApp devices on the local network (mDNS)."""
    from .discovery import discover as do_discover

    devices = _run(lambda: do_discover(timeout))
    if as_json:
        _dump_json(devices)
        return
    if not devices:
        typer.echo("No devices found. mDNS can be unreliable: try the hostname or IP directly.")
        return
    for device in devices:
        addresses = ", ".join(device.addresses)
        kind = device.type or "?"
        typer.echo(f"{device.host:<28} {addresses:<16} {kind:<10} sw {device.sw_version or '?'}")


# ---------------------------------------------------------------------- reads


@app.command()
def info(host: HostArg, as_json: JsonOpt = False) -> None:
    """Show device identification, capabilities, Smartfront and signal."""

    async def action(device: ZeptrionDevice) -> dict[str, Any]:
        return {
            "device": await device.identify(),
            "mac": await device.mac(),
            "rssi": await device.rssi(),
            "capabilities": await device.probe(),
            "smartfront": await device.smartfront(),
            "sensors": await device.sensors(),
        }

    data = _with_device(host, action)
    if as_json:
        _dump_json(data)
        return
    dev = data["device"]
    caps = data["capabilities"]
    typer.echo(f"Serial:      {dev.serial}")
    typer.echo(f"Type:        {dev.type}  (hw {dev.hw_version})")
    typer.echo(f"Firmware:    {dev.sw_version}")
    typer.echo(f"MAC:         {data['mac']}")
    typer.echo(f"Wi-Fi:       {data['rssi']} dBm")
    typer.echo(
        "Features:    "
        + ", ".join(
            f"{name}={'yes' if getattr(caps, name) else 'no'}"
            for name in ("websocket", "smartfront", "smartbuttons", "sensors")
        )
    )
    front = data["smartfront"]
    if front is not None:
        typer.echo(f"Smartfront:  {front.type} (sw {front.sw_version})")
        for key in front.keys:
            if key.kind == "unused":
                continue
            detail = key.kind
            if key.kind == "channel":
                channels = ",".join(f"ch{c}" for c in sorted(key.channels))
                detail = f"{channels} {key.direction}"
            typer.echo(f"  key {key.key}: {detail} ({key.raw})")
    sensors = data["sensors"]
    if sensors is not None:
        values = {
            "temperature": sensors.temperature,
            "illuminance": sensors.illuminance,
            "humidity": sensors.humidity,
        }
        present = {name: value for name, value in values.items() if value is not None}
        if present:
            typer.echo("Sensors:     " + ", ".join(f"{k}={v}" for k, v in present.items()))


@app.command()
def channels(
    host: HostArg,
    show_all: Annotated[bool, typer.Option("--all", help="Include unused channels.")] = False,
    as_json: JsonOpt = False,
) -> None:
    """List channels with their category and current value."""

    async def action(device: ZeptrionDevice) -> tuple[Any, dict[int, int | None]]:
        return await device.channels(), await device.states()

    chans, states = _with_device(host, action)
    if not show_all:
        chans = [channel for channel in chans if channel.in_use]
    if as_json:
        _dump_json([{**dataclasses.asdict(c), "value": states.get(c.number)} for c in chans])
        return
    for channel in chans:
        kind = (
            channel.category.name.lower() if channel.category else f"unused({channel.raw_category})"
        )
        value = states.get(channel.number)
        typer.echo(
            f"ch{channel.number}  {kind:<12} {value if value is not None else '?':>4}  "
            f"{channel.name}" + (f"  [{channel.group}]" if channel.group else "")
        )


@app.command()
def state(host: HostArg, as_json: JsonOpt = False) -> None:
    """Show the current value of every channel."""
    states = _with_device(host, lambda device: device.states())
    if as_json:
        _dump_json({f"ch{number}": value for number, value in states.items()})
        return
    for number, value in sorted(states.items()):
        typer.echo(f"ch{number}={value if value is not None else '?'}")


# --------------------------------------------------------------------- writes


def _send(host: str, channel: int, command: str) -> None:
    _with_device(host, lambda device: device.send(channel, command))
    typer.echo(f"ch{channel}: {command}")


@app.command()
def on(host: HostArg, channel: ChannelArg) -> None:
    """Switch a light on."""
    _send(host, channel, "on")


@app.command()
def off(host: HostArg, channel: ChannelArg) -> None:
    """Switch a light off."""
    _send(host, channel, "off")


@app.command()
def toggle(host: HostArg, channel: ChannelArg) -> None:
    """Toggle a light."""
    _send(host, channel, "toggle")


@app.command("open")
def open_cover(host: HostArg, channel: ChannelArg) -> None:
    """Open a cover fully."""
    _send(host, channel, "open")


@app.command("close")
def close_cover(host: HostArg, channel: ChannelArg) -> None:
    """Close a cover fully."""
    _send(host, channel, "close")


@app.command()
def stop(host: HostArg, channel: ChannelArg) -> None:
    """Stop a moving cover or a dimming light."""
    _send(host, channel, "stop")


class Direction(StrEnum):
    open = "open"
    close = "close"


class DimDirection(StrEnum):
    up = "up"
    down = "down"


MsOpt = Annotated[
    int | None, typer.Option("--ms", min=100, max=32000, help="Stop after this many milliseconds.")
]


@app.command()
def move(host: HostArg, channel: ChannelArg, direction: Direction, ms: MsOpt = None) -> None:
    """Move a cover (until stopped, or for --ms milliseconds)."""
    command = f"move_{direction.value}" + (f"_{ms}" if ms else "")
    _send(host, channel, command)


@app.command()
def dim(host: HostArg, channel: ChannelArg, direction: DimDirection, ms: MsOpt = None) -> None:
    """Dim a light up or down (until stopped, or for --ms milliseconds)."""
    command = f"dim_{direction.value}" + (f"_{ms}" if ms else "")
    _send(host, channel, command)


class SceneAction(StrEnum):
    recall = "recall"
    store = "store"
    delete = "delete"


@app.command()
def scene(
    host: HostArg,
    channel: ChannelArg,
    action: SceneAction,
    number: Annotated[int, typer.Argument(min=1, max=4, help="Scene 1-4.")],
) -> None:
    """Recall, store or delete a scene."""
    _send(host, channel, scene_command(action.value, number))


@app.command()
def send(
    host: HostArg,
    channel: ChannelArg,
    command: Annotated[str, typer.Argument(help="Raw command, e.g. move_open_1500.")],
) -> None:
    """Send a raw channel command (validated)."""
    _send(host, channel, command)


@app.command()
def rename(
    host: HostArg,
    channel: ChannelArg,
    name: Annotated[str | None, typer.Option(help="New channel name (max 32 bytes).")] = None,
    group: Annotated[str | None, typer.Option(help="New group/room (max 32 bytes).")] = None,
) -> None:
    """Change a channel's name and/or group stored on the device."""
    fields = {key: value for key, value in (("name", name), ("group", group)) if value is not None}
    if not fields:
        typer.echo("Nothing to change: give --name and/or --group.", err=True)
        raise typer.Exit(2)
    _with_device(host, lambda device: device.set_channel_description(channel, **fields))
    typer.echo(f"ch{channel}: updated {', '.join(fields)}")


# --------------------------------------------------------------------- events


def _format_event(host: str, event: Event) -> str:
    stamp = datetime.now().strftime("%H:%M:%S")
    if isinstance(event, ChannelUpdate):
        value = event.value if event.value is not None else "?"
        return f"{stamp} {host} ch{event.channel}={value}"
    if isinstance(event, KeyEvent):
        what = "pressed" if event.pressed else "released"
        detail = ""
        if event.function is not None:
            detail = f" ({event.function.kind}"
            if event.function.kind == "channel":
                channels = ",".join(f"ch{c}" for c in sorted(event.function.channels))
                detail += f" {channels} {event.function.direction}"
            detail += ")"
        return f"{stamp} {host} key{event.key} {what}{detail}"
    status = "connected" if event.connected else f"disconnected ({event.error})"
    return f"{stamp} {host} {status} via {event.transport}"


def _event_json(host: str, event: Event) -> str:
    data: dict[str, Any] = {"time": datetime.now().isoformat(timespec="milliseconds"), "host": host}
    data["type"] = {
        ChannelUpdate: "channel",
        KeyEvent: "key",
        ConnectionEvent: "connection",
    }[type(event)]
    payload = dataclasses.asdict(event)
    function = payload.get("function")
    if isinstance(function, dict):
        function["channels"] = sorted(function["channels"])
    data.update(payload)
    return json.dumps(data, ensure_ascii=False)


@app.command()
def watch(
    hosts: Annotated[list[str], typer.Argument(help="One or more devices.")],
    as_json: JsonOpt = False,
    count: Annotated[
        int | None, typer.Option(help="Exit after this many events (for scripts).")
    ] = None,
) -> None:
    """Print live events (channel changes, key presses) until interrupted."""

    async def runner() -> None:
        queue: asyncio.Queue[tuple[str, Event]] = asyncio.Queue()

        async def pump(host: str) -> None:
            async with ZeptrionDevice(host) as device:
                async for event in device.events():
                    await queue.put((host, event))

        tasks = [asyncio.create_task(pump(host)) for host in hosts]
        seen = 0
        try:
            while count is None or seen < count:
                getter = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait([getter, *tasks], return_when=asyncio.FIRST_COMPLETED)
                if getter not in done:
                    getter.cancel()
                    for task in done:
                        task.result()  # re-raise the pump's error
                    continue
                host, event = getter.result()
                typer.echo(_event_json(host, event) if as_json else _format_event(host, event))
                seen += 1
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    with contextlib.suppress(KeyboardInterrupt):
        _run(runner)


# ----------------------------------------------------------------------- dump


@app.command()
def dump(
    outdir: Annotated[Path, typer.Argument(help="Folder to write the fixtures into.")],
    hosts: Annotated[list[str], typer.Argument(help="One or more devices.")],
) -> None:
    """Capture anonymised, read-only fixtures (for bug reports and contributions)."""
    index = _run(lambda: capture(outdir, hosts))
    for name, summary in index.items():
        typer.echo(f"{name}: {summary['type']} categories={summary['categories']}")
    typer.echo(f"Written to {outdir}. Please review the files before sharing them.")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
