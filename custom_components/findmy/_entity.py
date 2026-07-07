# Part of hass-FindMy (https://github.com/malmeloo/hass-FindMy), GPL-3.0.
# Original integration (c) 2024-2026 malmeloo. This file added 2026 by
# @coxtor.
"""Shared helpers for FindMy entities (device_tracker + sensor + binary_sensor).

Keeps device grouping / unique-ID logic in one place so all platforms
attach to the same HASS "device" and remain in sync when the coordinator
updates.
"""

from __future__ import annotations

from math import asin, cos, radians, sin, sqrt
from typing import TYPE_CHECKING

from findmy import FindMyAccessory, KeyPair
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .openhaystack import OpenHaystackAccessory

if TYPE_CHECKING:
    from collections.abc import Sequence

    from findmy import LocationReport

    from .coordinator import FindMyCoordinator, FindMyDevice


def device_unique_id(device: FindMyDevice) -> str:
    """Stable identifier used both for the device registry and for the primary
    device_tracker's unique_id. All companion entities derive from this."""
    if isinstance(device, KeyPair):
        return device.hashed_adv_key_b64
    if isinstance(device, OpenHaystackAccessory):
        return device.identifier

    assert isinstance(device, FindMyAccessory)
    identifier = device.identifier
    if identifier is None:
        msg = "Device has no identifier"
        raise ValueError(msg)
    return identifier


def device_name(device: FindMyDevice) -> str:
    return device.name or "Unknown"


def build_device_info(device: FindMyDevice) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, device_unique_id(device))},
        name=device_name(device),
    )


def latest_report(
    coordinator: FindMyCoordinator,
    device: FindMyDevice,
) -> LocationReport | None:
    if coordinator.data is None:
        return None
    return coordinator.data.get(device)


# --- Battery bits (Apple Find My payload byte 6, bits 6-7) ----------------
# Encoded by heystack-nrf5x set_battery():
#   0b00 = OK        (>80 %)
#   0b01 = medium    (50-80 %)
#   0b10 = low       (30-50 %)
#   0b11 = critical  (<30 %)

BATTERY_LABELS = {
    0b00: "ok",
    0b01: "medium",
    0b10: "low",
    0b11: "critical",
}

# Rough percentage centres for each level. Not accurate; the tag only
# transmits 2 bits. Use these for the sensor value or leave the raw label.
BATTERY_PERCENTS = {
    0b00: 90,
    0b01: 65,
    0b10: 40,
    0b11: 15,
}

# CR2032 voltage curve mapped to the 4 levels. Also a rough estimate.
BATTERY_VOLTAGES_MV = {
    0b00: 3000,
    0b01: 2800,
    0b10: 2600,
    0b11: 2400,
}


def battery_bits(status: int | None) -> int | None:
    if status is None:
        return None
    return (status >> 6) & 0b11


def battery_label(status: int | None) -> str | None:
    bits = battery_bits(status)
    return BATTERY_LABELS.get(bits) if bits is not None else None


def battery_percent(status: int | None) -> int | None:
    bits = battery_bits(status)
    return BATTERY_PERCENTS.get(bits) if bits is not None else None


def battery_voltage_mv(status: int | None) -> int | None:
    bits = battery_bits(status)
    return BATTERY_VOLTAGES_MV.get(bits) if bits is not None else None


def status_counter(status: int | None) -> int | None:
    if status is None:
        return None
    return status & 0b00111111


# --- Position smoothing ---------------------------------------------------
# Location reports for a stationary tag "wander" across a few blocks because
# every iPhone that hears the tag uploads its OWN GPS fix as the tag's
# location.  We damp this by:
#   1. taking the last N reports (window)
#   2. finding a median centroid (robust to outliers)
#   3. dropping reports > radius_m from the centroid (the outliers)
#   4. returning the mean of what remains
#
# Fallback: if fewer than `min_samples` reports survive the filter, we return
# the freshest raw position so the entity doesn't go None during warm-up.

SMOOTH_WINDOW_DEFAULT = 20
SMOOTH_RADIUS_M_DEFAULT = 200.0
SMOOTH_MIN_SAMPLES = 3

_EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters."""
    rlat1, rlat2 = radians(lat1), radians(lat2)
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(rlat1) * cos(rlat2) * sin(dlon / 2) ** 2
    return 2 * _EARTH_RADIUS_M * asin(sqrt(a))


def _median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2


def smoothed_position(
    reports: Sequence[LocationReport],
    window: int = SMOOTH_WINDOW_DEFAULT,
    radius_m: float = SMOOTH_RADIUS_M_DEFAULT,
) -> tuple[float, float] | None:
    """Return (lat, lon) trimmed-centroid of the last `window` reports,
    dropping any farther than `radius_m` from the median centroid.
    None if `reports` is empty."""
    if not reports:
        return None
    window_reports = list(reports)[-window:]
    if not window_reports:
        return None

    m_lat = _median([r.latitude for r in window_reports])
    m_lon = _median([r.longitude for r in window_reports])

    good = [
        r for r in window_reports
        if haversine_m(m_lat, m_lon, r.latitude, r.longitude) <= radius_m
    ]

    if len(good) < SMOOTH_MIN_SAMPLES:
        # not enough data through the filter; fall back to the freshest raw report
        r = window_reports[-1]
        return (r.latitude, r.longitude)

    mean_lat = sum(r.latitude for r in good) / len(good)
    mean_lon = sum(r.longitude for r in good) / len(good)
    return (mean_lat, mean_lon)
