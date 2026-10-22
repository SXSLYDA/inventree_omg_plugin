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
# add this line - pinned to a release tag (see "Releasing and deploying"):
inventree-omg-harness-import @ git+https://github.com/SXSLYDA/inventree_omg_plugin.git@v0.2.0

inventree run invoke update
```

Or let `tools/server-update.sh` do it (it adds or moves the line, backs up,
updates, restarts and checks the installed version) - see below.

`invoke update` installs everything in `plugins.txt` (including
`mouser-lookup` automatically, since it's declared as this plugin's own
dependency in `setup.py`), then runs migrations and collects static
files, all in one step.

After it finishes, go to Settings → Plugins, find OmgHarnessImport, and
activate it.

## Releasing and deploying

Releases are git tags (`vX.Y.Z`). The server's `plugins.txt` pins the plugin
to one, and only a pushed tag deploys - a push to `master` or a branch never
reaches InvenTree on its own.

### Release (Windows / PyCharm)

1. Note what changed under `## Unreleased` in `CHANGELOG.md` as you work
   (say when a release adds a migration).
2. Run `.\build-and-bump.ps1` (patch), `-Bump minor`, `-Bump major` or
   `-Version X.Y.Z`. It builds the panels (`-SkipBuild` if none changed), moves
   setup.py's mouser_shared pin to its newest tag, checks the Python compiles,
   bumps `omg_import_plugin/version.py` and stamps the changelog. It doesn't
   commit, tag or push - it lists what changed and prints the commands.
3. Review the changes, commit them ("Release vX.Y.Z"), then tag that commit
   `vX.Y.Z` - PyCharm: Git -> New Tag; terminal:
   `git tag -a vX.Y.Z -m "Release vX.Y.Z"`.
4. Push the commit **and the tag** - PyCharm: Git -> Push, tick "Push tags";
   terminal: `git push --follow-tags`. The tag is what deploys: a commit
   pushed without it changes nothing on the server.

### What CI does with the tag (`.github/workflows/ci.yml`)

- Every push and pull request: all Python files must compile, and
  `tools/server-update.sh` must pass shellcheck.
- A `vX.Y.Z` tag, after those checks: the tag must match `version.py` and have
  a `CHANGELOG.md` section; a GitHub release is published with that section as
  its notes; and the server is updated to the tag over SSH with
  `tools/server-update.sh`.
- Re-run either for an existing tag: Actions -> CI -> Run workflow ->
  `release_tag`.

The deploy uses the same three secrets the old `deploy.yml` did
(`INVENTREE_HOST`, `INVENTREE_SSH_USER`, `INVENTREE_SSH_KEY`) - see "SSH key
setup" below. Optional:

| Setting | Where | What for |
|---|---|---|
| `INVENTREE_SSH_KNOWN_HOSTS` secret | Secrets | Output of `ssh-keyscan <host>`, so CI checks it's really your server (otherwise it trusts the key it first sees, with a warning) |
| `OMG_PLUGIN_DEPLOY` = `false` variable | Variables | Switch the server deploy off (releases are still published) |
| Required reviewer on the `inventree-production` environment | Settings -> Environments | An "Approve" button before each deploy |

The SSH user must be `root` or have passwordless `sudo` (the update restarts
InvenTree).

### Updating the server by hand

Install the update script once on the server:

```bash
sudo curl -fsSL -o /usr/local/bin/inventree-omg-update \
     https://raw.githubusercontent.com/SXSLYDA/inventree_omg_plugin/master/tools/server-update.sh
sudo chmod +x /usr/local/bin/inventree-omg-update
```

Then:

```bash
sudo inventree-omg-update                  # newest release
sudo inventree-omg-update v0.2.1           # a specific release
sudo inventree-omg-update --check-only     # installed / pinned / newest, change nothing
sudo inventree-omg-update v0.2.0 --allow-downgrade   # roll back
```

It backs up the database and `plugins.txt`, points the plugin's line at the
tag (adding it if missing), runs `inventree run invoke update`, restarts and
confirms the installed version. If the update fails, `plugins.txt` is put back.
Rolling back doesn't undo a newer release's migrations - check the changelog
first.

### SSH key setup (once)

1. Generate a dedicated key pair (not a personal one), outside this repo:

   ```powershell
   ssh-keygen -t ed25519 -f github-deploy-key -N ""
   ```

2. Append `github-deploy-key.pub` to the server user's
   `~/.ssh/authorized_keys` (`root` on this droplet).
3. Repo -> Settings -> Secrets and variables -> Actions -> New repository
   secret: `INVENTREE_HOST` (IP or domain), `INVENTREE_SSH_USER` (`root`),
   `INVENTREE_SSH_KEY` (the whole private key file).

### mouser_shared

`mouser-lookup` is pinned in `setup.py` to a mouser_shared release tag the same
way. Release mouser_shared first (its own `bump-version.ps1`, push the tag);
the next `build-and-bump.ps1` here moves the pin and notes it in the changelog.

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

**Solved for good by release tags**: `plugins.txt` pins
`...inventree_omg_plugin.git@vX.Y.Z`, and each release is a new tag - a new
install link - so pip always reinstalls (see "Releasing and deploying"). The
bare repo URL is what let pip skip updates.

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
