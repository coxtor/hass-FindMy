# Part of hass-FindMy (https://github.com/malmeloo/hass-FindMy), GPL-3.0.
# Original integration (c) 2024-2026 malmeloo. This file added 2026 by
# @coxtor.
"""FindMy sensor platform: lat/lon (recorded to HASS history) + battery
level / percent / voltage sensors derived from the Apple Find My status
byte the tag advertises."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, final, override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ._entity import (
    battery_label,
    battery_percent,
    battery_voltage_mv,
    build_device_info,
    device_unique_id,
    latest_report,
    status_counter,
)
from .coordinator import FindMyCoordinator, FindMyDevice
from .storage import RuntimeStorage

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.device_registry import DeviceInfo
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> bool:
    _LOGGER.debug("Setting up sensor entry: %s", entry.entry_id)

    item = RuntimeStorage.get(hass).get_entry(entry)
    if not isinstance(item, FindMyDevice):
        msg = "Cannot setup sensor entities for non-device!"
        raise ConfigEntryNotReady(msg)

    storage = RuntimeStorage.get(hass)
    async_add_entities(
        (
            FindMyLatitudeSensor(storage.coordinator, item, entry.entry_id),
            FindMyLongitudeSensor(storage.coordinator, item, entry.entry_id),
            FindMyPositionSensor(storage.coordinator, item, entry.entry_id),
            FindMySmoothedLatitudeSensor(storage.coordinator, item, entry.entry_id),
            FindMySmoothedLongitudeSensor(storage.coordinator, item, entry.entry_id),
            FindMySmoothedPositionSensor(storage.coordinator, item, entry.entry_id),
            FindMyBatteryLevelSensor(storage.coordinator, item, entry.entry_id),
            FindMyBatteryPercentSensor(storage.coordinator, item, entry.entry_id),
            FindMyBatteryVoltageSensor(storage.coordinator, item, entry.entry_id),
            FindMyStatusCounterSensor(storage.coordinator, item, entry.entry_id),
        ),
    )

    return True


class _FindMyBaseSensor(  # pyright: ignore[reportUninitializedInstanceVariable]
    CoordinatorEntity[FindMyCoordinator],
    SensorEntity,
):
    _attr_has_entity_name = True
    _attr_should_poll = False

    _suffix: str = ""

    def __init__(
        self,
        coordinator: FindMyCoordinator,
        device: FindMyDevice,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator, context=device)
        self._coordinator: FindMyCoordinator = coordinator
        self._device: FindMyDevice = device
        self._entry_id: str = entry_id
        self._cached_value: object | None = None

    @property
    @override
    def unique_id(self) -> str:  # pyright: ignore[reportIncompatibleVariableOverride]
        return f"{device_unique_id(self._device)}_{self._suffix}"

    @property
    @override
    def device_info(self) -> DeviceInfo:  # pyright: ignore[reportIncompatibleVariableOverride]
        return build_device_info(self._device)

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        self._cached_value = self._compute_value()
        self.async_write_ha_state()

    def _compute_value(self) -> object | None:
        return None


@final
class FindMyLatitudeSensor(_FindMyBaseSensor):
    _attr_name = "Latitude"
    _attr_native_unit_of_measurement = "°"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 6
    _suffix = "latitude"

    @override
    def _compute_value(self) -> float | None:
        report = latest_report(self._coordinator, self._device)
        return report.latitude if report else None

    @property
    @override
    def native_value(self) -> float | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        val = self._cached_value
        if val is None:
            val = self._compute_value()
        return val  # type: ignore[return-value]


@final
class FindMyLongitudeSensor(_FindMyBaseSensor):
    _attr_name = "Longitude"
    _attr_native_unit_of_measurement = "°"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 6
    _suffix = "longitude"

    @override
    def _compute_value(self) -> float | None:
        report = latest_report(self._coordinator, self._device)
        return report.longitude if report else None

    @property
    @override
    def native_value(self) -> float | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        val = self._cached_value
        if val is None:
            val = self._compute_value()
        return val  # type: ignore[return-value]


@final
class FindMyPositionSensor(_FindMyBaseSensor):
    """Convenience sensor combining lat + lon in a single 'lat,lon' string.
    Not graphable, but handy for template concatenation, notifications and
    passing to external map tools."""

    _attr_name = "Position"
    _suffix = "position"

    @override
    def _compute_value(self) -> str | None:
        report = latest_report(self._coordinator, self._device)
        if report is None:
            return None
        return f"{report.latitude:.6f},{report.longitude:.6f}"

    @property
    @override
    def native_value(self) -> str | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        val = self._cached_value
        if val is None:
            val = self._compute_value()
        return val  # type: ignore[return-value]


@final
class FindMySmoothedLatitudeSensor(_FindMyBaseSensor):
    """Trimmed-centroid latitude over the last N reports.

    Damps the "wandering" that stationary tags exhibit because every hearing
    iPhone attributes its OWN GPS fix to the tag. See _entity.smoothed_position
    for the algorithm and defaults.

    Disabled by default; enable in the entity registry if you want it on the
    map or in automations."""

    _attr_name = "Latitude (smoothed)"
    _attr_native_unit_of_measurement = "°"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 6
    _attr_entity_registry_enabled_default = False
    _suffix = "latitude_smoothed"

    @override
    def _compute_value(self) -> float | None:
        pos = self._coordinator.get_smoothed_position(self._device, self._entry_id)
        return pos[0] if pos else None

    @property
    @override
    def native_value(self) -> float | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        val = self._cached_value
        if val is None:
            val = self._compute_value()
        return val  # type: ignore[return-value]


@final
class FindMySmoothedLongitudeSensor(_FindMyBaseSensor):
    _attr_name = "Longitude (smoothed)"
    _attr_native_unit_of_measurement = "°"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 6
    _attr_entity_registry_enabled_default = False
    _suffix = "longitude_smoothed"

    @override
    def _compute_value(self) -> float | None:
        pos = self._coordinator.get_smoothed_position(self._device, self._entry_id)
        return pos[1] if pos else None

    @property
    @override
    def native_value(self) -> float | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        val = self._cached_value
        if val is None:
            val = self._compute_value()
        return val  # type: ignore[return-value]


@final
class FindMySmoothedPositionSensor(_FindMyBaseSensor):
    """`lat,lon` string of the smoothed position - for notifications and links."""

    _attr_name = "Position (smoothed)"
    _attr_entity_registry_enabled_default = False
    _suffix = "position_smoothed"

    @override
    def _compute_value(self) -> str | None:
        pos = self._coordinator.get_smoothed_position(self._device, self._entry_id)
        if pos is None:
            return None
        return f"{pos[0]:.6f},{pos[1]:.6f}"

    @property
    @override
    def native_value(self) -> str | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        val = self._cached_value
        if val is None:
            val = self._compute_value()
        return val  # type: ignore[return-value]


@final
class FindMyBatteryLevelSensor(_FindMyBaseSensor):
    _attr_name = "Battery level"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["ok", "medium", "low", "critical"]
    _suffix = "battery_level"

    @override
    def _compute_value(self) -> str | None:
        report = latest_report(self._coordinator, self._device)
        return battery_label(report.status if report else None)

    @property
    @override
    def native_value(self) -> str | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        val = self._cached_value
        if val is None:
            val = self._compute_value()
        return val  # type: ignore[return-value]


@final
class FindMyBatteryPercentSensor(_FindMyBaseSensor):
    _attr_name = "Battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _suffix = "battery_percent"

    @override
    def _compute_value(self) -> int | None:
        report = latest_report(self._coordinator, self._device)
        return battery_percent(report.status if report else None)

    @property
    @override
    def native_value(self) -> int | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        val = self._cached_value
        if val is None:
            val = self._compute_value()
        return val  # type: ignore[return-value]


@final
class FindMyBatteryVoltageSensor(_FindMyBaseSensor):
    _attr_name = "Battery voltage"
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = "mV"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_registry_enabled_default = False  # estimate only, opt-in
    _suffix = "battery_voltage"

    @override
    def _compute_value(self) -> int | None:
        report = latest_report(self._coordinator, self._device)
        return battery_voltage_mv(report.status if report else None)

    @property
    @override
    def native_value(self) -> int | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        val = self._cached_value
        if val is None:
            val = self._compute_value()
        return val  # type: ignore[return-value]


@final
class FindMyStatusCounterSensor(_FindMyBaseSensor):
    _attr_name = "Status counter"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_registry_enabled_default = False  # diagnostic, opt-in
    _suffix = "status_counter"

    @override
    def _compute_value(self) -> int | None:
        report = latest_report(self._coordinator, self._device)
        return status_counter(report.status if report else None)

    @property
    @override
    def native_value(self) -> int | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        val = self._cached_value
        if val is None:
            val = self._compute_value()
        return val  # type: ignore[return-value]
