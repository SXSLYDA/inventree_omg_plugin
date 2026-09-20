"""
The "catch to ensure the harness is set correctly in OMG" — after an
import/update runs (harness_import.py), a human resolves a flagged item
(api.py's ResolveItemView), or resolve_pending.py resolves a batch of
pending parts, this pushes several things back to OMG:

1. resolved_pending_parts: [{"pending_part_id", "inventree_pk"}] — so OMG
   can flip its Connector/WiringDiagram rows over from PendingPartId to
   a real InventreePartId (see OMG's pending_parts.services
   .resolve_pending_part_references).

2. resolved_matches: [{"omg_object_type", "omg_object_id", "inventree_pk"}]
   — a unified shape covering BOTH confident auto-links found during a
   harness import (exact/unique-parametric match on ConnectorPartNo/
   ConductorPartNo) AND matches a human makes afterward via
   ResolveItemView (picking from ambiguous candidates, or confirming a
   Mouser-suggested creation). Same shape either way, since OMG applies
   them identically — set InventreePartId on the named connector/wire
   and sync its display fields, regardless of which path resolved it.

3. flags: every UnresolvedImportItem from this batch that traces back to
   a specific OMG connector or wire (has omg_object_type/omg_object_id
   set) — so OMG's own connector/wire forms can show "InvenTree flagged
   this" instead of the person only finding out when they separately
   check InvenTree's review queue.

Fire-and-forget by design: if OMG is unreachable, the import/resolve
that already happened in InvenTree is NOT rolled back — the BOM is
correct either way, this is purely a courtesy notification. A failure
here is logged, not raised.
"""

import logging

from django.conf import settings

import requests

from .models import UnresolvedImportItem

logger = logging.getLogger(__name__)


def push_reconciliation_to_omg(batch, resolved_pending_parts=None, resolved_matches=None):
    """
    batch: an ImportBatch (from harness_import.import_or_update_harness_bom,
           resolve_pending.resolve_pending_parts, or the batch a
           human-resolved UnresolvedImportItem belongs to).
    resolved_pending_parts: optional list of {"pending_part_id", "inventree_pk"}.
    resolved_matches: optional list of {"omg_object_type", "omg_object_id",
           "inventree_pk"} — see module docstring point 2.

    Returns True on success, False on failure (never raises) — this is
    a best-effort notification, not a required step for the import
    itself to be considered successful.
    """
    omg_base_url = getattr(settings, "OMG_HARNESS_API_URL", None)
    # Separate credential from the general API token used for reading
    # FROM OMG — this one is what OMG's own reconciliation endpoint
    # checks (via hmac.compare_digest against InvenTreeSetupSettings
    # .inbound_webhook_token, not Django/DRF auth at all), specifically
    # because it's a different, unauthenticated-by-default endpoint:
    # OMG's own harness-search/BOM-export views use IsAuthenticated
    # against the API Token above, but this endpoint is the one thing
    # InvenTree calls INTO OMG rather than the other way around, so it
    # needs its own secret rather than reusing that one.
    webhook_token = getattr(settings, "OMG_INBOUND_WEBHOOK_TOKEN", None)
    if not omg_base_url or not webhook_token:
        logger.info("OMG webhook credentials not configured — skipping reconciliation push-back.")
        return False

    flags = [
        {
            "omg_object_type": item.omg_object_type,
            "omg_object_id": item.omg_object_id,
            "flag_type": item.reason,
            "message": item.notes,
        }
        for item in batch.items.filter(omg_object_type__isnull=False)
    ]

    payload = {
        "harness_part_number": batch.root_part_number,
        "resolved_pending_parts": resolved_pending_parts or [],
        "resolved_matches": resolved_matches or [],
        "flags": flags,
    }

    if not any([payload["resolved_pending_parts"], payload["resolved_matches"], payload["flags"]]):
        return True  # nothing to report — don't bother with the round trip

    try:
        resp = requests.post(
            f"{omg_base_url.rstrip('/')}/api/harness/inventree-reconciliation/",
            json=payload,
            headers={"Authorization": f"Token {webhook_token}"},
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        logger.warning("Failed to push reconciliation data back to OMG: %s", exc)
        return False
