# CP Plus Ezykam Cloud for Home Assistant

[![Add to Home Assistant](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start?domain=ezykam)
[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/docs/use/download/install/)
![Tests](https://img.shields.io/badge/tests-114%20passing-brightgreen)
![Verified on](https://img.shields.io/badge/HA-2026.11.2-blue)

[![Buy Me A Coffee](https://img.buymeacoffee.com/button-api/?text=Buy%20me%20a%20coffee&emoji=&slug=jerryindia&button_colour=FFDD00&font_colour=000000&font_family=Cookie&outline_colour=000000&coffee_colour=FFFFFF)](https://www.buymeacoffee.com/jerryindia)

An unofficial, **cloud-only** Home Assistant integration for CP Plus Ezykam+
cameras. Sign in with a QR code, and every compatible camera on your account is
discovered and streamed automatically.

> **No local access required.** No RTSP, no ONVIF, no camera IP addresses, no
> port forwarding. Everything travels through the Ezykam cloud over HTTPS,
> MQTT-over-WSS and WebRTC.

*Not affiliated with CP Plus. All endpoints are undocumented and may change.*

---

## Why this integration

Most Ezykam/CP Plus integrations push you toward RTSP or ONVIF, which requires
finding each camera's local IP, opening ports, and hoping the firmware exposes
a stream. This one does not: it speaks the same protocol the official
[web.ezykam.com](https://web.ezykam.com) portal uses, which was reverse
engineered from the portal's own JavaScript and verified against live traffic.

## Features

| | |
|---|---|
| **QR sign-in** | Scan with the ezyKam+ app. Your password is never entered or stored. |
| **Automatic discovery** | Homes, owned cameras *and* cameras shared with your account. |
| **Live streaming** | WebRTC → MJPEG at the camera's native frame rate (measured ~9–10 fps). |
| **Main / substream** | Selectable, with automatic fallback when the main stream is H.265. |
| **Online status** | Per-camera `binary_sensor`. |
| **Independent lifecycles** | Each camera starts and stops its own stream; idle streams are reaped. |
| **Reauthentication** | Session expiry triggers HA's native reconnect flow. |
| **Redacted diagnostics** | Secrets are masked before they leave the integration. |

### Verified, not claimed

Everything above was executed against a real Home Assistant 2026.11.2 instance
and a real camera account:

* QR config flow → entry created → **6 entities** with correct online/offline/unavailable states
* **Real frames** through HA's camera proxy (63 KB / 61 KB JPEGs, first request)
* Live MJPEG stream at **9.2 / 9.0 fps**
* Cloud-relayed (TURN) streaming **proven** with `iceTransportPolicy=relay`
* Diagnostics checked against the actual stored `sid`/`uid` — **zero matches**

See [`DEVELOPMENT_STATUS.md`](DEVELOPMENT_STATUS.md) for exactly what is
verified versus assumed.

---

## Installation

### HACS (recommended)

1. HACS → ⋮ → **Integrations** → **Add custom repository**
2. Repository: `https://github.com/JerryInd/Ezykam-HA-integration`, category **Integration**
3. Install **CP Plus Ezykam Cloud**, restart Home Assistant.

### Manual

```bash
cd <your-ha-config>/custom_components
git clone https://github.com/JerryInd/Ezykam-HA-integration.git ezykam
```

Restart Home Assistant.

> The repository root contains `custom_components/ezykam/`; copy that
> directory (not the whole repo) into your `custom_components/`.

## Configuration

1. **Settings → Devices & services → Add integration → CP Plus Ezykam**
2. Select **Submit** — a QR code appears
3. Open the **ezyKam+** app → **Scan** → point it at the code
4. Select **Submit** again

⏱ The QR code is valid for **5 minutes**.

Your account password is never entered and never stored — the QR produces a
session token exactly the way the official web portal does.

### Options

* **Stream profile** — `main` or `sub`
* **Online status refresh** — how often camera availability is re-checked

---

## How it works

```
web.ezykam.com            QR login  →  s-sid session
        │
        ├── homeList / roomList / shareList   → camera discovery
        ├── rtc/config                        → ICE servers, stream profiles
        ├── hybrid/getMsid                    → MQTT credentials
        │
m1-cube.cppluscloud.com   MQTT over WSS (port 443, path /mqtt)
        │   publish  /av/moto/{motoId}/u/{devId}   offer / candidate
        │   subscribe /av/u/{msid}                  answer / candidate
        ▼
   WebRTC (H.264 + PCMU)  →  decoded frames  →  MJPEG for Home Assistant
```

### Repository layout

```
custom_components/ezykam/
├── core/                  # pure protocol library — no Home Assistant imports
│   ├── auth.py            # QR login state machine
│   ├── client.py          # HTTP client + response envelope
│   ├── discovery.py       # homes → devices → normalised cameras
│   ├── mqtt.py            # MQTT over WSS + signaling envelope
│   ├── webrtc.py          # per-camera WebRTC sessions
│   ├── models.py          # session/home/camera value objects
│   ├── redact.py          # secret redaction
│   └── compat.py          # platform shims
├── brand/                 # integration icon (HA branding)
├── config_flow.py         # QR sign-in, reauth, options
├── coordinator.py         # discovery + on-demand stream pool
├── camera.py              # camera entities + MJPEG
├── binary_sensor.py       # online/offline
├── web.py                 # QR image endpoint
└── diagnostics.py         # redacted support dump
tests/                     # 114 tests
```

`core/` imports nothing from Home Assistant, so the protocol layer can be
developed and tested standalone — that is how the test suite runs without a
Home Assistant installation.

---

## Development

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements-dev.txt
python -m pytest              # 114 tests, ~4s
```

Live scripts in [`scripts/`](scripts/) — QR login, discovery, streaming,
frame-rate measurement and diagnostics verification.

## Documentation

| | |
|---|---|
| [Development status](DEVELOPMENT_STATUS.md) | What is verified vs. assumed |
| [Phase 1 audit](docs/PHASE1_AUDIT.md) | Prototype audit and defect list |
| [Implementation checklist](docs/IMPLEMENTATION_CHECKLIST.md) | Milestone tracking |
| [Raspberry Pi install](docs/RASPBERRY_PI_INSTALL.md) | Deployment |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Common failures |
| [Upgrading](docs/UPGRADING.md) | Versions and rollbacks |

## Security

* **No password is ever handled** — QR login only.
* `s-sid`, MQTT and TURN credentials are never logged; every secret-bearing
  type has a custom `__repr__` and all output passes through `core/redact.py`.
* The QR image endpoint uses an unguessable 24-byte capability token with a
  330-second TTL, and returns an indistinguishable 404 for expired/unknown IDs.
* `localKey` / `secKey` are deliberately **not** persisted.
* `.session/`, `*.har`, `*.env` and captures are git-ignored.

## Support this project

If this integration saved you from fighting with RTSP URLs and port
forwarding, you can support its development here:

[![Buy Me A Coffee](https://img.buymeacoffee.com/button-api/?text=Buy%20me%20a%20coffee&emoji=&slug=jerryindia&button_colour=FFDD00&font_colour=000000&font_family=Cookie&outline_colour=000000&coffee_colour=FFFFFF)](https://www.buymeacoffee.com/jerryindia)

## Contributing

Issues and PRs welcome at
[JerryInd/Ezykam-HA-integration](https://github.com/JerryInd/Ezykam-HA-integration).

When reporting a problem, attach the diagnostics dump — it is safe to share.

## License

MIT
