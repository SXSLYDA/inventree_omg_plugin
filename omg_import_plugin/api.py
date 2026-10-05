from django.urls import path

import requests
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .harness_import import import_or_update_harness_bom
from .models import ImportBatch, UnresolvedImportItem
from .omg_credentials import get_omg_credentials
from .reconciliation import push_reconciliation_to_omg, push_reconciliation_to_omg_detailed
from .resolve_pending import resolve_pending_parts
from .resolver import import_harness_bom, resolve_item


# ---------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------

class ComponentInputSerializer(serializers.Serializer):
    part_number = serializers.CharField(max_length=128)
    quantity = serializers.DecimalField(max_digits=12, decimal_places=4, default=1)


class ImportRequestSerializer(serializers.Serializer):
    root_part_number = serializers.CharField(max_length=128)
    components = ComponentInputSerializer(many=True)


class UnresolvedItemSerializer(serializers.ModelSerializer):
    candidates = serializers.SerializerMethodField()

    class Meta:
        model = UnresolvedImportItem
        fields = [
            "id", "batch", "part_number", "quantity", "reason", "candidate_pks", "candidates",
            "omg_object_type", "omg_object_id", "resolution", "resolved_part", "notes", "created_at",
        ]

    def get_candidates(self, obj):
        """
        Resolves candidate_pks into {pk, ipn, name} for display — the
        "Sync with OMG" panel's inline review list shows these as pick
        buttons, so it needs more than a bare pk to show someone.
        """
        if not obj.candidate_pks:
            return []
        from part.models import Part
        parts = Part.objects.filter(pk__in=obj.candidate_pks)
        return [{"pk": p.pk, "ipn": p.IPN, "name": p.name} for p in parts]


class ImportBatchSerializer(serializers.ModelSerializer):
    items = UnresolvedItemSerializer(many=True, read_only=True)

    class Meta:
        model = ImportBatch
        fields = [
            "id", "root_part_number", "root_part", "created_at", "completed_at",
            "total_items", "matched_items", "flagged_items", "items",
        ]


class HarnessImportRequestSerializer(serializers.Serializer):
    harness_part_number = serializers.CharField(max_length=120)
    category_pk = serializers.IntegerField(required=False, allow_null=True)
    # Set when this import was triggered from an EXISTING part's own
    # detail page (the "link this part to OMG" flow) rather than the
    # dashboard's search-and-import — pins the harness to this exact
    # part regardless of whether its name matches harness_part_number,
    # so linking a part the user is already looking at can never
    # accidentally create or match a different part instead.
    target_part_pk = serializers.IntegerField(required=False, allow_null=True)


# ---------------------------------------------------------------------
# BOM-string import (the original workflow: OMG posts a flat component list)
# ---------------------------------------------------------------------

