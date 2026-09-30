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
    """Boolean wrapper around push_reconciliation_to_omg_detailed() - see there."""
    ok, _data, _error = push_reconciliation_to_omg_detailed(
        batch, resolved_pending_parts=resolved_pending_parts, resolved_matches=resolved_matches,
    )
    return ok


def push_reconciliation_to_omg_detailed(batch, resolved_pending_parts=None, resolved_matches=None):
    """
    batch: an ImportBatch (from harness_import.import_or_update_harness_bom,
           resolve_pending.resolve_pending_parts, or the batch a
           human-resolved UnresolvedImportItem belongs to).
    resolved_pending_parts: optional list of {"pending_part_id", "inventree_pk"[, "mpn"]}
           - with "mpn", OMG only resolves the pending part if it's still
           for that MPN (see OMG's _pending_part_rejection).
    resolved_matches: optional list of {"omg_object_type", "omg_object_id",
           "inventree_pk"} — see module docstring point 2.

    Returns (ok, response_json_or_None, error_message_or_None) and never
    raises. For most callers this is a best-effort notification; the
    review queue's pending-part flow uses the response's
    "rejected_pending_parts" to refuse a link OMG didn't accept.
    """
    # Read from the plugin's own settings (what the settings page and the
    # "credentials set" widget use), falling back to Django settings for
    # deployments that configure these via environment instead. Reading
    # django.conf.settings alone meant values entered in the plugin UI were
    # never seen here, so every push silently bailed out as "not configured".
    #
    # The webhook token is a separate credential from the general API token
    # used for reading FROM OMG — OMG's reconciliation endpoint checks it
    # (via hmac.compare_digest against InvenTreeSetupSettings
    # .inbound_webhook_token), not Django/DRF user auth.
    omg_base_url, webhook_token = _get_webhook_credentials()
    if not omg_base_url or not webhook_token:
        logger.warning(
            "OMG webhook credentials not configured (API URL set: %s, webhook token set: %s) "
            "— skipping reconciliation push-back.",
            bool(omg_base_url), bool(webhook_token),
        )
        return False, None, "OMG webhook credentials aren't configured in the plugin settings."

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
        return True, None, None  # nothing to report — don't bother with the round trip

    try:
        resp = requests.post(
            f"{omg_base_url.rstrip('/')}/api/harness/inventree-reconciliation/",
            json=payload,
            headers={"Authorization": f"Token {webhook_token}"},
            timeout=15,
        )
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError:
            data = None
        return True, data, None
    except requests.HTTPError as exc:
        body = (exc.response.text or "")[:300] if exc.response is not None else ""
        logger.warning(
            "OMG rejected reconciliation push-back (HTTP %s): %s",
            exc.response.status_code if exc.response is not None else "?", body,
        )
        return False, None, f"OMG rejected the update (HTTP {exc.response.status_code if exc.response is not None else '?'}): {body}"
    except requests.RequestException as exc:
        logger.warning("Failed to push reconciliation data back to OMG: %s", exc)
        return False, None, f"Couldn't reach OMG: {exc}"


def _get_webhook_credentials():
    """
    Returns (base_url, webhook_token). Plugin settings take priority;
    Django settings are the fallback. Either may be None/empty.
    """
    url = token = None
    try:
        from plugin.registry import registry
        plugin = registry.get_plugin("omg-harness-import")
    except Exception:  # registry unavailable (e.g. during tests/migrations)
        plugin = None

    if plugin:
        url = plugin.get_setting("OMG_HARNESS_API_URL")
        token = plugin.get_setting("OMG_INBOUND_WEBHOOK_TOKEN")

    url = url or getattr(settings, "OMG_HARNESS_API_URL", None)
    token = token or getattr(settings, "OMG_INBOUND_WEBHOOK_TOKEN", None)
    return url, token
