# Raspberry Pi 4 installation

Target: **Home Assistant OS** (recommended) or Home Assistant Core in a venv on
Raspberry Pi OS.

The integration is pure Python plus `aiortc`/`av`/`Pillow`. On a Pi 4 the
H.264 decode is done in software, so **prefer the substream** for multi-camera
setups (see Options below).

## Home Assistant OS

1. Install [HACS](https://hacs.xyz/docs/use/download/install/).
2. HACS → ⋮ → **Integrations** → **Add custom repository** →
   `https://github.com/ezykam-ha/ezykam-ha` → category **Integration**.
3. Install **CP Plus Ezykam Cloud**.
4. Restart Home Assistant.
5. Settings → Devices & services → **Add integration** → *CP Plus Ezykam*.
6. Submit, then scan the QR with the ezyKam+ app.

### Dependencies

`aiomqtt`, `aiortc`, `av` and `Pillow` are declared in `manifest.json` and
installed automatically. If the build fails (see Troubleshooting), install them
inside the Home Assistant venv first:

```bash
docker exec -it homeassistant /usr/local/bin/python -m pip install \
    "aiomqtt>=2,<3" "aiortc>=1.6,<2" "av>=12,<19" "Pillow>=10"
```

## Raspberry Pi OS + Home Assistant Core (venv)

```bash
sudo apt-get update
sudo apt-get install -y python3-venv python3-dev git libavformat-dev \
    libavcodec-dev libavdevice-dev libavutil-dev libswscale-dev \
    libopus-dev libsrtp2-dev pkg-config

python3 -m venv /srv/homeassistant
source /srv/homeassistant/bin/activate
pip install homeassistant

mkdir -p /srv/homeassistant/config/custom_components
git clone https://github.com/ezykam-ha/ezykam-ha /tmp/ezykam
cp -r /tmp/ezykam/custom_components/ezykam /srv/homeassistant/config/custom_components/
```

Then start Home Assistant as usual and add the integration from the UI.

## Performance guidance for Pi 4

| Cameras | Recommended profile | Notes |
|---|---|---|
| 1–2 | `main` | Fine on a Pi 4 with H.264 |
| 3+ | `sub` | 640×360 decodes far cheaper |

Streams are **on demand and idle-timed out** (45 s), so a camera nobody is
looking at is not being decoded. Live view starts the stream when Home
Assistant asks for a frame.

## Verifying the install

```bash
# Inside the Home Assistant venv
python -c "import aiomqtt, aiortc, av, PIL; print('deps OK')"
```

Then check **Settings → System → Logs** for `custom_components.ezykam`
messages after adding the integration.

## Uninstalling

Remove the integration from **Devices & services**, then delete
`config/custom_components/ezykam/`. No files are written outside that
directory.
