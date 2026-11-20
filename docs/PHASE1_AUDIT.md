# Phase 1 — Prototype Audit

**Audit date:** 2026-11-20
**Source:** `cpplus-ezykam-prototype.zip` (extracted to `_prototype_extract/`)
**Auditor:** lead developer

The project working directory was **empty** before this archive was supplied. The
archive is therefore the sole prototype under audit. Every file was read in full.

---

## 1. File-by-file inventory

| File | Lines | Status | Notes |
|---|---|---|---|
| `README.md` | 47 | ✅ Working / keep | Honest scope statement. Correctly refuses to overclaim. Documents observed endpoints. |
| `.env.example` | 9 | ⚠️ Incomplete | Requires user to paste `s-sid` + `csrf-token`. Directly violates Phase 2 rule ("no manual browser token paste"). |
| `.gitignore` | 4 | ✅ Working / keep | Covers `.env`, `.venv/`, `__pycache__/`. Needs extension (HAR, recordings, `.storage`). |
| `requirements.txt` | 4 | ⚠️ Incomplete | Only `aiohttp`, `fastapi`, `uvicorn`, `python-dotenv`. Missing MQTT, `aiortc`, `pytest`, `cryptography`. |
| `run_windows.bat` | 16 | ⚠️ Incomplete | Bootstraps venv, opens Notepad for token paste. Replaced by Phase 8 scripts. |
| `ezykam_bridge/__init__.py` | 1 | ✅ Keep | Docstring only. |
| `ezykam_bridge/client.py` | 89 | ✅ **Core value — preserve** | The working API discovery code. Endpoint paths, header set, response envelope handling. |
| `ezykam_bridge/app.py` | 68 | ⚠️ Partial | FastAPI bridge. Correctly strips secrets from RTC config. Several defects (§3). |
| `scripts/inspect_har.py` | 9 | ✅ Keep | Sanitized HAR endpoint inventory printer. Useful, prints no secrets. |

**No test files, no CI, no Home Assistant code, no MQTT code, no WebRTC code exist in the prototype.**

---

## 2. What works and must be preserved

`ezykam_bridge/client.py` encodes verified knowledge:

* **Endpoint paths** (re-confirmed live against `web.ezykam.com` during this audit):
  * `POST /api/customized/common/homeList`
  * `POST /api/customized/common/roomList` (`{"homeId": ...}`)
  * `POST /api/customized/playback/shareList`
  * `POST /api/hybrid/getMsid`
  * `POST /api/customized/rtc/config` (`{"devId", "clientTraceId"}`)
* **Request header set:** `cloud-type`, `cpplus-region-code`, `public-region-code`,
  `origin`, `referer`, `x-requested-with`.
* **Response envelope:** `{result, t, success, status}` with errors carrying
  `errorCode` / `errorMsg`.
* **Secret hygiene in the RTC endpoint** — `auth`, `localKey`, TURN username and
  credential are stripped before responding. This behaviour is carried forward.
* **Honest framing** in the README: the HAR's WebRTC dump proved decoding over a
  *LAN host candidate*, not cloud relay. That caveat is retained in our status docs.

---

## 3. Defects found

### 3.1 Blocking

| # | Defect | Impact |
|---|---|---|
| D1 | **No authentication flow.** Only manual `s-sid`/`csrf-token` paste. | Fails Phase 2 outright. |
| D2 | **No session renewal / expiry handling.** Tokens silently rot. | Integration dies after token TTL. |
| D3 | **No MQTT implementation** despite `mqtt_session()` returning credentials. | Phase 4 absent. |
| D4 | **No WebRTC / media path.** `/health` literally reports `video_streaming: not_implemented`. | Phases 5–6 absent. |
| D5 | **No Home Assistant integration.** No config flow, entities, HACS layout. | Phase 7 absent. |
| D6 | **Zero tests.** No `pytest`, no test directory. | Cannot prove any milestone. |

### 3.2 Correctness bugs

| # | Location | Defect |
|---|---|---|
| B1 | `client.py:48` | `await resp.json()` runs **before** the status check. A non-JSON 4xx/5xx body raises `JSONDecodeError` instead of `EzykamError`, so callers mis-handle gateway failures. |
| B2 | `client.py:87` | `inventory()` treats each `roomList` entry as a **device**. `roomList` is semantically rooms; devices need their own source. Flagged as **ASSUMPTION — unverified**. |
| B3 | `client.py:61` | `rooms()` returns `body.get("result", [])`. If `result` is a dict, iteration yields dict *keys*, silently corrupting inventory. |
| B4 | `app.py:38` | `/api/cameras` fetches `shared_devices()` but never emits them. Shared cameras are unreachable. |
| B5 | `app.py:57` | Assumes `p2pConfig.ices[].urls` shape without a captured RTC response to confirm it. **ASSUMPTION.** |
| B6 | `app.py` (all routes) | Creates a brand-new `ClientSession` per request — no keep-alive, no connection reuse. |
| B7 | `client.py:15` | Constructor hard-requires `sid`+`csrf`, so the object cannot be used to *perform* a login. |
| B8 | `client.py:44` | No timeout granularity, no retry, no backoff, no 401→re-auth trigger. |

### 3.3 Missing relative to the brief

Config flow · account setup · automatic discovery · per-camera entities ·
main/substream selection · online/offline status · reauthentication handling ·
independent stream lifecycle · diagnostics with redaction · HACS packaging ·
Raspberry Pi install docs · upgrade/troubleshooting docs.

---

## 4. New findings from live reverse-engineering (this audit)

The portal's own JavaScript bundles were retrieved and parsed to avoid inventing
any endpoint or payload. **Verified by live call:**

| Operation | Endpoint | Verified |
|---|---|---|
| Region/domain config | `POST /api/customized/config/info` `{"domain":"web.ezykam.com"}` | ✅ live 200 |
| QR token mint | `POST /api/customized/qrcode` (empty body) → `{"result":{"token":"EU<ts>…<expiry>"}}` | ✅ live 200 |
| QR image | `POST /api/login/exchange` `{"url":"tuyaSmart--qrLogin?token=<token>"}` → base64 PNG | ✅ captured from live traffic |
| QR approval poll | `POST /api/customized/login/poll` `{"token":"<token>"}` | ✅ captured from live traffic |
| Password login | `POST /api/password/login` `{countryCode, userName, password(md5), securekey, domain}` | ⚠️ shape verified in JS, **not executed** |
| Captcha bootstrap | `POST /api/jy/init` | ❌ returns `API_OR_API_VERSION_WRONG`; needs the `yrule` captcha SDK |
| App info (auth'd) | `POST /api/customized/web/app/info` | ✅ live 401 `USER_SESSION_LOSS` pre-login (confirms it requires a session) |

**Auth algorithm decoded from the bundle (not invented):**
`password = MD5(whitespace-stripped password)`, `userName` also whitespace-stripped
(`wi()` applies `replace(/\s/g,"")`), and `securekey` comes from the `yrule`
behavioural captcha result (`Ei()` wrapper).

**Decision:** QR login is the primary authentication path. It is the portal's own
default (`loginConfig: 0`, guide text *"Scan QR code with ezyKam+ App"*), it is
fully verified, it requires **no captcha**, and it never handles the account
password — which satisfies the Phase 2 rule against pasting browser tokens.

---

## 5. Technical implementation checklist

See [`IMPLEMENTATION_CHECKLIST.md`](IMPLEMENTATION_CHECKLIST.md) for the
item-by-item checklist derived from this audit, with verified/assumed status
tracked per item.
