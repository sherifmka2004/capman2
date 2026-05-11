"""Auto-discovers all BaseSensor subclasses in the capman.sensors package."""
from __future__ import annotations

import importlib
import logging
import pkgutil
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncio
    from capman.events import Event

from capman.sensors.base import BaseSensor

logger = logging.getLogger(__name__)


class SensorRegistry:
    def __init__(self):
        self._sensors: dict[str, type[BaseSensor]] = {}

    def discover(self) -> None:
        """Import all modules in capman.sensors, collect BaseSensor subclasses."""
        import capman.sensors as sensors_pkg

        for _, name, _ in pkgutil.iter_modules(sensors_pkg.__path__):
            if name in ("base", "registry"):
                continue
            try:
                module = importlib.import_module(f"capman.sensors.{name}")
            except Exception as e:
                logger.warning("Failed to import sensor module %s: %s", name, e)
                continue
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, BaseSensor)
                    and attr is not BaseSensor
                    and hasattr(attr, "sensor_id")
                ):
                    self._sensors[attr.sensor_id] = attr
                    logger.debug("Discovered sensor: %s", attr.sensor_id)

    def get_enabled(self, config: dict) -> list[type[BaseSensor]]:
        """Return sensor classes enabled for current platform + config."""
        platform = sys.platform
        enabled_ids = config.get("sensors", {}).get("enabled", list(self._sensors.keys()))
        result = []
        for sid, cls in self._sensors.items():
            if sid not in enabled_ids:
                continue
            support = getattr(cls, "platform_support", {"*"})
            if "*" in support or platform in support:
                result.append(cls)
            else:
                logger.debug("Sensor %s not supported on %s, skipping", sid, platform)
        return result

    @property
    def all_sensor_ids(self) -> list[str]:
        return list(self._sensors.keys())
