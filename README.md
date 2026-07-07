# FindMy - Home Assistant integration

Experimental custom integration to provide device tracker entities for FindMy Network-enabled devices.

**This fork** adds a third device type on top of upstream: `openhaystack`. It accepts
the `devices.json` format that OpenHaystack / Macless-Haystack / this project's
`generate_keys.py` produce (one `privateKey` leader plus an `additionalKeys[]`
array) and tracks a firmware with a rotating pre-generated key set as a single
Home Assistant entity.

Why: upstream's `static` type only tracks a single key, so a rotating-firmware
tag (~250 keys, ~43 s per key = ~3 h cycle) only reports location during ~0.4 %
of its runtime. The `openhaystack` type fetches reports for every key in the
set in a single Apple API call and surfaces the freshest one, giving continuous
coverage.

## Setting up

1. Add this repository to HACS and install the `FindMy` integration.
2. Enable the integration. You must add at least two 'devices': one Apple Account and one tracker device.
   1. When adding an account, you will need to specify an anisette server. You can use a public one, but it
      might start throwing errors after a while, so private servers are preferred. Google is your friend.
   2. When adding a tracker, choose the type that matches your firmware:
      - `Static` — a single-key OpenHaystack tag (paste the private key).
      - `Rolling` — a real AirTag or accessory using Apple's rolling protocol (upload the .plist).
      - `OpenHaystack rotating` — a firmware that iterates a pre-generated key list (upload devices.json).
3. Enjoy!

## Increasing the update frequency

By default, the integration will only use your account to fetch for updates once per 15 minutes. This is to
reduce the risk of being banned by Apple. If you want to increase the tracker update frequency, it is possible
to add additional accounts. These accounts will divide the available time; 2 accounts will generate updates every
7.5 minutes, 3 will update every 5 minutes, etc.
