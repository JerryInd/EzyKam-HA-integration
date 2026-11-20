# Upgrading

## From HACS

HACS handles upgrades: update the repository, then restart Home Assistant.
Config entries are preserved.

## From a manual install

```bash
cd config/custom_components
mv ezykam ezykam.bak
# copy the new ezykam/ directory in
# restart Home Assistant, then remove ezykam.bak once verified
```

## Configuration entry schema

| Version | Change |
|---|---|
| 1 | Initial: `session` + `region` in entry data |

Migrations are added here as the schema evolves. `VERSION` / `MINOR_VERSION` in
`config_flow.py` track the entry schema; Home Assistant calls
`async_migrate_entry` when it is missing.

## Rolling back

Config entries are forward-compatible within a major version. To roll back:

1. Stop Home Assistant.
2. Restore the previous `custom_components/ezykam/`.
3. Start Home Assistant.

If the entry schema changed, the older version will report an unsupported
version and you will need to remove and re-add the integration (you will be
asked to scan a QR code again — no other reconfiguration is needed).

## Data written by this integration

| Location | Contents | Safe to delete? |
|---|---|---|
| Config entry (`entry.data`) | Cloud session tokens | Yes — triggers reauthentication |
| Config entry (`entry.options`) | Stream profile, refresh interval | Yes — resets to defaults |
| `.session/` (dev scripts only) | Local test session + PNGs | Yes — never used by HA |

Nothing is written outside `config/custom_components/ezykam/` and the config
entry storage.
