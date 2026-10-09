"""
The "search a harness part number, select import" workflow, plus the
"update in case I make changes in the design tool" re-sync case — both
go through this same function, since update is just import run again.

Design decisions worth knowing about before you wire this in:

1. Pending parts (OMG components not yet in InvenTree) are FLAGGED, not
   silently skipped or auto-created here. This function's job is to
   build the harness's BOM from what's ALREADY resolvable in InvenTree —
   resolving pending parts is resolve_pending.py's job (run that first,
   or this will flag every pending connector/conductor every time).

2. Contacts and blanks are worked out by OMG (its components/wire_ends.py:
   each wire end's contact from the connector's related parts, by wire
   size and cavity group, one per pin; blanks from the Cavity Layout) and
   arrive finished in payload["part_logic"] - this file adds those BOM
   lines and flags OMG's issues (_apply_omg_part_logic). It no longer
   chooses contacts or blanks itself, and has no parameter-name settings:
   OMG sends its mapping in payload["parameter_names"].

3. Re-running this on a harness that's already been imported will
   update quantities on existing BomItems (via update_or_create) but
   will NOT delete BomItems for parts that dropped out of the current
   OMG design — those get flagged instead. Auto-deleting a BOM line is
   a bigger, less reversible action than updating a quantity, so a
   human confirms removal rather than this silently pruning InvenTree's
   BOM out from under someone who might have added something manually
   there in the meantime.

4. Every flag traceable to a specific OMG connector or wire carries
   omg_object_type/omg_object_id (see models.py) — this is what lets
   reconciliation.py push a clean "fix this connector" signal back to
   OMG afterward, instead of OMG having to parse a note string.

5. NOTHING IS EVER AUTO-CREATED HERE, AND NO MOUSER LOOKUP HAPPENS
   DURING IMPORT EITHER. Connectors and conductors both try an exact
   part-number match first (ConnectorPartNo / ConductorPartNo), then —
   conductors only — a parametric fallback by ConductorSize narrowed by
   type/colors. A unique match auto-links (confident, no ambiguity).
   Anything else — multiple candidates, or nothing found at all — is
   ALWAYS flagged for a human, never guessed. This matters specifically
   because a parametric spec like "white 1.5mm wire" can easily match
   more than one real InvenTree part — auto-picking one, or worse,
   auto-creating a new "white 1.5mm wire" part when one or more already
   exist, would silently produce duplicate/wrong BOM lines.

   When nothing matches, the flag just says so and points at InvenTree's
   own native "Import Part" wizard (Parts screen -> Add Parts -> Import
   from Supplier -> Mouser) — see mouser_supplier.py — rather than this
   code searching Mouser itself and attaching a suggestion to review.
   That search-and-attach approach was tried and deliberately dropped:
   Mouser lookups belong in the part-creation flow a human is already
   in control of, not bundled into an unattended batch import.

6. THE ONE EXCEPTION: the harness's own root part auto-creates if it
   doesn't exist yet in InvenTree (using OMG's harness_description).
   "Search a harness part number, select import" is meant to work as a
   single continuous action — not "go create the harness's own part
   manually first, then separately come back and run this." The
   identifying data (harness_part_number) is exact, and it came from a
   human explicitly searching for and selecting THIS harness (see
   HarnessSearchProxyView), the same reasoning that already justifies
   exact-part-number auto-linking for connectors/conductors. This is
   different from Mouser/sub-component creation, which still always
   requires the native Import Part wizard and a human's confirmation.

7. Every harness part gets an explicit "OMG Harness" marker parameter
   set (see _mark_as_omg_harness), on creation and on every successful
   sync. This is what core.py's get_ui_panels() actually checks before
   showing the "Sync with OMG" panel — not just whether a part is an
   assembly, which would show it on any unrelated assembly that has
   nothing to do with OMG. Plain components (connectors, conductors,
   contacts, blanks) created via the Mouser Import Part wizard never
   get this marker OR assembly=True, so the panel correctly never
   appears on their pages either.
"""

from collections import defaultdict

from django.db.models import Q

from part.models import Part

from . import inventree_native_lookup as native
from .models import ImportBatch, UnresolvedImportItem
from .resolver import _find_candidates

def _mark_as_omg_harness(part):
    """
    Sets the OMG-harness marker parameter to "true" on a part — this is
    what core.py's get_ui_panels() actually checks before showing the
    "Sync with OMG" panel, instead of just checking `assembly`, so the
    panel never appears on an unrelated assembly that has nothing to do
    with OMG. Called every time a harness is created or successfully
    synced (idempotent — safe to call repeatedly, just ensures the
    parameter exists and is set).
    """
    native.set_part_parameter(
        part, native.HARNESS_MARKER_PARAM, "true",
        description="Set by the OMG Harness Import plugin — marks a part as an OMG-managed harness.",
    )

