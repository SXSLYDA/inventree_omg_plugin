# OMG Harness Import Plugin (InvenTree)

Imports/updates harness BOMs from OMG Harness, reconciles components
against InvenTree, and integrates Mouser into the native Import Part
wizard.

Depends on [`mouser-lookup`](https://github.com/SXSLYDA/mouser_shared),
a separate, framework-agnostic package shared between this plugin and
OMG Harness itself.

## Mixins used

- **AppMixin** — ships this plugin's own DB models (`ImportBatch`,
  `UnresolvedImportItem`, `OmgUserCredential`) as a real Django app.
- **UrlsMixin** — exposes `api.py`'s urlpatterns under
  `/plugin/<slug>/...`.
- **SettingsMixin** — renders the plugin settings screen in InvenTree's
  own Settings → Plugins UI.
- **MouserSupplierMixin** (extends `SupplierMixin`) — plugs Mouser into
  InvenTree's native "Import from Supplier" wizard.
- **UserInterfaceMixin** — three custom panels (sync review queue,
  harness import, sales order export — see below).

## Required settings

Set these in Settings → Plugins → OmgHarnessImport once installed:

| Setting | Purpose |
|---|---|
| `OMG_HARNESS_API_TOKEN` | Service-user token for **reading FROM OMG** (harness search, BOM export). Get this from OMG. |
| `OMG_INBOUND_WEBHOOK_TOKEN` | **Separate** credential OMG checks when this plugin **pushes reconciliation data back**. Copy the exact value from OMG's own InvenTree Setup page's "Inbound Webhook Token" field. **Do not reuse the API Token here** — they authenticate two different directions, and OMG will reject a mismatched value. |
| `OMG_MOUSER_API_KEY` | Mouser Electronics API key, for the supplier integration. |

## Building the frontend panels

Each of the three panels (`sync_panel_source/`, `so_export_panel_source/`,
`import_harness_panel_source/`) is a separate Vite/React project that
compiles into one shared `omg_import_plugin/static/` folder. Compiled
output is **not** committed to this repo — build it yourself:

```bash
cd omg_import_plugin/sync_panel_source
npm install
npm run build

cd ../so_export_panel_source
npm install
npm run build

cd ../import_harness_panel_source
npm install
npm run build
```

Build order doesn't matter, and none of these dependencies need a C/C++
compiler (Vite/esbuild/Biome all ship pre-built platform binaries).

