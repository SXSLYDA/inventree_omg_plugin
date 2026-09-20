# Sync with OMG — panel

A small custom panel, shown on a harness's own Part detail page (any
assembly part — see `core.py`'s `get_ui_panels`), with a button that
re-runs the harness import/update for that specific part. Same action
whether it's the very first import or a re-sync after the design
changed in OMG — `import_or_update_harness_bom` is explicitly built to
be safe to call repeatedly (see its own docstring).

Built and compiled the same way as the earlier Mouser search panel —
`inventree-plugin-creator`'s scaffold, then a real `npm run build`,
verified to compile clean on the first try. Not a guess at what would
work.

## What it does

- On load: `GET /plugin/omg-harness-import/batches/latest/?part_pk=...`
  — shows the last sync's timestamp and matched/flagged counts, or
  "Not synced with OMG yet" if this part has never been imported.
- Button: `POST /plugin/omg-harness-import/import-harness/` with this
  part's own IPN as `harness_part_number` — the exact same endpoint the
  original search-and-import flow uses.
- Uses `context.api` (InvenTree's authenticated axios instance) and
  `context.instance`/`context.id` (the current part, provided by
  InvenTree) — no separate auth or part-lookup needed in the panel.

## Rebuilding it

```
cd sync_panel_source
npm install
npm run build
```

Writes straight into `../static/` (see `vite.config.ts`'s output
config). `node_modules/` isn't included in this delivery — regenerate
it with `npm install`, it's fully reproducible from `package.json`/
`package-lock.json`.

**Important — this plugin now has THREE compiled panels sharing one
`static/` directory** (this one, `../so_export_panel_source/`'s Sales
Order parts list panel, and `../import_harness_panel_source/`'s
Import Harness dashboard item). All three build scripts run
`vite build --emptyOutDir`, which wipes the *entire* output directory
before writing — including the other panels' files, since they share
`../static`. Caught this exact mistake while building the second
panel: running its build wiped this one's `Panel.js` until it was
rebuilt too.

**Safe rebuild procedure**: build into a temporary directory first
(edit `vite.config.ts`'s two `dir: '../static'` lines to something like
`dir: '../static_tmp'`, run `npm run build`, copy the output files into
`../static/` alongside the other panels' files, delete the temp
directory, then revert `vite.config.ts` back to `dir: '../static'`).
Do NOT just run `npm run build` directly in any of
`sync_panel_source/`, `so_export_panel_source/`, or
`import_harness_panel_source/` without doing this — it will silently
delete the other two panels' compiled output.

## Why this panel still exists after the Mouser one was removed

The Mouser panel was removed because InvenTree's native `SupplierMixin`
does that same job (search + create) more completely. There's no
equivalent native mechanism for "trigger a custom backend action from a
part's detail page" — re-syncing a specific harness's BOM with an
external system isn't something the Import Part wizard, or any other
built-in InvenTree feature, covers. This one genuinely needs a panel.
