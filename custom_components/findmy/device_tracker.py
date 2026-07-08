from __future__ import annotations

import logging
from functools import cached_property
from typing import TYPE_CHECKING, final, override

from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.components.device_tracker.const import SourceType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import generate_entity_id
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from findmy import FindMyAccessory, KeyPair

from ._entity import battery_percent as _battery_percent
from .config_flow import DeviceEntryData
from .const import DOMAIN
from .coordinator import FindMyCoordinator, FindMyDevice
from .openhaystack import OpenHaystackAccessory
from .storage import RuntimeStorage

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from findmy import LocationReport

    from .config_flow import DeviceEntryData

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> bool:
    _LOGGER.debug("Setting up device tracker entry: %s", entry.entry_id)

    item = RuntimeStorage.get(hass).get_entry(entry)
    if not isinstance(item, FindMyDevice):
        msg = "Cannot setup device tracker entity for non-device!"
        raise ConfigEntryNotReady(msg)

    storage = RuntimeStorage.get(hass)
    async_add_entities(
        (
            FindMyDeviceTracker(storage.coordinator, item, entry.entry_id),
            FindMySmoothedDeviceTracker(storage.coordinator, item, entry.entry_id),
        ),
    )

    return True


@final
class FindMyDeviceTracker(  # pyright: ignore [reportUninitializedInstanceVariable, reportIncompatibleVariableOverride]
    CoordinatorEntity[FindMyCoordinator],
    TrackerEntity,
):
    _attr_has_entity_name = True
    _attr_name = None

    _attr_should_poll = False

    def __init__(self, coordinator: FindMyCoordinator, device: FindMyDevice, entry_id: str) -> None:
        super().__init__(coordinator, context=device)

        self._coordinator: FindMyCoordinator = coordinator
        self._device: FindMyDevice = device
        self._entry_id: str = entry_id

        self._last_location: LocationReport | None = None

        # Define entity id
        # Set here instead of in a property because it needs a setter, so this is more convenient.
        self.entity_id = generate_entity_id(
            "device_tracker.findmy_{}",
            self.given_name,
            hass=self._coordinator.hass,
        )

    @property
    def findmy_device(self) -> FindMyDevice:
        return self._device

    @property
    def given_name(self) -> str:
        return self._device.name or "Unknown"

    @property
    @override
    def unique_id(self) -> str:  # pyright: ignore [reportIncompatibleVariableOverride]
        if isinstance(self._device, KeyPair):
            return self._device.hashed_adv_key_b64

        if isinstance(self._device, OpenHaystackAccessory):
            return self._device.identifier

        assert isinstance(self._device, FindMyAccessory)

        identifier = self._device.identifier
        if identifier is None:
            msg = "Device has no identifier"
            raise ValueError(msg)
        return identifier

    @property
    @override
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    @override
    def latitude(self) -> float | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        if self._last_location is None:
            return None
        return self._last_location.latitude

    @property
    @override
    def longitude(self) -> float | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        if self._last_location is None:
            return None
        return self._last_location.longitude

    @property
    def detected_at(self) -> datetime | None:
        if self._last_location is None:
            return None
        return self._last_location.timestamp

    @property
    def status(self) -> int | None:
        if self._last_location is None:
            return None
        return self._last_location.status

    @property
    def battery_level(self) -> int | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Rough battery % decoded from the status byte (0/50/70/90).

        This drives the battery icon on the HA map card. For automations use
        the dedicated `binary_sensor.<tag>_battery_low` or the sensor
        entities exposed by the sensor platform."""
        return _battery_percent(self.status)

    @property
    def mac_address(self) -> str | None:
        if isinstance(self._device, KeyPair):
            return self._device.mac_address
        if isinstance(self._device, OpenHaystackAccessory):
            # The tag rotates its MAC; report the leader key's MAC as a placeholder.
            return self._device.keypairs[0].mac_address
        return None

    @cached_property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={
                (DOMAIN, self.unique_id),
            },
            name=self.given_name,
        )

    @property
    @override
    def extra_state_attributes(  # pyright: ignore[reportIncompatibleVariableOverride]
        self,
    ) -> Mapping[str, int | str | datetime | None] | None:
        attrs = {
            "detected_at": self.detected_at,
            "status": self.status,
            "mac_address": self.mac_address,
        }

        if isinstance(self._device, FindMyAccessory):
            # TODO(malmeloo): make properties public in FindMy.py  # noqa: FIX002, TD003
            attrs["alignment_index"] = self._device._alignment_index  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
            attrs["alignment_date"] = self._device._alignment_date  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]

        if isinstance(self._device, OpenHaystackAccessory):
            attrs["key_count"] = len(self._device.keypairs)

        return attrs

    def _update_entry(self) -> None:
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            _LOGGER.error("Config entry for device tracker disappeared")
            return

        if isinstance(self._device, KeyPair):
            data: DeviceEntryData = {
                "type": "device_static",
                "data": self._device.to_json(),
            }
        elif isinstance(self._device, OpenHaystackAccessory):
            data = {
                "type": "device_openhaystack",
                "data": self._device.to_json(),
            }
        elif isinstance(self._device, FindMyAccessory):  # pyright: ignore[reportUnnecessaryIsInstance]
            data = {
                "type": "device_rolling",
                "data": self._device.to_json(),
            }
        else:
            _LOGGER.error("Unknown device type for entry update: %s", type(self._device))
            return

        _ = self.hass.config_entries.async_update_entry(entry, data=data)

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._last_location = (self._coordinator.data or {}).get(self._device)
        _LOGGER.debug("Updated data from coordinator: %s", self._last_location)

        self.async_write_ha_state()

        self._update_entry()


@final
class FindMySmoothedDeviceTracker(  # pyright: ignore [reportUninitializedInstanceVariable, reportIncompatibleVariableOverride]
    CoordinatorEntity[FindMyCoordinator],
    TrackerEntity,
):
    """Companion device_tracker that reports the smoothed position instead of
    the raw latest.  Useful for the built-in HA Map card which only accepts
    device_tracker entities - point at this one to get a stable trail on the
    map without pulling the raw wandering.

    Disabled by default; enable in the entity registry (or via the standard
    'Enable entity' UI on the device page) to activate it.
    """

    _attr_has_entity_name = True
    _attr_name = "Smoothed"
    _attr_should_poll = False
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
        self._last_position: tuple[float, float] | None = None

        self.entity_id = generate_entity_id(
            "device_tracker.findmy_{}_smoothed",
            self.given_name,
            hass=self._coordinator.hass,
        )

    @property
    def given_name(self) -> str:
        return self._device.name or "Unknown"

    @property
    @override
    def unique_id(self) -> str:  # pyright: ignore [reportIncompatibleVariableOverride]
        # Suffix so it doesn't collide with the raw tracker's unique_id
        if isinstance(self._device, KeyPair):
            base = self._device.hashed_adv_key_b64
        elif isinstance(self._device, OpenHaystackAccessory):
            base = self._device.identifier
        else:
            assert isinstance(self._device, FindMyAccessory)
            identifier = self._device.identifier
            if identifier is None:
                msg = "Device has no identifier"
                raise ValueError(msg)
            base = identifier
        return f"{base}_smoothed"

    @property
    @override
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    @override
    def latitude(self) -> float | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        pos = self._current_position()
        return pos[0] if pos else None

    @property
    @override
    def longitude(self) -> float | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        pos = self._current_position()
        return pos[1] if pos else None

    def _current_position(self) -> tuple[float, float] | None:
        if self._last_position is not None:
            return self._last_position
        return self._coordinator.get_smoothed_position(self._device, self._entry_id)

    @cached_property
    def device_info(self) -> DeviceInfo:
        # Same identifiers as the raw tracker so both entities group under one
        # HA device instead of showing as two separate devices.
        if isinstance(self._device, KeyPair):
            base = self._device.hashed_adv_key_b64
        elif isinstance(self._device, OpenHaystackAccessory):
            base = self._device.identifier
        else:
            assert isinstance(self._device, FindMyAccessory)
            base = self._device.identifier or ""
        return DeviceInfo(
            identifiers={(DOMAIN, base)},
            name=self.given_name,
        )

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        self._last_position = self._coordinator.get_smoothed_position(
            self._device, self._entry_id,
        )
        self.async_write_ha_state()
