"""
Shell sensor — watches shell history files for new commands.
Tails ~/.bash_history / ~/.zsh_history on change.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import ClassVar

from capman.events import Event, EventType
from capman.sensors.base import BaseSensor

logger = logging.getLogger(__name__)


class ShellSensor(BaseSensor):
    sensor_id: ClassVar[str] = "shell"
    platform_support: ClassVar[set[str]] = {"darwin", "linux"}

    async def run(self) -> None:
        cfg = self.config.get("sensors", {}).get("shell", {})
        watch_files = [
            Path(p).expanduser()
            for p in cfg.get("watch_files", ["~/.bash_history", "~/.zsh_history"])
        ]
        poll_interval_s = 2.0

        # Track last seen line count per file
        positions: dict[Path, int] = {}
        for f in watch_files:
            if f.exists():
                positions[f] = sum(1 for _ in f.open())
            else:
                positions[f] = 0

        while not self._stop_event.is_set():
            for hist_file in watch_files:
                if not hist_file.exists():
                    continue
                try:
                    lines = hist_file.read_text(errors="replace").splitlines()
                    prev_count = positions.get(hist_file, 0)
                    new_lines = lines[prev_count:]
                    positions[hist_file] = len(lines)

                    for line in new_lines:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        # zsh history format: ": timestamp:0;command"
                        if line.startswith(": ") and ";" in line:
                            line = line.split(";", 1)[1]
                        await self.emit(Event(
                            type=EventType.SHELL_COMMAND,
                            payload={
                                "command": line,
                                "cwd": "",
                                "shell": hist_file.name,
                                "command_id": "",
                            },
                            sensor_id=self.sensor_id,
                        ))
                except Exception as e:
                    logger.debug("ShellSensor error reading %s: %s", hist_file, e)

            await asyncio.sleep(poll_interval_s)