NOT_IN_INVENTREE_MESSAGE = (
    "'{part_no}' isn't in InvenTree. Create it via Parts -> Add Parts -> "
    "Import from Supplier -> Mouser (search by this part number there), "
    "then re-run this import."
)
NOT_IN_INVENTREE_WITH_DESCRIPTION_MESSAGE = (
    "'{part_no}' isn't in InvenTree ({description}). Create it via Parts -> Add Parts -> "
    "Import from Supplier -> Mouser (search by this part number there), "
    "then re-run this import."
)
SUB_HARNESS_NOT_IMPORTED_MESSAGE = (
    "'{part_no}' is a linked sub-harness that hasn't been imported into InvenTree "
    "yet — search and import it as its own harness first (it's not a purchasable "
    "component, so the Mouser wizard won't help here), then re-run this import."
)


def _sync_harness_identity(batch, harness_part, harness_part_number, harness_description):
    """
    Keep the InvenTree harness part's name and description in step with
    OMG on EVERY sync, not only when the part is first created. OMG's
    PartNumber is the authoritative harness identifier (see OMG's
    reconciliation view: "InvenTree's Part name/IPN gets set to match
    it, never the other way"), so this runs OMG -> InvenTree - the
    opposite direction to connector/wire names, which follow InvenTree.

    Description is only updated when OMG actually has one (a blank OMG
    description never wipes InvenTree's). If the rename is refused - most
    likely another InvenTree part already has that name - nothing is
    changed and it's flagged on this batch instead of failing the sync.
    """
    from django.core.exceptions import ValidationError
    from django.db import IntegrityError, transaction

    updates = {}
    new_name = (harness_part_number or "").strip()
    if new_name and harness_part.name != new_name:
        updates["name"] = new_name
    new_description = (harness_description or "").strip()
    if new_description and (harness_part.description or "") != new_description:
        updates["description"] = new_description
    if not updates:
        return

    original = {field: getattr(harness_part, field) for field in updates}
    try:
        with transaction.atomic():  # savepoint - a refused rename can't break the rest of the sync
            for field, value in updates.items():
                setattr(harness_part, field, value)
            harness_part.save()
    except (ValidationError, IntegrityError) as exc:
        for field, value in original.items():
            setattr(harness_part, field, value)
        UnresolvedImportItem.objects.create(
            batch=batch, part_number=new_name or harness_part.name, quantity=0,
            reason=UnresolvedImportItem.Reason.NOT_FOUND,
            notes=(f"Couldn't update this harness part to match OMG "
                   f"({', '.join(f'{k}: {original[k]!r} -> {updates[k]!r}' for k in updates)}): {exc}. "
                   f"Most likely another InvenTree part already uses that name - rename or merge it, "
                   f"then re-run the sync."),
        )


def _flag_connector(batch, connector_id, label, message, candidate_pks=None):
    UnresolvedImportItem.objects.create(
        batch=batch, part_number=label or "Connector", quantity=1,
        reason=UnresolvedImportItem.Reason.AMBIGUOUS if candidate_pks else UnresolvedImportItem.Reason.NOT_FOUND,
        notes=message, candidate_pks=candidate_pks or [],
        omg_object_type=UnresolvedImportItem.OmgObjectType.CONNECTOR, omg_object_id=connector_id,
    )


def _flag_wire(batch, wire_id, message, candidate_pks=None, wire_no=None):
    """
    wire_no vs wire_id: wire_id is OMG's own database pk for this wire
    (used below as omg_object_id, which reconciliation.py needs to
    identify the exact record) - never shown to people. wire_no (OMG's
    WireNo, now sent in the BOM payload and pin specs) is what's
    displayed; if it's missing, just "Wire" - never the pk.
    """
    UnresolvedImportItem.objects.create(
        batch=batch, part_number=f"Wire {wire_no}" if wire_no not in (None, "") else "Wire", quantity=1,
        reason=UnresolvedImportItem.Reason.AMBIGUOUS if candidate_pks else UnresolvedImportItem.Reason.NOT_FOUND,
        notes=message, candidate_pks=candidate_pks or [],
        omg_object_type=UnresolvedImportItem.OmgObjectType.WIRE, omg_object_id=wire_id,
    )


