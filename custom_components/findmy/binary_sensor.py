# Part of hass-FindMy (https://github.com/malmeloo/hass-FindMy), GPL-3.0.
# Original integration (c) 2024-2026 malmeloo. This file added 2026 by
# @coxtor.
"""FindMy binary_sensor platform: battery-low flag for automations."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, final, override

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ._entity import (
    armed,
    battery_bits,
    build_device_info,
    device_unique_id,
    latest_report,
    motion_recent,
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
    _LOGGER.debug("Setting up binary_sensor entry: %s", entry.entry_id)

    item = RuntimeStorage.get(hass).get_entry(entry)
    if not isinstance(item, FindMyDevice):
        msg = "Cannot setup binary_sensor entities for non-device!"
        raise ConfigEntryNotReady(msg)

    storage = RuntimeStorage.get(hass)
    async_add_entities(
        (
            FindMyBatteryLowBinarySensor(storage.coordinator, item, entry.entry_id),
            FindMyMotionRecentBinarySensor(storage.coordinator, item, entry.entry_id),
            FindMyArmedBinarySensor(storage.coordinator, item, entry.entry_id),
        ),
    )

    return True


@final
class FindMyBatteryLowBinarySensor(  # pyright: ignore[reportUninitializedInstanceVariable]
    CoordinatorEntity[FindMyCoordinator],
    BinarySensorEntity,
):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "Battery low"
    _attr_device_class = BinarySensorDeviceClass.BATTERY

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
        self._cached: bool | None = None

    @property
    @override
    def unique_id(self) -> str:  # pyright: ignore[reportIncompatibleVariableOverride]
        return f"{device_unique_id(self._device)}_battery_low"

    @property
    @override
    def device_info(self) -> DeviceInfo:  # pyright: ignore[reportIncompatibleVariableOverride]
        return build_device_info(self._device)

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        self._cached = self._compute()
        self.async_write_ha_state()

    def _compute(self) -> bool | None:
        report = latest_report(self._coordinator, self._device)
        bits = battery_bits(report.status if report else None)
        if bits is None:
            return None
        # low = 0b10, critical = 0b11 => bit 1 set
        return bits >= 0b10

    @property
    @override
    def is_on(self) -> bool | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        val = self._cached
        if val is None:
            val = self._compute()
        return val


@final
class FindMyMotionRecentBinarySensor(  # pyright: ignore[reportUninitializedInstanceVariable]
    CoordinatorEntity[FindMyCoordinator],
    BinarySensorEntity,
):
    """Motion-recent flag from the coxtor tag firmware.

    Reads bit 5 of the OpenHaystack status byte, which the extended
    firmware sets for ~5 min after the tag's accelerometer fires and
    clears afterwards. Stock firmware leaves this bit at 0 so the entity
    stays off — hence the disabled-by-default in the entity registry.

    Deliberately mirrors FindMyBatteryLowBinarySensor above 1-to-1 to
    avoid class hierarchies that made earlier release attempts unstable.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "Motion recent"
    _attr_device_class = BinarySensorDeviceClass.MOTION
    _attr_entity_registry_enabled_default = False

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
        self._cached: bool | None = None

    @property
    @override
    def unique_id(self) -> str:  # pyright: ignore[reportIncompatibleVariableOverride]
        return f"{device_unique_id(self._device)}_motion_recent"

    @property
    @override
    def device_info(self) -> DeviceInfo:  # pyright: ignore[reportIncompatibleVariableOverride]
        return build_device_info(self._device)

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        self._cached = self._compute()
        self.async_write_ha_state()

    def _compute(self) -> bool | None:
        report = latest_report(self._coordinator, self._device)
        return motion_recent(report.status if report else None)

    @property
    @override
    def is_on(self) -> bool | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        val = self._cached
        if val is None:
            val = self._compute()
        return val


@final
class FindMyArmedBinarySensor(  # pyright: ignore[reportUninitializedInstanceVariable]
    CoordinatorEntity[FindMyCoordinator],
    BinarySensorEntity,
):
    """Armed / anti-theft flag from the coxtor tag firmware (bit 4)."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "Armed"
    _attr_device_class = BinarySensorDeviceClass.SAFETY
    _attr_entity_registry_enabled_default = False

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
        self._cached: bool | None = None

    @property
    @override
    def unique_id(self) -> str:  # pyright: ignore[reportIncompatibleVariableOverride]
        return f"{device_unique_id(self._device)}_armed"

    @property
    @override
    def device_info(self) -> DeviceInfo:  # pyright: ignore[reportIncompatibleVariableOverride]
        return build_device_info(self._device)

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        self._cached = self._compute()
        self.async_write_ha_state()

    def _compute(self) -> bool | None:
        report = latest_report(self._coordinator, self._device)
        return armed(report.status if report else None)

    @property
    @override
    def is_on(self) -> bool | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        val = self._cached
        if val is None:
            val = self._compute()
        return val
