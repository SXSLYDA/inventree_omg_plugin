# Sales Order Parts List — panel

A small panel on a Sales Order's own detail page with one button:
downloads an XLSX of that order's line items (part number + quantity,
one row each) — matching the flat, single-concept-per-row format of the
reference file provided when this was built.

Built and compiled the same way as the "Sync with OMG" panel —
`inventree-plugin-creator`'s scaffold, then a real `npm run build`,
verified to compile clean.

## Confirmed against InvenTree's current documentation

Followed up and fetched InvenTree's own current docs (dated 2026-08-30)
directly rather than leave the earlier caveats as unresolved guesses:

1. **`target_model == 'salesorder'`** (in `core.py`'s `get_ui_panels`) —
   now confirmed, not just inferred. A real, current official InvenTree
   sample plugin (`plugin/samples/integration/user_interface_sample.py`)
   explicitly checks `target_model == 'purchaseorder'` for a working
   Purchase Order panel, and InvenTree's Report Context documentation
   independently lists `salesorder` itself as a canonical model-type
   keyword (`| salesorder | A Sales Order instance |`). Same naming
   convention, confirmed from two independent current sources.
2. **`order.lines` returning `QuerySet[order.models.SalesOrderLineItem]`**
   — confirmed word-for-word in that same Report Context documentation.
3. **`line.part` / `line.quantity`** — now fully confirmed too, from a
   source better than documentation: a real, independently published,
   working third-party InvenTree plugin
   (`inventree-consolidated-shipment-lines` on PyPI). Its documented
   template code uses `entry.line_item.part.name` where `line_item` is
   explicitly typed as a `<SalesOrderLineItem instance>` in the
   plugin's own docstring, and separately documents `"quantity": <total
   quantity allocated>` on that same object — real production code,
   not an inference from the sibling class. That sibling
   (`PurchaseOrderLineItem`) also got confirmed directly this round,
   from InvenTree's live OpenAPI schema documentation (`part`/
   `quantity`, matching) — both routes converge on the same answer
   independently.

No open caveats left on this feature.

## What it does

- Backed by `sales_order_export.py`'s `SalesOrderPartsListExportView` —
  one row per line item, columns "Name" and "Qty".
- "Name" column shows the part's **IPN**, falling back to `name` only
  if IPN is blank — matching how this whole project treats "part
  number" everywhere else (exact-IPN matching, harness auto-create
  setting `name=IPN`). See the top-level README for the full reasoning
  on why IPN was chosen over `name` here.

## Rebuilding it

```
cd so_export_panel_source
npm install
npm run build
```

**Read `../sync_panel_source/README.md`'s rebuild warning first** —
this panel shares a `static/` output directory with the Sync panel and
the Import Harness dashboard item (three panels total now), and
naively rebuilding any one of them wipes the other two's compiled files.