def _flag_multicore(batch, multicore_id, name, message):
    UnresolvedImportItem.objects.create(
        batch=batch, part_number=name or "Multicore cable", quantity=1,
        reason=UnresolvedImportItem.Reason.NOT_FOUND, notes=message,
        omg_object_type=UnresolvedImportItem.OmgObjectType.MULTICORE, omg_object_id=multicore_id,
    )


def _flag_accessory(batch, accessory_id, kind, message, candidate_pks=None):
    UnresolvedImportItem.objects.create(
        batch=batch, part_number=kind or "Accessory", quantity=1,
        reason=UnresolvedImportItem.Reason.AMBIGUOUS if candidate_pks else UnresolvedImportItem.Reason.NOT_FOUND,
        notes=message, candidate_pks=candidate_pks or [],
        omg_object_type=UnresolvedImportItem.OmgObjectType.ACCESSORY, omg_object_id=accessory_id,
    )


def _flag_junction(batch, junction_id, name, message, candidate_pks=None):
    UnresolvedImportItem.objects.create(
        batch=batch, part_number=name or "Junction", quantity=1,
        reason=UnresolvedImportItem.Reason.AMBIGUOUS if candidate_pks else UnresolvedImportItem.Reason.NOT_FOUND,
        notes=message, candidate_pks=candidate_pks or [],
        omg_object_type=UnresolvedImportItem.OmgObjectType.JUNCTION, omg_object_id=junction_id,
    )


def _flag_sub_harness(batch, link_id, part_no, message):
    UnresolvedImportItem.objects.create(
        batch=batch, part_number=f"Linked sub-harness ({part_no})", quantity=1,
        reason=UnresolvedImportItem.Reason.NOT_FOUND, notes=message,
        omg_object_type=UnresolvedImportItem.OmgObjectType.SUB_HARNESS, omg_object_id=link_id,
    )


def _flag_connector_part_issue(batch, part, message):
    """Not tied to one OMG connector instance — a configuration gap on the InvenTree part itself."""
    UnresolvedImportItem.objects.create(
        batch=batch, part_number=part.name or part.IPN or str(part.pk), quantity=0,
        reason=UnresolvedImportItem.Reason.NOT_FOUND, notes=message,
    )


def _connector_pin_data(c):
    """
    (terminal_only, used_pin_ids, pin_specs) for one OMG connector row,
    taken as OMG sends them. OMG owns the pin rules (see its
    components/inventree_bom_export.py): "-" is never a pin, a connector
    whose pins are all "-" is terminal_only (no contacts, no blanks), and
    each real pin appears once however many wires share it, with the
    wires' combined size. The plugin doesn't repeat those rules - one
    place to change them.
    """
    if c.get("terminal_only"):
        return True, [], []
    return False, list(c.get("used_pin_ids", [])), list(c.get("pin_specs", []))


def _labels_text(labels, limit=12):
    """'EARTH 1, EARTH 2, ... EARTH 9' - every connector, naturally sorted, de-duplicated."""
    import re
    def key(label):
        return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", label or "")]
    unique = sorted({l for l in labels if l}, key=key)
    if len(unique) > limit:
        return ", ".join(unique[:limit]) + f" and {len(unique) - limit} more"
    return ", ".join(unique)


def _apply_omg_part_logic(batch, harness_part, part_logic, seen_sub_part_pks):
    """
    Add OMG's worked-out contact and blank lines to the BOM, and flag what
    OMG couldn't resolve. OMG chose every contact (by wire size, cavity
    group, one per pin) and totalled each across the whole harness, so
    nothing is decided here - just applied. Each part gets ONE line with
    the harness-wide total (adding per connector part used to overwrite a
    shared contact's count with the last connector's).
    """
    # seals: one per individually sealed cavity (OMG Cavity Map) - an older
    # OMG simply doesn't send the key
    for kind in ("contacts", "seals", "blanks"):
        for line in part_logic.get(kind, []):
            sub_part = Part.objects.filter(pk=line.get("inventree_pk")).first()
            if sub_part is None:
                where = ", ".join(line.get("connectors") or []) or "this harness"
                UnresolvedImportItem.objects.create(
                    batch=batch, part_number=line.get("name") or f"InvenTree part {line.get('inventree_pk')}", quantity=0,
                    reason=UnresolvedImportItem.Reason.NOT_FOUND,
                    notes=(f"OMG chose this part (InvenTree pk {line.get('inventree_pk')}) as a {kind[:-1]} for "
                           f"{where}, but it no longer exists in InvenTree - was it deleted?"),
                )
                continue
            _upsert_bom_line(harness_part, sub_part, line.get("quantity", 0))
            seen_sub_part_pks.add(sub_part.pk)

    for issue in part_logic.get("issues", []):
        # OMG marks each issue 'error' or 'warning' (e.g. a wire's insulation
        # OD unknown, so its seal / max OD couldn't be checked)
        message = issue.get("message", "")
        if issue.get("severity") == "warning":
            message = "Warning: " + message
        if issue.get("object_type") == "wire":
            _flag_wire(batch, issue.get("object_id"), message, wire_no=issue.get("wire_no"))
        else:
            _flag_connector(batch, issue.get("object_id"), issue.get("label", ""), message)


