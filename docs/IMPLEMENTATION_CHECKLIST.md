# Technical Implementation Checklist

Status legend:

* ✅ **VERIFIED** — proven by a live call or by captured browser traffic.
* 🟡 **ASSUMPTION** — inferred from code/shape, not yet proven. Must not be shipped as fact.
* ⬜ **TODO** — not started.
* 🧪 **TESTED** — executable test passes.
* 🚫 **BLOCKED** — cannot proceed without external input.

---

## Phase 1 — Audit

- [x] 🧪 Extract and read every prototype file — [`PHASE1_AUDIT.md`](PHASE1_AUDIT.md)
- [x] ✅ Re-confirm prototype endpoint paths against live portal
- [x] ✅ Enumerate API surface from portal JS bundles (no endpoint invented)
- [x] 🧪 Unit test: prototype client reproduces verified request envelope

## Phase 2 — Authentication

- [x] ✅ Decode QR login protocol: `qrcode` → `login/exchange` → `login/poll`
- [x] ✅ Confirm `qrcode` returns a live token
- [x] 🟡 Decode password login (`MD5` + `jy/init` securekey) — recorded, **not** primary path
- [ ] ⬜ `AuthSession` value object (sid, csrf, uid, region, expiry)
- [ ] ⬜ QR login state machine with polling + timeout + cancel
- [ ] ⬜ Session persistence/restore + expiry detection
- [ ] ⬜ 401 `USER_SESSION_LOSS` → `SessionExpiredError` → reauth trigger
- [ ] 🧪 Tests: QR state machine, cookie capture, expiry, 401 mapping
- [ ] 🧪 **Live test:** user scans QR, session established (requires user)

## Phase 3 — Camera discovery

- [ ] 🟡 Determine real device source (`roomList` semantics — prototype assumed devices)
- [ ] ⬜ Homes → rooms → devices → shared devices normalization
- [ ] ⬜ Stable camera ID derivation (survives rename/re-home)
- [ ] ⬜ Multi-camera + shared-camera support
- [ ] 🧪 Tests: normalization, ID stability, multi-home fixtures

## Phase 4 — MQTT

- [ ] ✅ Broker known: `m1-cube.cppluscloud.com`, path `/mqtt`, WSS (from research)
- [ ] 🟡 Exact topic/payload formats — **must be reverse-engineered from authorized captures**
- [ ] ⬜ Authenticated MQTT over WSS client
- [ ] ⬜ Subscription management + reconnect/backoff
- [ ] ⬜ Message parsing + structured **redacted** debugging
- [ ] 🧪 Tests: reconnect, parse, redaction

## Phase 5 — WebRTC (one camera)

- [x] ✅ Protocol 2.2, H.264 / PCMU, main+sub stream, cloud STUN/TURN (from research)
- [x] ⚠️ Browser diagnostics proved decoding over a **LAN host candidate only**
- [ ] ⬜ Signaling flow reproduction
- [ ] ⬜ Dynamic ICE config from `rtc/config`
- [ ] ⬜ ICE/SDP negotiation with `aiortc`
- [ ] ⬜ **Decoded frame validation**
- [ ] 🧪 **Gate:** real camera image delivered to a test player *(camera is available)*

## Phase 6 — Multiple cameras

- [ ] ⬜ Independent sessions, no event-loop blocking
- [ ] ⬜ Cleanup + reconnection
- [ ] 🧪 **Gate:** two cameras streaming simultaneously

## Phase 7 — Home Assistant

- [ ] ⬜ HACS layout (`hacs.json`, `manifest.json`)
- [ ] ⬜ Config flow (QR login step) + reauth flow
- [ ] ⬜ Coordinator: discovery + online/offline
- [ ] ⬜ Camera entities, main/substream selection
- [ ] ⬜ Independent stream lifecycle
- [ ] ⬜ Diagnostics with secrets redacted
- [ ] 🧪 Tests: config flow, entity creation, diagnostics redaction

## Phase 8 — Deployment

- [ ] ⬜ Windows setup scripts
- [ ] ⬜ Raspberry Pi install instructions
- [ ] ⬜ Automated test suite green
- [ ] ⬜ Upgrade + troubleshooting docs
- [ ] ⬜ `DEVELOPMENT_STATUS.md` current
