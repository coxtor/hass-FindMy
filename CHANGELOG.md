# Changelog

## v0.7.2 – 2026-07-17

### Added

- **`binary_sensor.<tag>_freefall_recent`** — reads bit 3 of the
  OpenHaystack status byte, which the coxtor tag firmware sets for
  ~30 min after the accelerometer registers a free-fall event.
  PROBLEM device class, disabled by default.

## v0.7.1 – 2026-07-17

### Added

- **`binary_sensor.<tag>_armed`** — bit 4 of the OpenHaystack status
  byte, written by the
  [coxtor tag firmware](https://github.com/coxtor/openhaystack-tag-firmware)
  when the user activates the anti-theft mode on the tag. SAFETY device
  class, disabled by default (dead on stock firmware).

### Fixed

- **Coordinator was calling Apple N times per poll for a single tag**,
  where N = number of entities subscribed to that tag. `async_contexts()`
  returns the context of every subscribed entity, so a device with, say,
  8 entities (`device_tracker` + battery + lat/lon + binary sensors)
  triggered 8 parallel `fetch_location` calls for the same tag every
  poll cycle. Apple's endpoint rate-limits this and the coordinator
  timed out, taking every findmy entity offline.
- Deduplicate contexts with an id()-keyed dict before flattening for
  the FindMy library, so we ask Apple exactly once per unique device
  per poll regardless of how many HA entities are attached. This is
  what was causing the "add one more entity → whole integration goes
  unavailable" pattern seen through 0.7.0's rocky rollout.
- Users on 0.6.0 also benefit — fewer Apple calls per poll means less
  chance of rate-limit-induced timeouts even without the custom
  firmware entities.

## v0.7.0 – 2026-07-17

### Added

- **`binary_sensor.<tag>_motion_recent`** — reads bit 5 of the
  OpenHaystack status byte, which the
  [coxtor/openhaystack-tag-firmware](https://github.com/coxtor/openhaystack-tag-firmware)
  sets for ~5 min after the tag's accelerometer fires. Disabled by
  default; stock firmware leaves this bit at 0 so the entity would
  otherwise be dead weight.
  MOTION device class, so it plays nicely with HA's presence /
  automation UI.

### Notes

- Deliberately ships as a single new entity to avoid the "add several
  entities → everything unavailable" issue seen in the pulled 0.7.0
  attempts earlier today. `armed`, `freefall_recent` and `temperature`
  will follow as separate incremental releases once each one is
  individually verified stable in a real HA install.

## v0.6.0 – 2026-07-08

### Changed

- **Smoothing: adaptive motion detection** – before computing the historical
  median centroid, the algorithm now inspects the last 3 reports. If they
  agree with each other (< radius_m apart) but sit more than 2×radius_m
  from the older reports' median, the tag has actually moved – and the
  smoother returns the recent cluster median directly instead of dragging
  the old location around for hours.
- Single outliers still get discarded normally. Multiple inconsistent
  recent reports fall through to the standard trimmed-centroid path.

### Rationale

Previously a moved tag would take ~half the window (2–3 hours at default
settings) to catch up to its new location because the older reports still
dominated the median. Motion detection means real movement is reflected on
the map within one poll interval while the anti-outlier behaviour for
stationary tags stays intact.

## v0.5.0 – 2026-07-08

### Added

- **Smoothed device_tracker** – a second `device_tracker` entity per tag
  named `device_tracker.findmy_<tag>_smoothed` that surfaces the
  trimmed-centroid coordinates instead of the raw latest.  HA's built-in
  Map card, the mobile Companion app map and zone automations all consume
  it directly; no template YAML or custom-card gymnastics required.
- Both trackers group under the same HA device so you can enable / disable
  them independently from Settings → Devices → tag.

### Notes

- Disabled by default; enable via the device page.
- Uses the same options-flow sliders as the smoothed sensor entities; a
  slider change immediately updates both trackers on next poll.

## v0.4.0 – 2026-07-08

### Added

- **Position smoothing** – three new opt-in sensor entities per tag:
  `sensor.<tag>_latitude_smoothed`, `sensor.<tag>_longitude_smoothed`,
  `sensor.<tag>_position_smoothed`. Trimmed-centroid algorithm damps the
  wandering that stationary tags exhibit because every hearing iPhone
  attributes its own GPS fix to the tag.
- **Options-flow with sliders** – Settings → Devices & Services → FindMy →
  your tag → *Configure* opens three number sliders to tune the smoothing
  per tag (window, outlier radius, max age). Defaults are conservative;
  changes take effect immediately without waiting for the next poll.
- **`max_age` filter** – reports older than the configured age drop out of
  the smoothing input, so a tag that moved and stopped doesn't lag on its
  old location for hours.

### Changed

- Coordinator now keeps a per-device ring buffer of the last 100 reports in
  memory. Buffer is empty after a HA restart; smoothed sensors fall back to
  the raw latest report until the buffer refills.

### Notes

- Smoothed sensors are disabled by default in the entity registry (opt-in
  from Settings → Devices → tag).
- Options flow only shows on device entries; account entries abort with
  "no options" as expected.

## v0.2.0 – 2026-07-07

Feature-heavy release built on top of upstream
[malmeloo/hass-FindMy v0.0.1](https://github.com/malmeloo/hass-FindMy).
Original integration, config-flow scaffolding and findmy.py library
by [@malmeloo](https://github.com/malmeloo). All additions in this release
by [@coxtor](https://github.com/coxtor) and are being proposed as upstream
PRs alongside this fork release; once a feature merges upstream it will be
dropped from this fork.

### Added

- **OpenHaystack rotating device type** — accepts `devices.json` (with
  `privateKey` + `additionalKeys[]`) and tracks a firmware that iterates a
  pre-generated key set as a single Home Assistant entity. Solves the sparse
  updates problem when a rotating tag was previously imported as a single
  static key.
- **Apple Account import** — new setup menu entry that accepts a findmy.py
  account-state JSON so migrating from Macless-Haystack or another findmy.py
  runtime skips the login + 2FA dance.
- **Bulk import** — one file with N devices creates N entries in a single
  upload (background flows handle the tail).
- **Sensor platform** with 7 entities per tag:
  - `sensor.<tag>_latitude`, `sensor.<tag>_longitude` (numeric, recorded)
  - `sensor.<tag>_position` (`lat,lon` string for notifications / URLs)
  - `sensor.<tag>_battery_level` (enum: ok/medium/low/critical)
  - `sensor.<tag>_battery` (%, drives HA map battery icon)
  - `sensor.<tag>_battery_voltage` (mV estimate, opt-in)
  - `sensor.<tag>_status_counter` (raw counter bits, opt-in diagnostic)
- **Binary sensor platform**: `binary_sensor.<tag>_battery_low`
- **Service** `findmy.delete_devices` — bulk-remove device entries with
  optional filter (all/openhaystack/static/rolling).
- **Two automation blueprints** shipped in the repo:
  - `tag_zone_change.yaml` – zone enter/leave notification
  - `tag_stale.yaml` – warn when a tag hasn't reported in N hours
- Extensive **README examples**: automations, notifications with map links,
  Lovelace map/history/entity cards, service call templates.

### Changed

- Config-entry data model gained `device_openhaystack` variant.
- `device_tracker` now also exposes `battery_level` (%) for the standard
  HA map icon.
- Manifest is rebranded to point at this fork's documentation + issues.

### Notes

- HA 2024.7.4+ required.
- Firmware side: the battery sensors show meaningful values only when the
  firmware is built with `HAS_BATTERY=1`. Without it, the tag advertises
  status byte 0 and every battery sensor reads "ok" / 90 % / 3000 mV.