def import_or_update_harness_bom(harness_part_number, omg_bom_data, category_pk=None, target_part_pk=None):
    """
    harness_part_number: the harness's IPN in InvenTree.
    omg_bom_data: the payload from OMG's HarnessBomExportView — includes
                  harness_description at the top level (see
                  inventree_bom_export.py), plus each connector/conductor
                  row's typed part number (connector_part_no /
                  conductor_part_no) alongside any already-resolved
                  inventree_pk.
    category_pk: optional InvenTree category to create the harness part
                 in, if it doesn't exist yet. Left uncategorized if omitted.
    target_part_pk: optional — when given, use THIS EXACT existing part
                 rather than finding/creating one by name match. This is
                 the "link this part I'm already looking at" flow (see
                 core.py's get_ui_panels, the unlinked-assembly panel),
                 as opposed to the dashboard's "search OMG, import"
                 flow, where no specific InvenTree part is already in
                 view and name-based find-or-create is the right
                 behavior. Raises ValueError if the pk doesn't exist.

    The harness's OWN part gets auto-created here if it doesn't exist
    yet — this is the one root-level exception to "never auto-create,"
    and it's a different situation from a connector/conductor: the
    identifying data (harness_part_number) came from the human
    explicitly searching for and selecting THIS harness to import (see
    HarnessSearchProxyView) — it's not a fuzzy match this code chose on
    its own, the same reasoning that already applied to
    exact-part-number conductor/connector matches. "Search a harness
    part number, select import" is meant to be one continuous action,
    not "create the harness part manually first, then separately run
    the BOM import" — see harness_description below for what it's
    created FROM.

    Safe to call repeatedly — this IS the "update" action, not a
    separate function. Returns (ImportBatch, resolved_matches) where
    resolved_matches is a list of {omg_object_type, omg_object_id,
    inventree_pk} for anything CONFIDENTLY auto-linked this run (exact or
    unique-parametric match only — nothing created, nothing ambiguous),
    meant to be passed straight to reconciliation.push_reconciliation_to_omg().
    """
    created = False
    linked_pk = omg_bom_data.get("harness_inventree_pk")
    if target_part_pk:
        try:
            harness_part = Part.objects.get(pk=target_part_pk)
        except Part.DoesNotExist:
            raise ValueError(f"No InvenTree part found with pk {target_part_pk}.")
    elif linked_pk and Part.objects.filter(pk=linked_pk).exists():
        # OMG already has this harness linked to an InvenTree part
        # (HarnessInventreeLink, sent as harness_inventree_pk) - use THAT
        # part, the same way connectors/wires use their inventree_pk.
        # Looking it up by name alone meant renaming the harness in OMG
        # made the next sync miss the existing part and create a second.
        harness_part = Part.objects.get(pk=linked_pk)
    else:
        # name is checked first — a real screenshot of this project's
        # actual InvenTree "Edit Part" form confirmed IPN is genuinely
        # never used (empty, not required) while the real part number
        # goes into name (the actually-required field). IPN is still
        # checked in the lookup below (harmless if a part happens to
        # have it set for some other reason), but is no longer WRITTEN
        # on create - explicitly not wanted, confirmed directly.
        harness_part = Part.objects.filter(
            Q(name__iexact=harness_part_number) | Q(IPN__iexact=harness_part_number)
        ).first()
        if not harness_part:
            harness_part = Part.objects.create(
                name=harness_part_number,
                description=omg_bom_data.get("harness_description") or "",
                category_id=category_pk,
                active=True, virtual=False, assembly=True,
            )
            created = True

    _mark_as_omg_harness(harness_part)

    batch = ImportBatch.objects.create(root_part_number=harness_part_number, root_part=harness_part)

    if not created:
        # OMG's own current part number from the payload, not the
        # harness_part_number argument - from the sync panel that's the
        # InvenTree part's existing (possibly old) name.
        _sync_harness_identity(
            batch, harness_part,
            omg_bom_data.get("harness_part_number") or harness_part_number,
            omg_bom_data.get("harness_description"),
        )
    seen_sub_part_pks = set()
    resolved_matches = []

    # The harness root part itself is a "match" too, same as any
    # connector/wire — OMG has a HarnessInventreeLink for exactly this,
    # but no way to populate it on its own since it can't know which
    # InvenTree pk corresponds to a harness it only ever identifies by
    # part number string. harness_id (OMG's own internal pk for this
    # PartNumber) has to come from the BOM payload itself for this to
    # be reportable at all — omitted entirely if the payload predates
    # that field, rather than guessing or erroring.
    harness_id = omg_bom_data.get("harness_id")
    if harness_id:
        resolved_matches.append({
            "omg_object_type": "harness", "omg_object_id": harness_id, "inventree_pk": harness_part.pk,
        })

    # labels: every connector of this part; pin_labels: only those that
    # actually have pins (need contacts / may need blanks) - what the
    # contact and blank messages name.
    connector_agg = defaultdict(lambda: {"quantity": 0, "labels": [], "pin_labels": [], "label_by_id": {},
                                         "connector_ids": [], "instances": [], "pin_specs": []})
    for c in omg_bom_data.get("connectors", []):
        if c.get("pending_part_id") and not c.get("inventree_pk"):
            _flag_connector(batch, c["connector_id"], c.get("label", ""),
                             "Still a pending part in OMG (not yet in InvenTree) — resolve pending parts before re-running this import.")
            continue

        pk = c.get("inventree_pk")
        if not pk:
            part_no = (c.get("connector_part_no") or "").strip()
            if not part_no:
                _flag_connector(batch, c["connector_id"], c.get("label", ""),
                                 "No part selected or typed for this connector in OMG yet.")
                continue

            candidates = _find_candidates(part_no)
            if len(candidates) == 1:
                pk = candidates[0].pk
                resolved_matches.append({
                    "omg_object_type": "connector", "omg_object_id": c["connector_id"], "inventree_pk": pk,
                })
            elif len(candidates) > 1:
                _flag_connector(
                    batch, c["connector_id"], c.get("label", ""),
                    f"'{part_no}' matches more than one InvenTree part — choose which one is correct.",
                    candidate_pks=[p.pk for p in candidates],
                )
                continue
            else:
                _flag_connector(batch, c["connector_id"], c.get("label", ""), NOT_IN_INVENTREE_MESSAGE.format(part_no=part_no))
                continue

        connector_agg[pk]["quantity"] += 1
        # Every connector of this part, by its own label - not one label
        # overwritten by the last connector (flags used to name only the
        # last one, e.g. "EARTH 9" for contacts used across EARTH 1-9).
        connector_agg[pk]["labels"].append(c.get("label", ""))
        connector_agg[pk]["label_by_id"][c["connector_id"]] = c.get("label", "")
        connector_agg[pk]["connector_ids"].append(c["connector_id"])
        # One entry per real pin (shared pins once, "-" never); a
        # terminal-only connector (all "-") contributes no contacts and no
        # blanks - see _connector_pin_data().
        terminal_only, used_pins, specs = _connector_pin_data(c)
        if not terminal_only:
            connector_agg[pk]["instances"].append(used_pins)
            connector_agg[pk]["pin_specs"].extend(specs)
            if used_pins:
                connector_agg[pk]["pin_labels"].append(c.get("label", ""))

    # Contacts and blanks worked out by OMG (its components/wire_ends.py):
    # finished BOM lines plus plain-words issues. None from an older OMG.
    part_logic = omg_bom_data.get("part_logic")

    for pk, agg in connector_agg.items():
        try:
            part = Part.objects.get(pk=pk)
        except Part.DoesNotExist:
            for cid in agg["connector_ids"]:
                _flag_connector(batch, cid, agg["label_by_id"].get(cid, ""), "OMG references this InvenTree pk but it no longer exists — was it deleted in InvenTree?")
            continue

        _upsert_bom_line(harness_part, part, agg["quantity"])
        seen_sub_part_pks.add(pk)

    if part_logic is not None:
        _apply_omg_part_logic(batch, harness_part, part_logic, seen_sub_part_pks)
    elif connector_agg:
        # OMG chooses contacts and blanks (its components/wire_ends.py); the
        # plugin no longer guesses them itself. Without them, say why.
        UnresolvedImportItem.objects.create(
            batch=batch, part_number=harness_part.name or harness_part.IPN or str(harness_part.pk), quantity=0,
            reason=UnresolvedImportItem.Reason.NOT_FOUND,
            notes=("OMG didn't send its worked-out contacts and blanks, so none were added to this BOM. "
                   "Update OMG to the version with wire ends (components/wire_ends.py), then re-sync."),
        )

    conductor_agg = defaultdict(lambda: {"length_m": 0.0, "wire_ids": [], "wire_no_by_id": {}})

    for w in omg_bom_data.get("conductors", []):
        if w.get("pending_part_id") and not w.get("inventree_pk"):
            _flag_wire(batch, w["wire_id"], "Still a pending part in OMG (not yet in InvenTree) — resolve pending parts before re-running this import.",
                       wire_no=w.get("wire_no"))
            continue

        pk = w.get("inventree_pk")
        if not pk:
            part_no = (w.get("conductor_part_no") or "").strip()
            description = (w.get("conductor_description") or "").strip()

            resolved_pk = None
            candidate_pks = None

            if part_no:
                candidates = _find_candidates(part_no)
                if len(candidates) == 1:
                    resolved_pk = candidates[0].pk
                elif len(candidates) > 1:
                    candidate_pks = [p.pk for p in candidates]

            # OMG's own choice (components/wire_selection.py - mm² or AWG,
            # colour codes, the harness's insulation/material/temperature
            # rules, parts in stock first). Sent only for wires with no
            # part number. If OMG couldn't reach InvenTree it sends none,
            # and the wire is flagged below rather than guessed at here.
            omg_selection = w.get("omg_selection") if not part_no else None
            if resolved_pk is None and candidate_pks is None and omg_selection:
                if omg_selection.get("status") == "match" and omg_selection.get("pk"):
                    resolved_pk = omg_selection["pk"]
                else:
                    _flag_wire(batch, w["wire_id"],
                               "OMG couldn't pick a wire part: " + (omg_selection.get("message") or "no match."),
                               wire_no=w.get("wire_no"))
                    continue

            if candidate_pks:
                _flag_wire(
                    batch, w["wire_id"],
                    "Multiple InvenTree parts match this conductor — choose which one is correct.",
                    candidate_pks=candidate_pks,
                    wire_no=w.get("wire_no"),
                )
                continue

            if resolved_pk is None:
                if not part_no:
                    _flag_wire(batch, w["wire_id"],
                               "No conductor part selected or typed for this wire in OMG, and OMG didn't pick one "
                               "(no wire selection was sent - re-sync, or pick the part in OMG's Wire Auto-Select).",
                               wire_no=w.get("wire_no"))
                    continue
                if description:
                    _flag_wire(batch, w["wire_id"], NOT_IN_INVENTREE_WITH_DESCRIPTION_MESSAGE.format(part_no=part_no, description=description),
                               wire_no=w.get("wire_no"))
                else:
                    _flag_wire(batch, w["wire_id"], NOT_IN_INVENTREE_MESSAGE.format(part_no=part_no),
                               wire_no=w.get("wire_no"))
                continue

            pk = resolved_pk
            resolved_matches.append({"omg_object_type": "wire", "omg_object_id": w["wire_id"], "inventree_pk": pk})

        conductor_agg[pk]["length_m"] += w.get("length_m", 0)
        conductor_agg[pk]["wire_ids"].append(w["wire_id"])
        conductor_agg[pk]["wire_no_by_id"][w["wire_id"]] = w.get("wire_no")

    for pk, agg in conductor_agg.items():
        try:
            part = Part.objects.get(pk=pk)
        except Part.DoesNotExist:
            for wid in agg["wire_ids"]:
                _flag_wire(batch, wid, "OMG references this InvenTree pk but it no longer exists — was it deleted in InvenTree?",
                           wire_no=agg["wire_no_by_id"].get(wid))
            continue

        _upsert_bom_line(harness_part, part, round(agg["length_m"], 4))
        seen_sub_part_pks.add(pk)

    multicore_agg = defaultdict(lambda: {"length_m": 0.0, "multicore_ids": []})
    for m in omg_bom_data.get("multicores", []):
        if m.get("pending_part_id") and not m.get("inventree_pk"):
            _flag_multicore(batch, m["multicore_id"], m.get("name", ""),
                             "Still a pending part in OMG (not yet in InvenTree) — resolve pending parts before re-running this import.")
            continue
        pk = m.get("inventree_pk")
        if not pk:
            _flag_multicore(batch, m["multicore_id"], m.get("name", ""), "No cable part selected for this multicore group in OMG yet.")
            continue
        multicore_agg[pk]["length_m"] += m.get("length_m", 0)
        multicore_agg[pk]["multicore_ids"].append(m["multicore_id"])

    for pk, agg in multicore_agg.items():
        try:
            part = Part.objects.get(pk=pk)
        except Part.DoesNotExist:
            for mid in agg["multicore_ids"]:
                _flag_multicore(batch, mid, "", "OMG references this InvenTree pk but it no longer exists — was it deleted in InvenTree?")
            continue

        _upsert_bom_line(harness_part, part, round(agg["length_m"], 4))
        seen_sub_part_pks.add(pk)

    # --- Accessories: exact match only (no wiring/pin data involved at all) ---
    accessory_agg = defaultdict(lambda: {"quantity": 0, "accessory_ids": []})
    for a in omg_bom_data.get("accessories", []):
        if a.get("pending_part_id") and not a.get("inventree_pk"):
            _flag_accessory(batch, a["accessory_id"], a.get("kind", ""),
                             "Still a pending part in OMG (not yet in InvenTree) — resolve pending parts before re-running this import.")
            continue

        pk = a.get("inventree_pk")
        if not pk:
            part_no = (a.get("part_no") or "").strip()
            if not part_no:
                _flag_accessory(batch, a["accessory_id"], a.get("kind", ""),
                                 "No part selected or typed for this accessory in OMG yet.")
                continue

            candidates = _find_candidates(part_no)
            if len(candidates) == 1:
                pk = candidates[0].pk
                resolved_matches.append({
                    "omg_object_type": "accessory", "omg_object_id": a["accessory_id"], "inventree_pk": pk,
                })
            elif len(candidates) > 1:
                _flag_accessory(
                    batch, a["accessory_id"], a.get("kind", ""),
                    f"'{part_no}' matches more than one InvenTree part — choose which one is correct.",
                    candidate_pks=[p.pk for p in candidates],
                )
                continue
            else:
                _flag_accessory(batch, a["accessory_id"], a.get("kind", ""), NOT_IN_INVENTREE_MESSAGE.format(part_no=part_no))
                continue

        accessory_agg[pk]["quantity"] += a.get("quantity", 1)
        accessory_agg[pk]["accessory_ids"].append(a["accessory_id"])

    for pk, agg in accessory_agg.items():
        try:
            part = Part.objects.get(pk=pk)
        except Part.DoesNotExist:
            for aid in agg["accessory_ids"]:
                _flag_accessory(batch, aid, "", "OMG references this InvenTree pk but it no longer exists — was it deleted in InvenTree?")
            continue

        _upsert_bom_line(harness_part, part, agg["quantity"])
        seen_sub_part_pks.add(pk)

    # --- Junctions (Y-pieces, multi-way splitters): exact match only. ---
    # Quantity is always +1 per junction ROW here (not summed from a
    # per-row quantity field like accessories) — each row in
    # omg_bom_data["junctions"] already represents exactly one physical
    # junction part, regardless of how many connectors point at it; the
    # aggregation below just counts how many separate junction rows
    # resolved to the SAME InvenTree part (e.g. three identical Y-pieces
    # used across the harness correctly sums to quantity 3).
    junction_agg = defaultdict(lambda: {"quantity": 0, "junction_ids": []})
    for j in omg_bom_data.get("junctions", []):
        if j.get("pending_part_id") and not j.get("inventree_pk"):
            _flag_junction(batch, j["junction_id"], j.get("name", ""),
                            "Still a pending part in OMG (not yet in InvenTree) — resolve pending parts before re-running this import.")
            continue

        pk = j.get("inventree_pk")
        if not pk:
            part_no = (j.get("part_no") or "").strip()
            if not part_no:
                _flag_junction(batch, j["junction_id"], j.get("name", ""),
                                "No part selected or typed for this junction in OMG yet.")
                continue

            candidates = _find_candidates(part_no)
            if len(candidates) == 1:
                pk = candidates[0].pk
                resolved_matches.append({
                    "omg_object_type": "junction", "omg_object_id": j["junction_id"], "inventree_pk": pk,
                })
            elif len(candidates) > 1:
                _flag_junction(
                    batch, j["junction_id"], j.get("name", ""),
                    f"'{part_no}' matches more than one InvenTree part — choose which one is correct.",
                    candidate_pks=[p.pk for p in candidates],
                )
                continue
            else:
                _flag_junction(batch, j["junction_id"], j.get("name", ""), NOT_IN_INVENTREE_MESSAGE.format(part_no=part_no))
                continue

        junction_agg[pk]["quantity"] += 1
        junction_agg[pk]["junction_ids"].append(j["junction_id"])

    for pk, agg in junction_agg.items():
        try:
            part = Part.objects.get(pk=pk)
        except Part.DoesNotExist:
            for jid in agg["junction_ids"]:
                _flag_junction(batch, jid, "", "OMG references this InvenTree pk but it no longer exists — was it deleted in InvenTree?")
            continue

        _upsert_bom_line(harness_part, part, agg["quantity"])
        seen_sub_part_pks.add(pk)

    # --- Linked sub-harnesses: one BOM line per distinct sub-harness, ---
    # --- resolved by its own PartNumber string (its own harness IPN) ---
    sub_harness_agg = defaultdict(lambda: {"quantity": 0, "link_ids": [], "part_no": ""})
    for s in omg_bom_data.get("sub_harnesses", []):
        part_no = (s.get("sub_harness_part_number") or "").strip()
        if not part_no:
            _flag_sub_harness(batch, s["link_id"], "", "This linked sub-harness has no part number set in OMG.")
            continue

        candidates = _find_candidates(part_no)
        if len(candidates) == 1:
            pk = candidates[0].pk
        elif len(candidates) > 1:
            _flag_sub_harness(batch, s["link_id"], part_no,
                               f"'{part_no}' matches more than one InvenTree part — resolve manually.")
            continue
        else:
            _flag_sub_harness(batch, s["link_id"], part_no, SUB_HARNESS_NOT_IMPORTED_MESSAGE.format(part_no=part_no))
            continue

        sub_harness_agg[pk]["quantity"] += 1
        sub_harness_agg[pk]["link_ids"].append(s["link_id"])
        sub_harness_agg[pk]["part_no"] = part_no

    for pk, agg in sub_harness_agg.items():
        try:
            part = Part.objects.get(pk=pk)
        except Part.DoesNotExist:
            for lid in agg["link_ids"]:
                _flag_sub_harness(batch, lid, agg["part_no"], "OMG references this InvenTree pk but it no longer exists — was it deleted in InvenTree?")
            continue

        _upsert_bom_line(harness_part, part, agg["quantity"])
        seen_sub_part_pks.add(pk)

    stale_lines = harness_part.bom_items.exclude(sub_part_id__in=seen_sub_part_pks)
    for line in stale_lines:
        UnresolvedImportItem.objects.create(
            batch=batch, part_number=line.sub_part.name or line.sub_part.IPN or str(line.sub_part_id), quantity=0,
            reason=UnresolvedImportItem.Reason.NOT_FOUND,
            notes="This BOM line no longer appears in the current OMG design. Remove it manually if that's "
                  "intentional — it was left alone rather than auto-deleted.",
        )

    batch.total_items = (
        len(omg_bom_data.get("connectors", []))
        + len(omg_bom_data.get("conductors", []))
        + len(omg_bom_data.get("multicores", []))
        + len(omg_bom_data.get("accessories", []))
        + len(omg_bom_data.get("sub_harnesses", []))
        + len(omg_bom_data.get("junctions", []))
    )
    batch.matched_items = len(seen_sub_part_pks)
    batch.flagged_items = batch.items.count()
    batch.save()

    _close_superseded_review_items(harness_part, batch)
    return batch, resolved_matches


def _close_superseded_review_items(harness_part, current_batch):
    """
    Close review items still open from EARLIER syncs of this harness.
    Every sync re-flags whatever is still wrong in its own new batch, so
    the older batches' open items are duplicates of those (or already
    fixed). Nothing used to close them, so the per-harness review queue
    grew with every sync - duplicate entries, and eventually more than
    OMG's review-details endpoint accepts in one request.

    Marked DISMISSED with a note rather than deleted, so the history of
    what each sync found is kept. Items a person already resolved are
    untouched.
    """
    from django.db.models import Value
    from django.db.models.functions import Concat
    from django.utils import timezone

    UnresolvedImportItem.objects.filter(
        batch__root_part=harness_part,
        resolution=UnresolvedImportItem.Resolution.UNRESOLVED,
    ).exclude(batch=current_batch).update(
        resolution=UnresolvedImportItem.Resolution.DISMISSED,
        resolved_at=timezone.now(),
        notes=Concat("notes", Value(f" [Superseded by sync batch #{current_batch.pk}.]")),
    )


def _upsert_bom_line(harness_part, sub_part, quantity):
    from part.models import BomItem
    BomItem.objects.update_or_create(part=harness_part, sub_part=sub_part, defaults={"quantity": quantity})
