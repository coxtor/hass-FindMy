# FindMy - Home Assistant integration

Home Assistant custom integration that provides `device_tracker` entities for
FindMy-Network-enabled devices. Talks directly to Apple's Find My servers with
your Apple ID credentials - no self-hosted backend required.

**This fork adds a third device type: `openhaystack`**, which understands the
`devices.json` format produced by OpenHaystack / Macless-Haystack. It's built
for firmwares that rotate through a pre-generated set of keys (typical
OpenHaystack build with `MAX_KEYS=250`), where the upstream `static` device
would only report location during ~0.4 % of the tag's runtime because it only
knows one key.

Upstream: [malmeloo/hass-FindMy](https://github.com/malmeloo/hass-FindMy) - report bugs there for the non-openhaystack code paths.

## Highlights

- **Integrated Anisette provider** - no need to run a separate anisette-server
  container. Optionally point the account setup at a private/remote anisette
  URL if you have one.
- **Direct Apple-account polling** via [findmy.py](https://github.com/malmeloo/FindMy.py).
  Does *not* use Macless-Haystack as a backend; it fetches location reports
  itself.
- **Three device types**:
  - `Static` - single OpenHaystack private key.
  - `Rolling` - real AirTag / FindMy accessory (.plist).
  - **`OpenHaystack rotating`** - a `devices.json` with a leader `privateKey`
    plus an `additionalKeys[]` array. All keys are polled in one Apple API
    call per interval; the freshest report is surfaced on a single HA entity.

## End-to-end flow

```
 ┌──────────────────────┐        ┌──────────────────────┐
 │ Tag hardware         │        │ Passing iPhones      │
 │ (nRF52810 + firmware)│ BLE    │ (any Find My user)   │
 │                      ├───────>│                      │
 │ rotates through      │  adv   │ upload encrypted     │
 │ N pre-generated keys │        │ location reports     │
 └──────────────────────┘        └──────────┬───────────┘
                                            │ HTTPS
                                            v
                              ┌──────────────────────────┐
                              │ Apple Find My servers    │
                              └──────────┬───────────────┘
                                         │ your Apple ID
                                         │ (fetch by key)
                                         v
                          ┌──────────────────────────────┐
                          │ hass-FindMy (this integration)│
                          │ integrated anisette provider │
                          │ FindMy.py 0.10.x             │
                          └──────────┬───────────────────┘
                                     │
                                     v
                          `device_tracker.<tag_name>`
```

Firmware side: [OpenHaystack](https://github.com/seemoo-lab/openhaystack) tools
generate the key set; a build tool patches it into the firmware binary; the
same tools emit a `devices.json` that you upload here. See the [hardware
example](#hardware-example-holyiot-nrf52810) below for a full walkthrough.

## Installation via HACS

1. HACS → ⋮ (top-right) → **Custom repositories**
2. Add `https://github.com/coxtor/hass-FindMy`, category **Integration**
3. Search for "FindMy", install
4. Restart Home Assistant

## Configuring accounts and devices

You need at least one Apple Account entry and at least one tracker device
entry.

### Apple Account

There are two ways to configure an Apple Account:

**Option A: Fresh login** (`Apple Account (login)` in the setup menu)

1. `Settings → Devices & Services → Add Integration → FindMy → Apple Account (login)`
2. Enter e-mail + password
3. Anisette URL (optional): leave blank to use the integrated provider. Only
   set it if you have a private Anisette server (e.g.
   [anisette-v3-server](https://github.com/Dadoum/anisette-v3-server)) which
   tends to be more reliable long-term than public ones.
4. Complete 2FA if prompted.

**Option B: Import existing session** (`Apple Account (import JSON)`)

If you already have an authenticated findmy.py account elsewhere (a
standalone script, Macless-Haystack, etc.), you can migrate the session into
HASS without redoing 2FA.

1. Export the account state as JSON from wherever it currently lives:
   ```python
   # in whatever Python context has the AsyncAppleAccount object
   import json
   with open("account.json", "w") as f:
       json.dump(account.to_json(), f)
   ```
   For Macless-Haystack: the account state lives in the container's data
   directory (usually `data/account.json` inside the MH container). Copy it
   out with `docker cp macless-haystack:/app/data/account.json .`
2. `Settings → Devices & Services → Add Integration → FindMy → Apple Account (import JSON)`
3. Upload the JSON. The integration skips the login/2FA dance and stores the
   pre-authenticated session directly.

The importer accepts three JSON shapes for convenience:
- Raw `to_json()` output (dict of tokens/state)
- `{"account": {...}}` (matches some MH exports)
- `{"account_data": {...}}` (matches an HA config-entry export)

**Rate limiting**: each account is polled every 15 min by default. If you add
multiple accounts, the coordinator round-robins between them, effectively
halving/thirding/etc. the poll interval. Every account fetches every device,
so you get up-to-date reports faster without hitting Apple's per-account
limit.

### Tracker device

`Settings → Devices & Services → FindMy → Add Device`, then pick:

| Type | When to use | Input |
|---|---|---|
| Static | Firmware advertises exactly one key (no rotation) | Paste base64 private key |
| Rolling | Real AirTag / FindMy accessory | Upload `.plist` |
| **OpenHaystack rotating** | Firmware rotates through N pre-generated keys | Upload `devices.json` |

For the rotating case, upload the `devices.json` produced when you generated
your key set. The file's first entry is imported with its `privateKey` +
`additionalKeys[]` as one Home Assistant entity. Attribute `key_count` reflects
the total key count.

If your `devices.json` contains multiple tags in one file, only the first is
imported per run; re-add for each additional tag.

## Hardware example: HolyIOT nRF52810

A worked example using a cheap HolyIOT nRF52810 module and the
[heystack-nrf5x](https://github.com/mmilata/heystack-nrf5x) firmware fork.
The firmware source and detailed build notes live in a separate repo:
**https://github.com/coxtor/openhaystack-tag-firmware** (or the `heystack-victor/`
directory of this workspace during local development).

### 1. Generate a key set for a new tag

Use the bootstrap script bundled with the firmware repo:

```bash
./create_tag.sh MYTAG
```

Produces (into `out/MYTAG/`):
- `MYTAG_keyfile` - packed adv keys, fed to the firmware build
- `MYTAG.keys` - human-readable listing (private / adv / hash)
- `MYTAG_devices.json` - **upload this to HASS**
- `MYTAG_nrf52810_xxaa-dcdc_s112_patched.bin` - flashable firmware

Under the hood: 250 fresh ECDSA SECP224R1 keys, packed into the OpenHaystack
patch format, merged with SoftDevice S112 6.1.1.

### 2. Flash

ST-Link V2 + OpenOCD on macOS:

```bash
openocd -f interface/stlink.cfg -f target/nrf52.cfg -c "init; halt; nrf5 mass_erase; program out/MYTAG/MYTAG_nrf52810_xxaa-dcdc_s112_patched.bin verify; reset; exit"
```

### 3. Register in HASS

Upload `out/MYTAG/MYTAG_devices.json` in the OpenHaystack rotating device flow.

### Board notes (this specific HolyIOT variant)

- LED on **P0.30**, active-low (LED between VDD and pin).
- Button on **P0.31**, active-high, **NO INTERNAL PULL** - internal 13 kΩ
  pulldown fights the external series R and the pin never crosses HIGH.
- Wiring: `GPIO(P0.31) → button → R → VDD`.
- Boot pattern: 3 long blinks + 2 short blinks = firmware alive.
- Short press → 30 s find-me fast advertising.
- Double click → force key rotation.
- 2 s hold → mute BLE for 10 min.

Other HolyIOT boards will have different pin assignments. The firmware repo
ships pin-scanner diagnostic firmwares (`pinscan/`) that identify LED and
button pins in ~60 s each.

## Examples

Copy-pasteable snippets for common scenarios. All examples assume a tag
named `f2math`; adjust entity IDs to match yours.

### Entities you get per tag

After adding a tag, HA creates one **device** with these entities:

| Entity | Type | Notes |
|---|---|---|
| `device_tracker.f2math` | tracker | Zone state + battery icon on map |
| `sensor.f2math_latitude` | number (°) | Graphable |
| `sensor.f2math_longitude` | number (°) | Graphable |
| `sensor.f2math_position` | `"lat,lon"` string | For notifications / links |
| `sensor.f2math_battery_level` | enum ok/medium/low/critical | |
| `sensor.f2math_battery` | number (%) | Drives HA's map battery icon |
| `sensor.f2math_battery_voltage` | number (mV) | Opt-in, rough estimate |
| `sensor.f2math_status_counter` | number | Opt-in, diagnostic |
| `binary_sensor.f2math_battery_low` | on/off | For automations |

### Automation: notify when the tag leaves home

```yaml
alias: F2MATH left home
trigger:
  - platform: zone
    entity_id: device_tracker.f2math
    zone: zone.home
    event: leave
action:
  - service: notify.mobile_app_myphone
    data:
      title: F2MATH left home
      message: >-
        Last seen at {{ state_attr('device_tracker.f2math', 'detected_at') }}
      data:
        url: >-
          https://www.google.com/maps/search/?api=1&query={{ states('sensor.f2math_position') }}
```

Prefer the [`tag_zone_change` blueprint](blueprints/) if you don't want to
maintain YAML by hand.

### Automation: notify when battery goes low or critical

```yaml
alias: F2MATH battery low
trigger:
  - platform: state
    entity_id: binary_sensor.f2math_battery_low
    to: "on"
action:
  - service: notify.mobile_app_myphone
    data:
      title: F2MATH battery low
      message: >-
        Battery status: {{ states('sensor.f2math_battery_level') }}
        ({{ states('sensor.f2math_battery') }} %). Time to swap the CR2032.
```

### Automation: warn if no report for 24h

Use the [`tag_stale` blueprint](blueprints/), or roll your own:

```yaml
alias: F2MATH stale
trigger:
  - platform: time_pattern
    minutes: /30
condition:
  - condition: template
    value_template: >-
      {% set d = state_attr('device_tracker.f2math', 'detected_at') %}
      {{ d and (as_timestamp(now()) - as_timestamp(d)) > 24 * 3600 }}
action:
  - service: persistent_notification.create
    data:
      title: F2MATH stale
      message: >-
        No report for {{ ((as_timestamp(now()) -
        as_timestamp(state_attr('device_tracker.f2math', 'detected_at'))) / 3600)
        | round(1) }} h.
```

### Automation: geofence with distance calc

```yaml
alias: F2MATH within 500m of home
trigger:
  - platform: state
    entity_id: sensor.f2math_latitude
condition:
  - condition: template
    value_template: >-
      {% set lat = states('sensor.f2math_latitude') | float(0) %}
      {% set lon = states('sensor.f2math_longitude') | float(0) %}
      {{ distance(lat, lon, 'zone.home') < 0.5 }}
action:
  - service: light.turn_on
    target:
      entity_id: light.living_room
```

Uses HA's built-in `distance()` template (returns km).

### Notification with Google Maps link

Anywhere in a notification message:

```yaml
data:
  message: F2MATH is here.
  data:
    url: >-
      https://www.google.com/maps/search/?api=1&query={{ states('sensor.f2math_position') }}
```

Tap the notification → Google Maps opens at the tag's last known location.

### Lovelace: map with breadcrumb trail

```yaml
type: map
entities:
  - device_tracker.f2math
hours_to_show: 168     # 1 week trail
default_zoom: 14
```

### Lovelace: history graph of lat/lon

```yaml
type: history-graph
entities:
  - sensor.f2math_latitude
  - sensor.f2math_longitude
hours_to_show: 24
```

For fancier plots use the ApexCharts-Card from HACS with two Y-axes.

### Lovelace: entity card with all the info

```yaml
type: entities
title: F2MATH
entities:
  - device_tracker.f2math
  - sensor.f2math_battery
  - sensor.f2math_battery_level
  - binary_sensor.f2math_battery_low
  - type: attribute
    entity: device_tracker.f2math
    attribute: detected_at
    name: Last seen
  - sensor.f2math_position
```

### Multi-tag bulk delete via Developer Tools

```yaml
service: findmy.delete_devices
data:
  filter: openhaystack   # all|openhaystack|static|rolling
```

### Template combining across multiple tags

```yaml
template:
  - sensor:
      - name: FindMy fleet moving
        state: >-
          {{ expand(state_attr('group.findmy_tags', 'entity_id'))
             | selectattr('state', 'ne', 'not_home')
             | list | count }}
```

## Common tasks

### The tag doesn't appear after import

- Poll is up to 15 min after adding a device or restarting HA.
- Check logs for `Coordinator: Updating interval`. If interval is `None`, no
  Apple Account is configured.
- If the account was configured but reports are empty, verify the private key
  in the `devices.json` matches the firmware's advertised keys. Load `.keys`
  file, take the leader `Private key:`, base64-decode 28 bytes.

### Sparse updates despite the rotating type

- A location report only exists if an iPhone was within BLE range of the tag
  *while it was advertising a specific key*. With 250 keys × 43 s each you
  cover ~3 h before repeating. Coverage in a busy area is usually good.
- Rural / no-iPhone areas: fewer reports regardless of integration.

### Rate-limit / 429 errors on the account

- Add a second Apple ID. Both accounts round-robin; poll interval halves.
- Move to a private Anisette server if you're using the integrated one for
  many accounts.

## Increasing update frequency (upstream text)

By default, the integration will only use your account to fetch updates once
per 15 minutes. This is to reduce the risk of being banned by Apple. If you
want to increase the tracker update frequency, add additional Apple accounts.
These accounts will divide the available time; 2 accounts will generate
updates every 7.5 minutes, 3 will update every 5 minutes, etc.

## What's on my roadmap (this fork)

- Bulk-import from a `devices.json` containing multiple tags (single-shot,
  produces N entities).
- Optional import from a running Macless-Haystack instance's API.
- Support for RGB-LED / LIS2DH12 status bytes once the new hardware ships
  (motion-active flag, low-battery flag exposed as HA binary sensors).
