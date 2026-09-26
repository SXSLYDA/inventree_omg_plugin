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

2. Blanks-needed and contacts-consumed both come from InvenTree's own
   native data (see inventree_native_lookup.py) rather than a bespoke
   plugin table:
     - Cavity Layout parameter -> the connector's real cavity ID list
       (exact, handles skipped letters like I/O/Q properly)
     - Component Type parameter on OTHER parts ("Blank" / "Contact") +
       InvenTree's native Related Parts link -> which part is this
       connector's blank/contact
   Contacts-consumed only needs the "Contact" related part and the
   actual wired pin IDs — it doesn't need the cavity layout at all,
   since "one contact per wired cavity" is true regardless of how many
   total cavities exist. Blanks DO need the cavity layout (to know the
   full universe of positions), and fall back to an approximate
   contact-count-parameter estimate (flagged as approximate) if no
   Cavity Layout has been set for that connector yet.

   A connector can have MORE THAN ONE Contact-tagged related part —
   different gauge ranges — in which case contact selection happens per
   wire, by that wire's actual ConductorSize, not one blanket contact
   for the whole connector. See select_contact_for_gauge() in
   inventree_native_lookup.py. A single Contact-tagged related part
   (the common case) skips all of this and behaves exactly as before —
   no gauge data needed at all unless a
   connector genuinely has multiple contact variants to choose between.

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

from django.conf import settings
from django.db.models import Q

from part.models import Part

from . import inventree_native_lookup as native
from .models import ImportBatch, UnresolvedImportItem
from .resolver import _find_candidates

DEFAULT_CONTACT_COUNT_PARAM = getattr(settings, "OMG_CONTACT_COUNT_PARAM_NAME", "Contact Count")
DEFAULT_HARNESS_MARKER_PARAM = getattr(settings, "OMG_HARNESS_MARKER_PARAM_NAME", "OMG Harness")


def _mark_as_omg_harness(part, marker_param=None):
    """
    Sets the OMG-harness marker parameter to "true" on a part — this is
    what core.py's get_ui_panels() actually checks before showing the
    "Sync with OMG" panel, instead of just checking `assembly`, so the
    panel never appears on an unrelated assembly that has nothing to do
    with OMG. Called every time a harness is created or successfully
    synced (idempotent — safe to call repeatedly, just ensures the
    parameter exists and is set).
    """
    from part.models import PartParameter, PartParameterTemplate

    marker_param = marker_param or DEFAULT_HARNESS_MARKER_PARAM
    template, _ = PartParameterTemplate.objects.get_or_create(
        name=marker_param, defaults={"description": "Set by the OMG Harness Import plugin — marks a part as an OMG-managed harness."},
    )
    PartParameter.objects.update_or_create(part=part, template=template, defaults={"data": "true"})

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


def _get_contact_count_param(part, param_name):
    raw = native.get_part_parameter_str(part, param_name)
    try:
        return int(float(raw)) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _flag_connector(batch, connector_id, label, message, candidate_pks=None):
    UnresolvedImportItem.objects.create(
        batch=batch, part_number=f"OMG connector #{connector_id} ({label})", quantity=1,
        reason=UnresolvedImportItem.Reason.AMBIGUOUS if candidate_pks else UnresolvedImportItem.Reason.NOT_FOUND,
        notes=message, candidate_pks=candidate_pks or [],
        omg_object_type=UnresolvedImportItem.OmgObjectType.CONNECTOR, omg_object_id=connector_id,
    )


def _flag_wire(batch, wire_id, message, candidate_pks=None, wire_no=None):
    """
    wire_no vs wire_id: wire_id is OMG's own database pk for this wire
    (used below as omg_object_id, which reconciliation.py needs to
    identify the exact record) - it was ALSO being shown to the user
    in part_number as "OMG wire #<pk>", which is a meaningless internal
    id, not the actual wire number a person would recognize from their
    own harness design. wire_no (OMG's own human-readable wire number
    field, WireNo in the OMG app's own model) is what should display
    instead - falls back to wire_id only if a caller doesn't have it,
    so this never regresses to showing nothing.
    """
    UnresolvedImportItem.objects.create(
        batch=batch, part_number=f"OMG wire #{wire_no if wire_no is not None else wire_id}", quantity=1,
        reason=UnresolvedImportItem.Reason.AMBIGUOUS if candidate_pks else UnresolvedImportItem.Reason.NOT_FOUND,
        notes=message, candidate_pks=candidate_pks or [],
        omg_object_type=UnresolvedImportItem.OmgObjectType.WIRE, omg_object_id=wire_id,
    )


