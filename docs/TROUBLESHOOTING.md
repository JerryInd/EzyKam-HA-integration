# Troubleshooting

## Sign-in

### "Unable to reach the Ezykam cloud"
The integration calls `https://web.ezykam.com`. Check outbound HTTPS from the
Home Assistant host, and check for a proxy or firewall blocking it.

### QR code expires before I can scan it
The token lives exactly **300 seconds**. Generate a fresh code and scan within
that window. The Home Assistant flow restarts cleanly after a `timeout` abort.

### "Sign-in failed"
Check Settings → System → Logs for `custom_components.ezykam`. If you see
`USER_SESSION_LOSS`, the session was rejected after login — use the
**Reauthenticate** action on the integration entry.

## Discovery

### "No cameras were found on this account"
* Confirm the cameras appear in the ezyKam+ mobile app under the **same
  account**.
* Shared cameras are included; cameras under a *different* account must be
  shared to you first.
* Homes are addressed by their `gid` — if you have multiple homes and only one
  shows, this is a bug worth reporting with diagnostics attached.

### Only some cameras appear
Offline cameras are still listed (as unavailable). If one is missing entirely,
pull a diagnostics dump and check the `inventory` section.

## Streaming

### Camera shows "unavailable"
The cloud reports it offline (`cloudOnline: false`). Check the camera has power
and internet in the ezyKam+ app.

### Frame is black or takes a long time to appear
The first frame requires a full WebRTC negotiation (typically **1–7 seconds**).
Home Assistant asks for an image on demand, so the very first request may
return `None`; the next one will have a frame.

### Main stream shows nothing but substream works
The camera's main stream is **H.265**, which this pipeline cannot decode. The
integration detects this and falls back to the H.264 substream automatically —
you will see a warning in the logs naming the fallback. Set the option to `sub`
to skip the negotiation.

### High CPU on Raspberry Pi
Each decoded stream costs CPU. Options:
* Set the stream profile to `sub` (640×360).
* Reduce the number of cameras rendered simultaneously.
* Streams idle out after 45 s of nobody asking for a frame.

## MQTT

### Logs show repeated "MQTT connection lost"
The broker is `m1-cube.cppluscloud.com:443` over secure WebSocket. Confirm
outbound WSS (TCP 443) is allowed. Reconnection is automatic with exponential
backoff (1 s → 60 s).

### `NotImplementedError: add_reader` / `add_writer`
Windows-only. paho-mqtt cannot use the default Proactor event loop on Windows.
The integration installs the selector policy automatically
(`core/compat.py`); this should never appear on Linux/HA OS. If you run Home
Assistant on Windows for development, this is expected to be handled — if it is
not, please report it.

## Diagnostics

Attach a diagnostics dump (**Devices & services → CP Plus Ezykam → Download
diagnostics**). It is safe to share: cookies, MQTT credentials, TURN
credentials, `localKey` and presigned URL signatures are all redacted
automatically.

## Reporting a problem

Include:
1. Home Assistant and integration version
2. The diagnostics dump
3. Relevant log lines with debug logging enabled for `custom_components.ezykam`
4. Camera model and whether main or substream is selected

**Never** paste raw session tokens, MQTT passwords, TURN credentials or HAR
files into an issue.
