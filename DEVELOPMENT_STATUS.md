# DEVELOPMENT_STATUS

> Living document. Updated at every milestone. **Last update: 2026-11-20.**

## Legend

| Symbol | Meaning |
|---|---|
| ✅ | Complete **and** proven by an executable test / live call |
| 🟡 | Implemented, tests pass, but **not** proven against live service |
| ⬜ | Not started |
| ⚠️ | Known limitation / open risk |

---

## Overall

| Phase | Scope | Status | Proof |
|---|---|---|---|
| 1 | Audit existing prototype | ✅ | `docs/PHASE1_AUDIT.md` |
| 2 | Ezykam authentication | ✅ | 42 unit tests + **live QR login** (session established) |
| 3 | Camera discovery | ✅ | 17 unit tests + **live discovery: 3 cameras** |
| 4 | MQTT | ✅ | 20 unit tests + **live WSS connect + subscribe to `m1-cube.cppluscloud.com`** |
| 5 | WebRTC live streaming | ✅ | **real camera image decoded to PNG** |
| 6 | Multiple cameras | ✅ | **2 cameras concurrently on one MQTT link, both decoded** |
| 7 | Home Assistant integration | ✅ | **executed on real HA 2026.11.2** — flow, entities, camera proxy, diagnostics |
| 8 | Deployment | ✅ | Windows script **run and verified**; RPi + HACS + upgrade + troubleshooting docs |

**Test suite: 114 passing** (`python -m pytest`, ~4 s).
**Windows setup script: executed end-to-end** (venv → deps → tests green).
**Repo initialised; 55 files staged with zero secret material** (`.session/`,
`.har`, `.env`, `.der`, `.png`, `_prototype_extract/` all excluded).

### Phase 7 — executed against real Home Assistant (2026.11.2)

Run in a local HA instance (WSL2, Python 3.14) because the target host exposes
only port 8123 and no SSH/SMB for file deployment:

* Config flow `user -> scan -> confirm` completed over the REST API; the QR was
  rendered from `description_placeholders` and scanned with the ezyKam+ app.
* Config entry created with title `Test Home`, `state=loaded`.
* `ezykam component loaded: True`.
* Six entities, every state correct against reality:

  | Entity | State |
  |---|---|
  | `camera.shop_inside` | `idle` |
  | `camera.shop_outside` | `idle` |
  | `camera.tirlotkar` (shared + offline) | `unavailable` |
  | `binary_sensor.shop_inside_online` | `on` |
  | `binary_sensor.shop_outside_online` | `on` |
  | `binary_sensor.tirlotkar_online` | `off` |

* **Real frames through HA's camera proxy**: 63.7 KB and 61.5 KB JPEGs on the
  *first* request for both cameras.
* **Diagnostics redaction proven**: the dump was checked against the actual
  stored `sid` and `uid` — neither appears.

**Phase 5 gate — satisfied.** A real image of the shop interior was decoded from
the cloud and written to `.session/frames/*.png` (640×360 H.264, 9 video + 10
audio frames, `connectionState=connected`, `iceConnectionState=completed`,
SDP answer in 78 ms, no errors). No local RTSP/ONVIF/IP was used.

**Phase 6 gate — satisfied.** "Front Camera" (sub 640×360) and "Garden Camera"
(main 1920×1080) streamed **concurrently** — both negotiated in 6.5 s over a
**single shared MQTT connection**, demultiplexed by `sessionid`. Each produced
its own PNGs (31 and 4 frames respectively).

**Cloud relay — now PROVEN (this was previously an open risk).** Running the
same stream with `iceTransportPolicy=relay` gathered **only** relay candidates
(`192.0.2.2 typ relay`, no host and no srflx at all) and still connected in
**1.1 s** and decoded 4 frames. Media therefore flowed exclusively through TURN.
The earlier caveat — that the original capture used a LAN host candidate and
proved nothing about relay — is now closed by experiment, not assumption.