After building, `omg_import_plugin/static/` should contain six `.js`
files (each panel's hashed and non-hashed build) plus their `.js.map`
sourcemaps.

**Antivirus note**: freshly-built, unsigned JS bundles like these
sometimes trigger a false-positive heuristic detection (seen: Windows
Defender `Trojan:Win32/MalUri.A!cl`). Verified by rebuilding
independently and checking every URL embedded in the output — the only
one present is the standard SVG XML namespace
(`http://www.w3.org/2000/svg`), used in virtually every web app with
icons. If Defender flags it, submit to
[Microsoft's file-submission portal](https://www.microsoft.com/en-us/wdsi/filesubmission)
rather than assuming it's real; it typically clears within a day or two.

## Installing on InvenTree

`setup.py` declares this as a real pip package (`inventree-omg-harness-import`),
installable via a git URL since it isn't published to PyPI:

```
inventree-omg-harness-import @ git+https://github.com/SXSLYDA/inventree_omg_plugin.git
```

### For a DigitalOcean one-click / "package installer" deployment

This deployment type uses InvenTree's official **package installer**,
not Docker — confirmed by checking DigitalOcean's own marketplace
listing ("makes use of the installer"). This changes several things
from the Docker-oriented instructions in InvenTree's own docs:

- **Base path**: `/opt/inventree`
- **Config file**: `/etc/inventree/config.yaml`
- **Plugin list**: `/etc/inventree/plugins.txt` (not under
  `/opt/inventree/data/` like Docker docs describe)
- **Python venv**: `/opt/inventree/env`
- **Invoke commands must be prefixed** with `inventree run invoke`, not
  called bare — e.g. `inventree run invoke update`, `inventree run
  invoke migrate`. Bare `invoke` isn't on the shell's PATH; it lives
  inside the dedicated venv above.

**Recommended install method: edit `plugins.txt` directly**, rather
than using the "Install Plugin" web form. In testing, the web form's
install did not reliably persist into `plugins.txt` — the plugin would
appear briefly (or not at all after installing) then vanish entirely
after any container/service restart, since a restart re-derives the
active plugin set from this file rather than whatever the last web-form
click did:

```bash
nano /etc/inventree/plugins.txt
# add this line:
inventree-omg-harness-import @ git+https://github.com/SXSLYDA/inventree_omg_plugin.git

inventree run invoke update
```

`invoke update` installs everything in `plugins.txt` (including
`mouser-lookup` automatically, since it's declared as this plugin's own
dependency in `setup.py`), then runs migrations and collects static
files, all in one step.

After it finishes, go to Settings → Plugins, find OmgHarnessImport, and
activate it.

## Known issues and how they were solved

These were all discovered the hard way against a live DigitalOcean
deployment. Recorded here so a future fix doesn't have to rediscover
them.

### 1. `AppMixin` models require an explicit `app_label`

Symptom: `Model class omg_import_plugin.models.ImportBatch doesn't
declare an explicit app_label and isn't in an application in
INSTALLED_APPS`, repeating every time the plugin tries to initialize.

This is confirmed, documented InvenTree behavior, not a bug —
[inventree/InvenTree#3588](https://github.com/inventree/InvenTree/issues/3588)
was closed as "wontfix." Even with `AppMixin` correctly registering the
plugin's directory into `INSTALLED_APPS`, every model still needs
`app_label` set explicitly in its own `Meta` class, since Django can't
infer it any other way for a plugin-provided app. All three models in
`models.py` have this set — if this error reappears after a future
change, check that any *new* model added also has it.

### 2. Vite output folder mismatch and shared-folder overwrites

`import_harness_panel_source/vite.config.ts` used to point at
`../static_output` while the other two panels pointed at `../static`.
Since `setup.py`'s `package_data` only ships `static/*`, that panel's
build was silently never included in the installed package at all.

Separately, `sync_panel_source` and `so_export_panel_source` share
`../static` as their output folder, and each panel's build script ran
`vite build --emptyOutDir` — which wipes the *entire* target folder
before writing, deleting whichever panel built there most recently.

Fixed by pointing all three panels at `../static` and removing
`--emptyOutDir` from every panel's `build` script in `package.json`.
Verified by deleting `static/` entirely and rebuilding all three
back-to-back — all six files (three panels × hashed/non-hashed) now
coexist correctly regardless of build order.

### 3. pip silently skips reinstalling an unchanged version number

Symptom: pushed a genuine code fix (e.g. #1 above) to GitHub, ran
`inventree run invoke update`, and the *exact same* error kept
appearing — even though the fix was confirmed present on GitHub itself.

Root cause: `setup.py`'s version (`0.1.0`) never changed between the
broken and fixed commits. pip tracks installed packages primarily by
name+version, not by content — when it sees the same version already
installed, it can skip actually copying the new files, even though the
underlying git commit changed. Confirmed directly: `grep app_label` on
the actually-installed file (found via `find / -path
'*omg_import_plugin/models.py'`) came back empty, despite GitHub
showing the fix was there.

Fix: force a real reinstall, bypassing the version-match skip:

```bash
/opt/inventree/env/bin/python3 -m pip install --force-reinstall --no-deps \
  git+https://github.com/SXSLYDA/inventree_omg_plugin.git
```

**Going forward**: either bump the version number in `setup.py` on
every fix, or remember to use `--force-reinstall` — a plain `inventree
run invoke update` will not pick up a code change on its own if the
version string didn't change.

### 4. Two separate credentials, easy to conflate

`OMG_HARNESS_API_TOKEN` (outbound, reading from OMG) and
`OMG_INBOUND_WEBHOOK_TOKEN` (inbound, OMG's reconciliation webhook
checking this plugin's push) are unrelated values authenticating
opposite directions. An earlier version of OMG's own settings-page UI
copy incorrectly told users to paste the webhook token into the field
for the API token — since fixed on the OMG side, but worth remembering
if a similar mix-up resurfaces: these must never share a value.

## Verifying a working install

```bash
# Confirm the fix/version actually installed:
grep -n 'app_label' /opt/inventree/env/lib/python3.9/site-packages/omg_import_plugin/models.py

# Confirm plugins.txt has the right line:
cat /etc/inventree/plugins.txt
```

Settings → Plugins → OmgHarnessImport should show `Active: Yes`, a real
`Installation Path` (not `None`), and populated Description/Author/
Version fields — all `None` with `Active: Yes` indicates a stale
plugin-config database record from an earlier broken install; re-running
`inventree run invoke update` after a genuine reinstall (see #3 above)
should refresh it.