class ImportView(APIView):
    """POST /plugin/omg-harness-import/import/ — see resolver.py for behavior."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ImportRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        batch = import_harness_bom(
            root_part_number=serializer.validated_data["root_part_number"],
            components=serializer.validated_data["components"],
        )
        return Response(ImportBatchSerializer(batch).data, status=status.HTTP_201_CREATED)


REVIEW_CONTEXT_TYPES = {"connector", "wire", "multicore", "accessory", "junction"}
REVIEW_CONTEXT_CHUNK = 500  # OMG's per-request limit (ReviewContextInputSerializer max_length)


def _fetch_review_context(user, items):
    """
    Live OMG details for review items that trace back to an OMG object -
    typed part number, description, label and any Mouser pending part
    (MPN, manufacturer, link). One call to OMG's /api/inventree/review-context/.

    Returns ({(omg_object_type, omg_object_id): context}, error_or_None).
    Never raises: if OMG can't be reached the queue still shows, just
    without the extra details.
    """
    wanted = [
        {"omg_object_type": i.omg_object_type, "omg_object_id": i.omg_object_id}
        for i in items if i.omg_object_type in REVIEW_CONTEXT_TYPES and i.omg_object_id
    ]
    if not wanted:
        return {}, None

    from plugin.registry import registry
    plugin = registry.get_plugin("omg-harness-import")
    omg_base_url, omg_token = get_omg_credentials(user, plugin=plugin)
    if not omg_base_url or not omg_token:
        return {}, "OMG credentials aren't configured, so OMG details can't be shown."
    # OMG accepts up to 500 items per request - send in chunks, and skip
    # duplicates (the same OMG object flagged more than once).
    unique = list({(w["omg_object_type"], w["omg_object_id"]): w for w in wanted}.values())
    rows = []
    try:
        for start in range(0, len(unique), REVIEW_CONTEXT_CHUNK):
            resp = requests.post(
                f"{omg_base_url.rstrip('/')}/api/inventree/review-context/",
                json={"items": unique[start:start + REVIEW_CONTEXT_CHUNK]},
                headers={"Authorization": f"Token {omg_token}"},
                timeout=10,
            )
            resp.raise_for_status()
            rows.extend(resp.json().get("items", []))
    except (requests.RequestException, ValueError) as exc:
        return {}, f"Couldn't load details from OMG: {exc}"
    return {(r["omg_object_type"], r["omg_object_id"]): r for r in rows if r.get("found")}, None


class UnresolvedQueueView(APIView):
    """
    GET /plugin/omg-harness-import/unresolved/ — items awaiting human review.
    GET /plugin/omg-harness-import/unresolved/?part_pk=123 — scoped to one
    harness's own review queue (via its ImportBatch history) — backs the
    "Sync with OMG" panel's inline review list, so resolving a flagged
    item doesn't require leaving InvenTree's own UI for Django admin.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = UnresolvedImportItem.objects.filter(
            resolution=UnresolvedImportItem.Resolution.UNRESOLVED
        ).select_related("batch")

        part_pk = request.query_params.get("part_pk")
        if part_pk:
            qs = qs.filter(batch__root_part_id=part_pk)

        items = list(qs)
        data = UnresolvedItemSerializer(items, many=True).data
        # Per-harness queue (the sync panel): attach OMG's live details to
        # each item so it can be searched/imported without leaving the panel.
        if part_pk:
            contexts, error = _fetch_review_context(request.user, items)
            for row, item in zip(data, items):
                row["omg_context"] = contexts.get((item.omg_object_type, item.omg_object_id))
            return Response({"items": data, "omg_context_error": error})
        return Response(data)


class ResolveItemView(APIView):
    """
    POST /plugin/omg-harness-import/unresolved/<id>/resolve/
    body: {"action": "link"|"created"|"dismiss",
           "part_pk": 123, "notes": "...",
           "pending_part_id": 45, "pending_mpn": "DT04-12PA"}   <- optional

    With pending_part_id (the item is waiting on an OMG Mouser pending
    part): OMG is told first, and checks the pending part still exists,
    is still used, isn't resolved to another part, and is still for
    pending_mpn. Only if OMG accepts is the item resolved here (and the
    BOM line added) - if OMG refuses, nothing is resolved and the reason
    comes back as a 409, so a stale review item can't overwrite OMG.

    After resolving, if the item traces back to a specific OMG
    connector/wire (omg_object_type/omg_object_id set), the resolution
    is pushed straight back to OMG (see reconciliation.py) — this is
    what makes a human's manual pick here actually update the OMG-side
    InventreePartId, not just InvenTree's own review queue.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, item_id):
        item = UnresolvedImportItem.objects.get(pk=item_id)
        action = request.data.get("action")
        part_pk = request.data.get("part_pk")
        notes = request.data.get("notes", "")
        pending_part_id = request.data.get("pending_part_id")

        if pending_part_id and action in ("link", "created") and part_pk:
            ok, omg_data, error = push_reconciliation_to_omg_detailed(
                item.batch,
                resolved_pending_parts=[{
                    "pending_part_id": int(pending_part_id),
                    "inventree_pk": int(part_pk),
                    "mpn": request.data.get("pending_mpn") or "",
                }],
            )
            if not ok:
                return Response({"detail": error or "Couldn't update OMG.", "part_pk": part_pk},
                                status=status.HTTP_502_BAD_GATEWAY)
            rejected = [r for r in (omg_data or {}).get("rejected_pending_parts", [])
                        if r.get("pending_part_id") == int(pending_part_id)]
            if rejected:
                return Response({"detail": f"OMG didn't accept this link: {rejected[0].get('reason')}",
                                 "part_pk": part_pk}, status=status.HTTP_409_CONFLICT)
            try:
                resolve_item(item, action=action, part_pk=part_pk, notes=notes)
            except ValueError as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            data = UnresolvedItemSerializer(item).data
            data["reconciliation_pushed"] = True
            return Response(data)

        try:
            resolve_item(item, action=action, part_pk=part_pk, notes=notes)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        reconciliation_pushed = True
        if item.omg_object_type and item.resolved_part_id:
            reconciliation_pushed = push_reconciliation_to_omg(
                item.batch,
                resolved_matches=[{
                    "omg_object_type": item.omg_object_type,
                    "omg_object_id": item.omg_object_id,
                    "inventree_pk": item.resolved_part_id,
                }],
            )

        data = UnresolvedItemSerializer(item).data
        data["reconciliation_pushed"] = reconciliation_pushed
        return Response(data)


class BatchDetailView(APIView):
    """GET /plugin/omg-harness-import/batches/<id>/"""
    permission_classes = [IsAuthenticated]

    def get(self, request, batch_id):
        batch = ImportBatch.objects.get(pk=batch_id)
        return Response(ImportBatchSerializer(batch).data)


class LatestBatchForPartView(APIView):
    """
    GET /plugin/omg-harness-import/batches/latest/?part_pk=123

    Backs the "OMG Harness" panel — reports both whether this part is
    linked to OMG at all (the same HARNESS_MARKER_PARAM check
    core.py's get_ui_panels uses to decide whether to show this panel
    in the first place — checked again here, server-side, rather than
    trusting the frontend to re-derive it, since the frontend has no
    reliable way to read a plugin setting or a part parameter on its
    own) and the last sync's outcome (when, matched/flagged counts), so
    the panel can render its "link" or "sync" state correctly and show
    something before anyone clicks a button. batch is null if this part
    has never been imported — a normal state, not a failure, for a
    part that might just have been marked manually and not synced yet.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        part_pk = request.query_params.get("part_pk")
        if not part_pk:
            return Response({"detail": "part_pk is required."}, status=status.HTTP_400_BAD_REQUEST)

        from part.models import Part
        from .inventree_native_lookup import get_part_parameter_str

        part = Part.objects.filter(pk=part_pk).first()
        if not part:
            return Response({"detail": f"No part found with pk {part_pk}."}, status=status.HTTP_404_NOT_FOUND)

        from .inventree_native_lookup import HARNESS_MARKER_PARAM
        is_linked = (get_part_parameter_str(part, HARNESS_MARKER_PARAM) or "").strip().lower() == "true"

        batch = ImportBatch.objects.filter(root_part_id=part_pk).order_by("-created_at").first()
        return Response({
            "is_linked": is_linked,
            "batch": ImportBatchSerializer(batch).data if batch else None,
        })


