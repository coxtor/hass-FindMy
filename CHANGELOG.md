# Changelog

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