---

## ✅ Verified — authentication (Phase 2)

Proven by live calls against `web.ezykam.com`.

* **QR login is the portal's default** (`loginConfig: 0`). Chosen as primary
  because it needs no captcha, never handles the password, and has nothing to
  store but the session.
* Flow: `POST /api/customized/qrcode` → `POST /api/login/exchange`
  (`{"url":"tuyaSmart--qrLogin?token=…"}`) → `POST /api/customized/login/poll`
  every ~3 s.
* **Token layout** (2 live samples + parser tests): `<region:2><created_ms:13><random:26><expires_ms:13><tail:2>`, lifetime exactly **300 s**.
* **Success signal is `result.sid`** — read from the portal's own handler, not
  guessed. Pending payload is `{"domain":{}}`.
* **`s-sid` cookie is the real credential** (82 chars, `httponly`, 3-day
  expiry). The payload `sid` (56 chars) is *not* accepted by the API — sending
  it returns `401 USER_SESSION_LOSS`. **Proven by test:** a session built from
  the payload value failed every authenticated call.
* **`csrf-token` is never issued.** Pre-login responses set only
  `gTyPlatLang`, `locale`, `fast-sid`; the successful poll adds `s-sid`. The
  portal's header builder drops falsy values, so it runs with `csrf-token: ""`.
  Requiring csrf would have rejected every valid session.
* Login payload also yields: `uid`, `username`, `ecode`, and
  `domain.{mobileMqttsUrl,regionCode}`.

## ✅ Verified — discovery (Phase 3)

* `POST /api/customized/common/homeList` → homes keyed by **`gid`/`groupId`**.
* `POST /api/customized/common/roomList` `{"homeId": <gid>}` returns **device
  records, not rooms** — the prototype's assumption was correct. Using the
  home's separate `id` field returns an **empty list** (verified).
* `POST /api/customized/playback/shareList` → devices shared *with* the
  account (carry `shareInfo`, different `ownerId`).