class WorkOnInOmgUrlView(APIView):
    """
    GET /plugin/omg-harness-import/work-on-url/?part_pk=123

    Builds the OMG deep link for "work on this harness" (see OMG's
    omg_deep_link.py) — server-side, so the frontend panel never needs
    to know OMG_HARNESS_API_URL or construct the URL itself, just open
    whatever's returned. Uses this InvenTree part's own IPN, since
    that's what OMG matches harnesses by throughout this whole project.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        part_pk = request.query_params.get("part_pk")
        if not part_pk:
            return Response({"detail": "part_pk is required."}, status=status.HTTP_400_BAD_REQUEST)

        from part.models import Part
        part = Part.objects.filter(pk=part_pk).first()
        # REFACTORED: was `if not part or not part.IPN` — a real
        # screenshot of this project's actual InvenTree "Edit Part" form
        # confirmed IPN is genuinely never used (empty, not required)
        # while the real part number goes into `name` (the actually-
        # required field). This check would have failed for basically
        # every real harness part in this system before this fix, since
        # none of them have IPN set at all.
        identifier = part.name if part else None
        if not part or not identifier:
            return Response({"detail": "Part not found, or has no name set."}, status=status.HTTP_400_BAD_REQUEST)

        from plugin.registry import registry
        plugin = registry.get_plugin("omg-harness-import")
        omg_base_url, _token = get_omg_credentials(request.user, plugin=plugin)  # deep link uses the browser's own OMG session, not this token

        if not omg_base_url:
            return Response({"detail": "OMG Harness isn't configured — set it in plugin settings, or ask an admin to set up your personal OMG credential."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        return Response({"url": f"{omg_base_url.rstrip('/')}/harness/work-on/{identifier}/"})


# ---------------------------------------------------------------------
# Pending parts (Mouser-sourced components not yet in InvenTree)
# ---------------------------------------------------------------------

class ResolvePendingView(APIView):
    """
    POST /plugin/omg-harness-import/resolve-pending/
    body: {"items": [{"pending_part_id", "kind", "mpn", "mouser_data"}, ...]}

    Called after OMG fetches its unresolved PendingPart list — see
    resolve_pending.py. Automatically pushes the resulting pending-part
    resolutions back to OMG afterward (see reconciliation.py) so OMG can
    flip PendingPartId over to a real InventreePartId without a separate
    manual step.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        items = request.data.get("items", [])
        category_map = request.data.get("category_map", {})
        results = resolve_pending_parts(items, category_map=category_map)

        resolved = [
            {"pending_part_id": r["pending_part_id"], "inventree_pk": r["inventree_pk"]}
            for r in results if r["inventree_pk"]
        ]
        reconciliation_pushed = True
        if resolved:
            # This batch has no single harness_part_number (pending parts
            # can span multiple harnesses), so root_part_number is a
            # placeholder — push_reconciliation_to_omg only actually uses
            # resolved_pending_parts here, not per-harness flags.
            from .models import ImportBatch as _Batch
            fake_batch = _Batch(root_part_number="(pending-part sync)")
            fake_batch.items = UnresolvedImportItem.objects.none()
            reconciliation_pushed = push_reconciliation_to_omg(fake_batch, resolved_pending_parts=resolved)

        return Response({"results": results, "reconciliation_pushed": reconciliation_pushed})


