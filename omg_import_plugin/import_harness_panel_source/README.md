# Import Harness from OMG — dashboard item

Fills a real gap found on review: `HarnessSearchProxyView` and
`HarnessImportView` (search OMG's harnesses, then import one) existed
as working backend endpoints in `api.py` with **no frontend calling
them anywhere in this project** — grepped every `.tsx`/`.ts` file to
confirm before building this, rather than assume.

## Why a dashboard item, not a panel

`get_ui_panels` scopes to an existing record (`target_model` +
`target_id`) — but importing a harness by part number creates or finds
its *own* Part (`harness_import.py`'s auto-create), completely
unrelated to whatever record you'd be viewing. There's no existing
part, sales order, or category page this naturally belongs on. A
dashboard item has no such scoping requirement.

`get_ui_dashboard_items` itself is confirmed working, not inferred —
fetched directly from InvenTree's own real sample plugin source
(`plugin/samples/integration/user_interface_sample.py`), which
includes a working `sample-dashboard-item` example.

## What it does

- Search box, calls `GET .../harness-search/?q=...` (proxies to OMG's
  own harness search, scoped to whatever the requesting user's OMG
  credentials can see).
- Optional "Category ID" field — only needed if the harness doesn't
  exist in InvenTree yet (`HarnessImportView`'s `category_pk` is
  otherwise ignored on an update). No category picker UI — just a
  plain numeric ID field, kept deliberately simple; look up the
  category's pk from InvenTree's own category page if needed.
- "Import" button per result, calls `POST .../import-harness/` with
  the exact request shape that endpoint expects — checked directly
  against `HarnessImportRequestSerializer` and the view's own
  docstring, not guessed.
- Reports matched/flagged counts from the response, or the error
  message if the import couldn't reach OMG or failed.

## Rebuilding it

```
cd import_harness_panel_source
npm install
npm run build
```

**Read `../sync_panel_source/README.md`'s rebuild warning first** —
this is now the *third* panel sharing one `static/` output directory.
`vite build --emptyOutDir` wipes the whole directory on every build;
build into a temp directory and merge manually, exactly as documented
there, or you'll silently delete the other two panels' compiled files.
