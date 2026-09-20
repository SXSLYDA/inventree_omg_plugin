"""
Add-on to the omg_import_plugin built earlier (see resolver.py/api.py in
that plugin). This handles the OTHER direction: parts that were picked
from Mouser inside OMG because InvenTree didn't have them yet.

Endpoint: POST /plugin/omg-harness-import/resolve-pending/
body: [{"pending_part_id": 12, "kind": "connector", "mpn": "DT04-12PA",
        "mouser_data": {...}}, ...]   (OMG sends its unresolved PendingPart
                                        list — see pending_parts app,
                                        UnresolvedPendingPartsView)

For each item:
  1. Check InvenTree by exact IPN=mpn first — maybe someone added it
     manually since OMG created the pending record.
  2. If not found, create it from the Mouser data via
     mouser_lookup.prefill.to_inventree_payload() — this is the only
     place Mouser data actually becomes a real InvenTree Part, using
     InvenTree's field names.
  3. If multiple InvenTree candidates match ambiguously, flag via the
     existing UnresolvedImportItem queue rather than guessing.

Response is a list of {"pending_part_id", "status", "inventree_pk"} —
OMG's side (pending_parts.services.resolve_pending_part_references)
uses this to flip Connector/WiringDiagram references from PendingPart
over to the real InventreePartId.
"""

from django.conf import settings

from mouser_lookup import prefill
from part.models import Part

from .models import ImportBatch, UnresolvedImportItem
from .resolver import _find_candidates  # reuse the same exact-IPN-first matching


def resolve_pending_parts(items, category_map=None):
    """
    category_map: optional {kind: inventree_category_pk} — if you have
    ComponentTypesCategories set up (see the earlier inventree_integration
    models fix), pass its mapping here so created parts land in the right
    category instead of uncategorized.
    """
    category_map = category_map or {}
    batch = ImportBatch.objects.create(root_part_number="(pending-part sync)")
    results = []

    for item in items:
        pending_id = item["pending_part_id"]
        kind = item["kind"]
        mpn = item["mpn"]
        mouser_data = item["mouser_data"]

        candidates = _find_candidates(mpn)

        if len(candidates) == 1:
            results.append({"pending_part_id": pending_id, "status": "already_existed", "inventree_pk": candidates[0].pk})
            continue

        if len(candidates) > 1:
            UnresolvedImportItem.objects.create(
                batch=batch, part_number=mpn, quantity=1,
                reason=UnresolvedImportItem.Reason.AMBIGUOUS,
                candidate_pks=[p.pk for p in candidates],
                notes=f"From OMG pending part #{pending_id} ({kind}) — multiple InvenTree matches, needs manual pick.",
            )
            results.append({"pending_part_id": pending_id, "status": "ambiguous", "inventree_pk": None})
            continue

        # Nothing in InvenTree at all — create it from the Mouser data.
        payload = prefill.to_inventree_payload(mouser_data, category_pk=category_map.get(kind))
        part = Part.objects.create(**{k: v for k, v in payload.items() if v is not None})
        results.append({"pending_part_id": pending_id, "status": "created", "inventree_pk": part.pk})

    batch.total_items = len(items)
    batch.matched_items = sum(1 for r in results if r["status"] in ("already_existed", "created"))
    batch.flagged_items = sum(1 for r in results if r["status"] == "ambiguous")
    batch.save()

    return results