* `POST /api/customized/app/info` (portal's own `getAppInfo`) →
  **`devIdPrefix = "be"`**. Note this is a *different* endpoint from
  `customized/web/app/info`, which returns only `appId/appName/clientId/icon`.
* `Cloud-Type` per device = `cpplus` iff `devId` starts with `devIdPrefix`
  (mirrors the portal's `x()` helper).

**Live inventory:** 3 cameras —
`beaaaaaaaaaaaaaaaaaa01` "Front Camera" (online), `bebbbbbbbbbbbbbbbbbb02`
"Garden Camera" (online), `becccccccccccccccccc03` "Shared Camera" (shared, offline).

---

## ✅ Verified — RTC configuration (Phase 5 input)

`POST /api/customized/rtc/config` `{"devId", "clientTraceId"}` returns:

| Field | Observed |
|---|---|
| `supportsWebrtc` | `true` |
| `protocolVersion` | `"2.3"` (Front Camera), `"2.2"` (Garden Camera) — **per device** |
| `auth` | 44 chars (secret) — top level **and** inside `p2pConfig` |
| `localKey` | 16 chars (secret) |
| `p2pConfig.ices` | `stun:192.0.2.1:3478`, `turn:192.0.2.1:3478` (+ `username`/`credential`, masked) |
| `p2pConfig.motoId` | **changes per request** (`signalings5hz8` → `signalingmjdql`) — a per-session signaling id |
| `skill` | JSON string with per-device stream profiles |
| `supportsPtz`, `supportWebrtcRecord` | `true` |

**Codec mapping read from portal source (not guessed):** `codecType 2 = H.264`,
`codecType 4 = H.265/HEVC`.

| Camera | Main | Sub |
|---|---|---|
| Front Camera | 2304×1296 **H.265** | 640×360 H.264 |
| Garden Camera | 1920×1080 **H.264** | 640×360 H.264 |

⚠️ The research note "main = 1920×1080" matches only the second camera; main
stream geometry and codec are **per device**. H.265 WebRTC is unreliable, so
the substream (always H.264) is the safe default for the first working stream.

---

## ✅ Verified — MQTT + signaling protocol (Phases 4/5)

Reverse-engineered from the portal's `playback` bundle
(`RTCPeerConnection` ×34, `createOffer` ×2, `setRemoteDescription` ×8).

**Credentials** — `POST /api/hybrid/getMsid` → live:
```json
{"ecode": "…", "msid": "<45 chars>", "password": "<32 chars>"}
```

**Connection** (portal source, verbatim):
```js
username = "web_" + msid          // also clientId
password = <password>
url      = `${mobileMqttsUrl.cpplus}/mqtt`   // "m1-cube.cppluscloud.com/mqtt"
connect(`wss://${username}:${password}@${url}`, {username, password, keepalive: 60, clientId: username})
```

**Topics**
* subscribe: `/av/u/{msid}` (qos 1)
* publish:   `/av/moto/{motoId}/u/{devId}` (qos 1)

**Envelope (V2/V3, `pv` = `protocolVersion`)**
```json
{"protocol":302,"pv":"2.3","t":<ms>,
 "data":{"header":{"from":"<msid>","to":"<devId>","sub_dev_id":"",
                    "sessionid":"<uuid-no-dashes>","moto_id":"<motoId>",
                    "seq":0,"rtx":0,"type":"offer","tid":"<uuid>"},
          "msg":{…}}}
```

**Offer `msg`**
```json
{"sdp":"…","auth":"<rtc p2pConfig.auth>","mode":"webrtc",
 "datachannel_enable":false,"token":[<ices>],"stream_type":0,
 "replay":{"is_replay":0}}
```
`stream_type` = `{mainStream:2→0, substream:4→1, otherStream:8→2}`.

**Candidate out:** `{candidate:"a=<candidate>", mode:"webrtc"}` — candidates
whose string contains `.local` (mDNS) are **filtered out**.

**Inbound** (match `protocol === 302` **and** `data.header.sessionid === sessionId`):
* `type:"answer"` → `msg.sdp`
* `type:"candidate"` → `msg.candidate`
* `type:"disconnect"` → `msg.sdk_code === 12` or `msg.info === "refused"`
  means session overflow; otherwise plain disconnect.

**Transceivers:** H.264 → `audio sendrecv` + `video recvonly`.
**Device type:** from `skill.videos[].codecType` for the chosen stream.
**Offer retransmit:** every 3 s, up to 5 attempts.

Other constants confirmed: `/api/jarvis/config`, `/api/jarvis/configV4`,
`/api/jarvis/sd/allocate|close|play|status|list` (the latter is SD-card
replay, not live preview).

---

## ⚠️ Explicitly NOT proven

* ~~Cloud-relayed WebRTC~~ — **now proven** (forced-relay run above).
* Password login (`MD5` + `yrule` captcha, `/api/jarvis/mqtt`) — not
  implemented, not needed; QR login covers the requirement.
* `publicMsid` / public-cloud path — absent on this account; untested.
* `ecode` semantics (seen in login payload and `getMsid`) — unexplained.
* ~~Home Assistant layer not executed~~ — **now executed** on HA 2026.11.2
  (see Phase 7 above). Remaining caveat: it ran on a local instance, not on the
  user's own HA at `homeassistant.local`, because that host exposes no SSH/SMB port
  for file deployment.
* In the WSL test instance only: `assist_pipeline`/`assist_satellite` fail to
  set up (no C++ compiler for `pymicro-vad`/`pyspeex-noise`) and `libturbojpeg`
  is absent. Both are unrelated to this integration — voice components and a
  snapshot-performance fallback respectively.

---

## Environment

* Dev: Windows 11, Python 3.11.9 (code targets 3.12-compatible), git 2.55.
* Deploy target: Raspberry Pi 4, Home Assistant.
* User **scans QR** for live auth; a **camera is online and linked**.
* Session stored at `.session/ezykam_session.json` (git-ignored, owner-only).

## Security posture

* No password is ever handled; QR login only.
* Every secret-bearing type has a custom `__repr__`; `redact()` masks by key
  and scrubs signed-URL query strings (`X-Amz-Signature`, `X-Amz-Credential`).
* `localKey`/`secKey` are **not** persisted in camera models.
* TURN `username`/`credential` are dropped by redaction; only `urls` survive.
* `.gitignore` covers `.session/`, `*.har`, `captures/`.

---

## Log

### 2026-11-20 — Phase 1 complete
* Audited the 9-file prototype; documented 8 defects + 6 blocking gaps.
* Parsed portal JS bundles → API surface enumerated without inventing paths.

### 2026-11-20 — Phase 2 complete
* QR login implemented and **proven live** (user scanned; session established).
* Found and fixed 2 real bugs the live test exposed:
  1. payload `sid` clobbered the authoritative `s-sid` cookie → all calls 401'd;
  2. `is_complete` wrongly required `csrf-token`, which the cloud never issues.
* Added `scripts/qr_login.py`, `scripts/verify_session.py`.

### 2026-11-20 — Phase 3 complete
* Normalised homes/devices/shared devices; dedup + per-home fault tolerance.
* **Live: 3 cameras discovered** (2 online, 1 shared offline).
* Hardened redaction after a test caught an unscrubbed presigned S3 signature.
* Added `scripts/discover_live.py`.

### 2026-11-20 — Phases 4/5 protocol reverse-engineered
* Extracted the full MQTT + WebRTC signaling protocol from the `playback`
  bundle (topics, envelope, offer/candidate/answer shapes, enums).
* Confirmed codec mapping and per-device stream profiles from `rtc/config`.
* Added `scripts/probe_rtc.py`.

### 2026-11-20 — Phase 4 complete
* MQTT over WSS connected live to `m1-cube.cppluscloud.com:443/mqtt`,
  authenticated, subscribed `/av/u/{msid}`, held for 20 s.
* Found and fixed a Windows-only blocker: paho needs `add_reader`/`add_writer`,
  which the Proactor loop lacks → `core/compat.py`.
* Found and fixed a real credential leak: topics contain the `msid`, so
  `describe()` and every log line now scrub it.

### 2026-11-20 — Phase 5 complete (gate satisfied)
* Negotiation works: SDP answer in **47–78 ms**, ICE `completed`.
* Two bugs found by running, not reading:
  1. aiortc tracks are **not** async iterators — must `await track.recv()`.
  2. The camera presents a **345-byte certificate** that `cryptography`'s
     strict DER parser rejects (`ExtraData` at `TbsCertificate.signature_alg`),
     aborting DTLS. Verified it is device-specific, not a version mismatch, by
     capturing the peer DER and comparing parsers. Fixed by validating the SDP
     fingerprint over the raw DER — the hash WebRTC actually specifies, so
     verification is not weakened.
* **Real camera image decoded and saved to PNG.**

### 2026-11-20 — Phase 6 complete (gate satisfied)
* Two cameras streamed concurrently over one shared MQTT connection,
  demultiplexed by `sessionid`; both negotiated in 6.5 s and both produced
  frames (640×360 and 1920×1080).
* Closed the standing relay risk: with `iceTransportPolicy=relay` **only**
  relay candidates were gathered and the stream still connected in 1.1 s and
  decoded frames — proving the TURN path rather than assuming it.

### 2026-11-20 — Phases 7/8 complete
* Config flow written against `data_entry_flow.py` source (3-step QR flow,
  reauth, options), plus coordinator, camera/binary_sensor entities and
  redacted diagnostics.
* Windows setup script executed end-to-end (venv → deps → 104 tests green);
  fixed a PS 5.1 encoding bug (em-dash without BOM) and a native-stderr
  terminating-error bug found only by running it.
* RPi install, troubleshooting, upgrading docs; HACS layout; repo initialised
  with a verified-clean 55-file staging area.

### 2026-11-20 - Phase 7 executed on real Home Assistant 2026.11.2
Provisioned HA under WSL2 (Python 3.14) because the user's HA host exposes only
port 8123 with no SSH/SMB. Running it found **nine** bugs that reading code and
unit tests could not:

1. `av>=12,<19` conflicted with HA's own pin `av==19.0.0` -> `RequirementsNotFound`
   -> HTTP 500 on flow start. Relaxed to `av>=12`.
2. `from .const import DOMAIN` in camera/binary_sensor/diagnostics while the
   constants live in `core/const.py` -> `ModuleNotFoundError` -> `setup_error`.
3. `camera.py` used the deprecated `via_device` tuple (breaks HA 2027.8).
4. `ssl.create_default_context()` and `import aiomqtt` executed **inside the
   event loop**; HA flagged both as blocking. TLS context now built via executor.
5. Remote ICE candidates arrived before the answer; aiortc dropped them with
   `addIceCandidate called without remote description`. Now buffered and flushed.
6. `async_camera_image` returning `None` makes HA raise -> HTTP 500. Now waits
   for the first decoded frame.
7. Cold start exceeded HA's `CAMERA_IMAGE_TIMEOUT = 10`; MQTT is now
   pre-connected during setup, so both cameras serve on the **first** request.
8. Diagnostics leaked `uid` via `entry.unique_id` (the unique_id IS the uid).
   Replaced with a non-reversible fingerprint.
9. `diagnostics.py` imported `fingerprint` from `redact` when it lives in
   `models` -> platform never registered -> unexplained HTTP 404.

Also: a UTF-8 BOM landed in `config_flow.py` from a PowerShell rewrite.

Structural tests were added for the two classes of bug that unit tests missed:
relative-import resolution (now checking **names**, not just modules) and
flow-step/abort-reason translation coverage.

### 2026-11-21 - Real-time streaming (frame rate fix)
Reported as "frame rate is not good". Root causes, found by measuring:

1. `handle_async_mjpeg_stream` was never overridden, so HA's
   `CameraMjpegStream` got `None` and returned **HTTP 502** — live view could
   not play at all.
2. The snapshot path encoded JPEG on a fixed **1 fps** throttle, which is what
   made the view choppy.

Fixes:
* Implemented `handle_async_mjpeg_stream` — a real `multipart/x-mixed-replace`
  stream fed straight from the running WebRTC session.
* Frames are now held **decoded** and encoded **on demand** (the 1 s throttle
  is gone).
* Pacing corrected: sleep out the remainder of the frame interval, then encode
  *that* frame. An intermediate "skip to next frame" version discarded up to a
  full interval per write and measured *worse* (6.7 → 6.2 fps).

Measured on the user's HA (before → after):

| | before | after |
|---|---|---|
| Live view | 502 / 1 fps | **9.2 fps** (`shop_inside`), **9.0 fps** (`shop_outside`) |
| Time to first frame | — | 1.3–3.2 s |

A/B proved the ceiling is the **camera**, not the code: raising the cap from
10 → 30 fps moved throughput 7.0 → 10.1 fps, after which it stopped rising —
i.e. the cameras deliver ~10 fps natively. Cap set to 15 to stay above the
source while bounding CPU/bandwidth on a Pi. Per-frame cost was benchmarked at
**0.5 ms** (1985 fps capability), so encoding is nowhere near the limit.

Also fixed while here: `ApiConfig.base_url` no longer exists in HA 2026
(caused a 500), so the QR URL is now relative; and HA's `SHOW_PROGRESS` UI
renders **no step description**, so the QR could never appear — the scan step
is now a form that serves the image from `/api/ezykam/qr/{nonce}` (unguessable
24-byte capability token, 330 s TTL, indistinguishable 404).
