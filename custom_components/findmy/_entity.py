# Part of hass-FindMy (https://github.com/malmeloo/hass-FindMy), GPL-3.0.
# Original integration (c) 2024-2026 malmeloo. This file added 2026 by
# @coxtor.
"""Shared helpers for FindMy entities (device_tracker + sensor + binary_sensor).

Keeps device grouping / unique-ID logic in one place so all platforms
attach to the same HASS "device" and remain in sync when the coordinator
updates.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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


# Bits 5-0 of the status byte are repurposed by the
# coxtor/openhaystack-tag-firmware for live sensor + mode flags. Stock
# firmware leaves them at zero, so entities that read them are disabled
# by default.
_STATUS_MOTION_RECENT_MASK   = 0b00100000  # bit 5
_STATUS_ARMED_MASK           = 0b00010000  # bit 4
_STATUS_FREEFALL_RECENT_MASK = 0b00001000  # bit 3
_STATUS_TEMP_MASK            = 0b00000111  # bits 2-0

# Bucket → midpoint °C for the 8-step die-temp reading.
_TEMP_BUCKET_CELSIUS: dict[int, int] = {
    0: -15,
    1: -5,
    2: 5,
    3: 15,
    4: 25,  # room
    5: 35,
    6: 45,
    7: 55,
}


def motion_recent(status: int | None) -> bool | None:
    if status is None or not isinstance(status, int):
        return None
    return bool(status & _STATUS_MOTION_RECENT_MASK)


def armed(status: int | None) -> bool | None:
    if status is None or not isinstance(status, int):
        return None
    return bool(status & _STATUS_ARMED_MASK)


def freefall_recent(status: int | None) -> bool | None:
    if status is None or not isinstance(status, int):
        return None
    return bool(status & _STATUS_FREEFALL_RECENT_MASK)


def temperature_celsius_approx(status: int | None) -> int | None:
    if status is None or not isinstance(status, int):
        return None
    bucket = status & _STATUS_TEMP_MASK
    return _TEMP_BUCKET_CELSIUS.get(bucket)


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
SMOOTH_MAX_AGE_HOURS_DEFAULT = 6.0
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
    max_age_hours: float = SMOOTH_MAX_AGE_HOURS_DEFAULT,
) -> tuple[float, float] | None:
    """Return (lat, lon) trimmed-centroid of the last `window` reports.

    Filters applied in order:
      1. Only reports newer than `max_age_hours` back are considered.  If the
         tag moved and stopped, this stops the smoothed position from lagging
         forever on the old location.
      2. Take at most the last `window` of those.
      3. Compute median centroid, drop reports farther than `radius_m`.
      4. Mean of survivors. If fewer than SMOOTH_MIN_SAMPLES survive the
         filter, fall back to the freshest raw report.
    """
    if not reports:
        return None

    if max_age_hours > 0:
        cutoff = datetime.now(UTC) - timedelta(hours=max_age_hours)
        fresh = [r for r in reports if r.timestamp >= cutoff]
    else:
        fresh = list(reports)

    if not fresh:
        # every report is older than the cutoff; fall back to the freshest raw
        # so the sensor stays populated rather than going None
        r = reports[-1]
        return (r.latitude, r.longitude)

    window_reports = fresh[-window:]

    # --- Motion detection ---------------------------------------------------
    # Before we compute the historical median, look at the very freshest
    # reports.  If the last N_RECENT of them all agree with each other but
    # sit meaningfully far from the older ones, the tag has actually moved
    # - and clinging to the old median would lag the smoothed sensor by
    # hours.  In that case we return the recent-cluster median directly.
    #
    # "Meaningfully far" == more than 2 * radius_m from the older median.
    # That's roughly "beyond the normal iPhone-GPS noise the smoother is
    # designed to absorb".
    N_RECENT = 3
    if len(window_reports) >= N_RECENT + 3:
        recent = window_reports[-N_RECENT:]
        older = window_reports[:-N_RECENT]

        r_lat = _median([r.latitude for r in recent])
        r_lon = _median([r.longitude for r in recent])
        o_lat = _median([r.latitude for r in older])
        o_lon = _median([r.longitude for r in older])

        cluster_shift = haversine_m(r_lat, r_lon, o_lat, o_lon)
        if cluster_shift > 2 * radius_m:
            # Are the recent reports agreeing with each other?  If yes -> motion.
            recent_spread = max(
                haversine_m(r_lat, r_lon, r.latitude, r.longitude)
                for r in recent
            )
            if recent_spread <= radius_m:
                return (r_lat, r_lon)
            # Otherwise the recent points are all over the map; fall through
            # to the normal trimmed-centroid logic (they'll be treated as
            # outliers around the old centroid).

    m_lat = _median([r.latitude for r in window_reports])
    m_lon = _median([r.longitude for r in window_reports])

    good = [
        r for r in window_reports
        if haversine_m(m_lat, m_lon, r.latitude, r.longitude) <= radius_m
    ]

    if len(good) < SMOOTH_MIN_SAMPLES:
        r = window_reports[-1]
        return (r.latitude, r.longitude)

    mean_lat = sum(r.latitude for r in good) / len(good)
    mean_lon = sum(r.longitude for r in good) / len(good)
    return (mean_lat, mean_lon)