# ---------------------------------------------------------------------
# Harness BOM import/update — search a harness, then import
# ---------------------------------------------------------------------

class HarnessSearchProxyView(APIView):
    """
    GET /plugin/omg-harness-import/harness-search/?q=R1300G

    Proxies to OMG's harness search endpoint (see
    omg/harness_addon/harness_search_view.py). Uses the requesting
    InvenTree user's own OMG credentials if they have a personal
    OmgUserCredential set (see omg_credentials.py) — so different
    InvenTree users searching harnesses see whatever OMG's own
    company-scoping shows THEM, not one shared service account's view —
    falling back to the plugin-wide setting otherwise.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        query = request.query_params.get("q", "").strip()

        from plugin.registry import registry
        plugin = registry.get_plugin("omg-harness-import")
        omg_base_url, omg_token = get_omg_credentials(request.user, plugin=plugin)

        if not omg_base_url or not omg_token:
            return Response({"results": [], "error": "OMG Harness credentials aren't configured — set them in plugin settings, or ask an admin to set up your personal OMG credential."})
        if len(query) < 2:
            return Response({"results": [], "error": None})

        try:
            resp = requests.get(
                f"{omg_base_url.rstrip('/')}/api/harness-search/",
                params={"q": query, "limit": 15},
                headers={"Authorization": f"Token {omg_token}"},
                timeout=10,
            )
            resp.raise_for_status()
            return Response({"results": resp.json().get("results", []), "error": None})
        except requests.RequestException as exc:
            return Response({"results": [], "error": f"Could not reach OMG Harness: {exc}"})


class HarnessImportView(APIView):
    """
    POST /plugin/omg-harness-import/import-harness/
    body: {"harness_part_number": "HARN-CAT-R1300G-001", "category_pk": 5}

    The main workflow: creates the harness's own InvenTree part if it
    doesn't exist yet (using OMG's harness description — see
    harness_import.py's module docstring point 6 for why this is safe to
    auto-create, unlike sub-components), pulls the harness's BOM from OMG
    (connectors with contact counts worked out, aggregated wire lengths),
    creates/updates BomItems, and pushes connector/wire-specific flags
    back to OMG afterward — see harness_import.py and reconciliation.py.
    category_pk is optional — only used if the harness part needs
    creating; ignored on an update where it already exists.

    Also serves as the "update" action — re-running this after design
    changes in OMG is the intended way to keep things in sync.

    Uses the requesting InvenTree user's own OMG credentials if they
    have a personal OmgUserCredential set (see omg_credentials.py),
    falling back to the plugin-wide setting otherwise — same as
    HarnessSearchProxyView.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = HarnessImportRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        harness_part_number = serializer.validated_data["harness_part_number"]
        category_pk = serializer.validated_data.get("category_pk")
        target_part_pk = serializer.validated_data.get("target_part_pk")

        from plugin.registry import registry
        plugin = registry.get_plugin("omg-harness-import")
        omg_base_url, omg_token = get_omg_credentials(request.user, plugin=plugin)

        if not omg_base_url or not omg_token:
            return Response(
                {"detail": "OMG Harness credentials aren't configured — set them in plugin settings, or ask an admin to set up your personal OMG credential."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            # inventree_pk lets OMG find the harness through its link to
            # this InvenTree part even after the harness was renamed in OMG
            # (harness_part_number here is this InvenTree part's current,
            # possibly old, name). Ignored by older OMG versions.
            resp = requests.get(
                f"{omg_base_url.rstrip('/')}/api/harness/{harness_part_number}/inventree-bom/",
                headers={"Authorization": f"Token {omg_token}"},
                params={"inventree_pk": target_part_pk} if target_part_pk else None,
                timeout=15,
            )
            resp.raise_for_status()
            omg_bom_data = resp.json()
        except requests.RequestException as exc:
            return Response({"detail": f"Could not reach OMG Harness to fetch BOM data: {exc}"}, status=status.HTTP_502_BAD_GATEWAY)

        # Contacts, blanks and the parameter names all come from OMG with the
        # payload (part_logic / parameter_names) - no plugin settings for them.
        try:
            batch, resolved_matches = import_or_update_harness_bom(
                harness_part_number, omg_bom_data,
                category_pk=category_pk, target_part_pk=target_part_pk,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        reconciliation_pushed = push_reconciliation_to_omg(batch, resolved_matches=resolved_matches)

        data = ImportBatchSerializer(batch).data
        data["reconciliation_pushed"] = reconciliation_pushed
        return Response(data, status=status.HTTP_201_CREATED)


# Mouser search/create is no longer a custom endpoint here — it lives in
# InvenTree's own native "Import Part" wizard now (Parts screen -> Add
# Parts -> Import from Supplier), via mouser_supplier.py's SupplierMixin
# implementation. That's a more complete mechanism (category selection,
# parameter matching, pricing, initial stock) than this endpoint pair
# ever was, so they were removed rather than kept alongside it.


class CredentialStatusView(APIView):
    """
    GET /plugin/omg-harness-import/credential-status/

    Reports whether each credential is actually configured (non-empty)
    — nothing about the value itself, just a boolean. Exists because
    InvenTree's own Plugin Settings page masks every "protected"
    setting identically whether it holds a real value or is
    genuinely empty (confirmed directly: clearing OMG_HARNESS_API_TOKEN
    to blank and reloading that page shows the exact same masked
    placeholder as when a real token is set) — so the settings page
    itself cannot answer "did this actually get configured?" at all.

    This sidesteps that entirely by not going through InvenTree's
    settings-display API in the first place: it's a small view this
    plugin defines itself, reading the real, unmasked value
    server-side via get_setting(), same as anywhere else in this file.
    Only ever returns True/False per key — the actual credential
    values themselves are never included in this response.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from plugin.registry import registry
        plugin = registry.get_plugin("omg-harness-import")

        def is_set(key):
            return bool((plugin.get_setting(key) if plugin else "") or "")

        return Response({
            "omg_harness_api_url": is_set("OMG_HARNESS_API_URL"),
            "omg_inventree_user_token": is_set("OMG_HARNESS_API_TOKEN"),
            "inventree_webhook_token": is_set("OMG_INBOUND_WEBHOOK_TOKEN"),
            "mouser_api_key": is_set("OMG_MOUSER_API_KEY"),
        })


from .sales_order_export import SalesOrderPartsListExportView

urlpatterns = [
    path("import/", ImportView.as_view(), name="omg-import"),
    path("unresolved/", UnresolvedQueueView.as_view(), name="omg-unresolved"),
    path("unresolved/<int:item_id>/resolve/", ResolveItemView.as_view(), name="omg-resolve-item"),
    path("sales-order/<int:order_id>/parts-list-xlsx/", SalesOrderPartsListExportView.as_view(), name="omg-so-parts-xlsx"),
    path("batches/latest/", LatestBatchForPartView.as_view(), name="omg-batch-latest"),
    path("work-on-url/", WorkOnInOmgUrlView.as_view(), name="omg-work-on-url"),
    path("batches/<int:batch_id>/", BatchDetailView.as_view(), name="omg-batch-detail"),
    path("resolve-pending/", ResolvePendingView.as_view(), name="omg-resolve-pending"),
    path("harness-search/", HarnessSearchProxyView.as_view(), name="omg-harness-search"),
    path("import-harness/", HarnessImportView.as_view(), name="omg-import-harness"),
    path("credential-status/", CredentialStatusView.as_view(), name="omg-credential-status"),
]