def _flag_multicore(batch, multicore_id, name, message):
    UnresolvedImportItem.objects.create(
        batch=batch, part_number=f"OMG multicore cable #{multicore_id} ({name})", quantity=1,
        reason=UnresolvedImportItem.Reason.NOT_FOUND, notes=message,
        omg_object_type=UnresolvedImportItem.OmgObjectType.MULTICORE, omg_object_id=multicore_id,
    )


def _flag_accessory(batch, accessory_id, kind, message, candidate_pks=None):
    UnresolvedImportItem.objects.create(
        batch=batch, part_number=f"OMG connector accessory #{accessory_id} ({kind})", quantity=1,
        reason=UnresolvedImportItem.Reason.AMBIGUOUS if candidate_pks else UnresolvedImportItem.Reason.NOT_FOUND,
        notes=message, candidate_pks=candidate_pks or [],
        omg_object_type=UnresolvedImportItem.OmgObjectType.ACCESSORY, omg_object_id=accessory_id,
    )


def _flag_junction(batch, junction_id, name, message, candidate_pks=None):
    UnresolvedImportItem.objects.create(
        batch=batch, part_number=f"OMG connector junction #{junction_id} ({name})", quantity=1,
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


def _cavity_bom_additions(batch, part, agg, contact_count_param):
    """
    Returns a list of (sub_part, quantity) tuples to add to the BOM for
    this connector part — contacts consumed, and blanks needed, in that
    order. Anything that can't be resolved gets flagged instead of
    guessed at; this function never invents a part reference.

    Contact selection: a connector can have MULTIPLE Contact-tagged
    related parts (different gauge ranges), not just one. Each wire's
    own gauge determines which variant it needs — this isn't guessed,
    it's read from that specific wire's ConductorSize
    (see agg["pin_specs"], populated by OMG's get_pin_wire_specs()).
    Falls back to the old single-contact-part behavior automatically
    when a connector only has one Contact-tagged related part — no
    gauge matching needed or attempted in that common case.
    """
    additions = []

    contact_candidates = native.get_related_parts_by_type(part, "Contact")
    total_used = sum(len(instance_pins) for instance_pins in agg["instances"])

    if len(contact_candidates) == 1:
        # Common case: one contact type fits every pin on this connector — no
        # gauge matching needed, this is just the original simple behavior.
        if total_used > 0:
            additions.append((contact_candidates[0], total_used))
    elif len(contact_candidates) > 1:
        # Multiple variants exist — resolve per pin, by that wire's actual gauge.
        contact_qty = defaultdict(int)
        for pin in agg["pin_specs"]:
            gauge = pin.get("conductor_size")
            if gauge is None:
                _flag_wire(batch, pin["wire_id"], "This wire has no gauge set yet, and its connector has "
                                                   "multiple contact variants — can't tell which contact it needs.",
                           wire_no=pin.get("wire_no"))
                continue
            contact_part, status = native.select_contact_for_gauge(
                contact_candidates, gauge,
            )
            if status == "found":
                contact_qty[contact_part.pk] += 1
            elif status == "ambiguous":
                _flag_wire(batch, pin["wire_id"],
                           f"This wire's gauge ({gauge}) matches more than one contact variant on "
                           f"{part.name or part.IPN} — narrow their Min/Max Gauge parameters so they don't overlap, "
                           f"or remove the extra Related Part.",
                           wire_no=pin.get("wire_no"))
            else:
                _flag_wire(batch, pin["wire_id"],
                           f"No contact variant on {part.name or part.IPN} covers this wire's gauge ({gauge}) — "
                           f"add one, or fix its Min/Max Gauge parameters.",
                           wire_no=pin.get("wire_no"))
        for pk, qty in contact_qty.items():
            from part.models import Part as _Part
            additions.append((_Part.objects.get(pk=pk), qty))
    elif total_used > 0:
        _flag_connector_part_issue(
            batch, part,
            f"{total_used} contact(s) consumed by {part.name or part.IPN} ({agg['label']}) but no related part is "
            f"tagged Component Type = Contact for it yet — add a Related Part and set its type.",
        )

    valid_cavities = native.get_valid_cavities(part)
    if valid_cavities is not None:
        missing = sum(len(set(valid_cavities) - instance_pins) for instance_pins in agg["instances"])
        if missing > 0:
            blank_part, status = native.get_related_part_by_type(part, "Blank")
            if status == "found":
                additions.append((blank_part, missing))
            elif status == "ambiguous":
                _flag_connector_part_issue(
                    batch, part,
                    f"{part.name or part.IPN} has more than one related part tagged Component Type = Blank — "
                    f"can't tell which one to use.",
                )
            else:
                _flag_connector_part_issue(
                    batch, part,
                    f"{missing} blank(s) needed for {part.name or part.IPN} ({agg['label']}) but no related part is "
                    f"tagged Component Type = Blank for it yet.",
                )
    else:
        contact_count = _get_contact_count_param(part, contact_count_param)
        if contact_count is not None:
            approx_missing = sum(max(0, contact_count - len(instance_pins)) for instance_pins in agg["instances"])
            if approx_missing > 0:
                _flag_connector_part_issue(
                    batch, part,
                    f"~{approx_missing} blank(s) estimated for {part.name or part.IPN} ({agg['label']}) from the "
                    f"'{contact_count_param}' parameter — APPROXIMATE, can be wrong if this connector's "
                    f"cavity lettering skips positions. Set a '{native.DEFAULT_CAVITY_LAYOUT_PARAM}' "
                    f"parameter on {part.name or part.IPN} for an exact count.",
                )

    return additions


def import_or_update_harness_bom(harness_part_number, omg_bom_data, contact_count_param=None, category_pk=None, harness_marker_param=None, target_part_pk=None):
    """
    harness_part_number: the harness's IPN in InvenTree.
    omg_bom_data: the payload from OMG's HarnessBomExportView — includes
                  harness_description at the top level (see
                  inventree_bom_export.py), plus each connector/conductor
                  row's typed part number (connector_part_no /
                  conductor_part_no) alongside any already-resolved
                  inventree_pk.
    contact_count_param: InvenTree parameter name used ONLY as the
                          blanks fallback when no Cavity Layout parameter
                          is set. Defaults to OMG_CONTACT_COUNT_PARAM_NAME.
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
    contact_count_param = contact_count_param or DEFAULT_CONTACT_COUNT_PARAM

    if target_part_pk:
        try:
            harness_part = Part.objects.get(pk=target_part_pk)
        except Part.DoesNotExist:
            raise ValueError(f"No InvenTree part found with pk {target_part_pk}.")
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

    _mark_as_omg_harness(harness_part, marker_param=harness_marker_param)

    batch = ImportBatch.objects.create(root_part_number=harness_part_number, root_part=harness_part)
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

    connector_agg = defaultdict(lambda: {"quantity": 0, "label": "", "connector_ids": [], "instances": [], "pin_specs": []})
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
        connector_agg[pk]["label"] = c.get("label", "")
        connector_agg[pk]["connector_ids"].append(c["connector_id"])
        # NOT wrapped in set() — get_used_pin_ids() may deliberately return
        # a list with repeats (e.g. "-" repeated once per wire, for
        # connectors using "-" as a "no distinguishable pin ID" convention
        # — see that function's docstring). Wrapping in set() here would
        # collapse those repeats back down to one and undercount contacts
        # consumed. set(valid_cavities) - instance_pins below still works
        # correctly with a list on the right-hand side.
        connector_agg[pk]["instances"].append(list(c.get("used_pin_ids", [])))
        connector_agg[pk]["pin_specs"].extend(c.get("pin_specs", []))

    for pk, agg in connector_agg.items():
        try:
            part = Part.objects.get(pk=pk)
        except Part.DoesNotExist:
            for cid in agg["connector_ids"]:
                _flag_connector(batch, cid, agg["label"], "OMG references this InvenTree pk but it no longer exists — was it deleted in InvenTree?")
            continue

        _upsert_bom_line(harness_part, part, agg["quantity"])
        seen_sub_part_pks.add(pk)

        for sub_part, quantity in _cavity_bom_additions(batch, part, agg, contact_count_param):
            _upsert_bom_line(harness_part, sub_part, quantity)
            seen_sub_part_pks.add(sub_part.pk)

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
            size = w.get("conductor_size")
            conductor_type = (w.get("conductor_type") or "").strip()
            primary = (w.get("conductor_primary") or "").strip()
            secondary = (w.get("conductor_secondary") or "").strip()

            resolved_pk = None
            candidate_pks = None

            if part_no:
                candidates = _find_candidates(part_no)
                if len(candidates) == 1:
                    resolved_pk = candidates[0].pk
                elif len(candidates) > 1:
                    candidate_pks = [p.pk for p in candidates]

            if resolved_pk is None and candidate_pks is None and size is not None:
                param_candidates = native.find_conductor_candidates_native(
                    size=size, conductor_type=conductor_type, primary_color=primary, secondary_color=secondary,
                )
                if len(param_candidates) == 1:
                    resolved_pk = param_candidates[0]
                elif len(param_candidates) > 1:
                    candidate_pks = param_candidates

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
                    _flag_wire(batch, w["wire_id"], "No conductor part selected or typed for this wire in OMG yet, and no gauge/size to search by.",
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
    return batch, resolved_matches


def _upsert_bom_line(harness_part, sub_part, quantity):
    from part.models import BomItem
    BomItem.objects.update_or_create(part=harness_part, sub_part=sub_part, defaults={"quantity": quantity})
