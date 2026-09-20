"""
Core import logic. Runs inside the InvenTree process, so it talks to
InvenTree's own models directly rather than over HTTP.

Input shape (from OMG Harness):
    {
        "root_part_number": "HARN-CAT-R1300G-001",
        "components": [
            {"part_number": "DT06-2S", "quantity": 1},
            {"part_number": "0462-201-16141", "quantity": 4},
            ...
        ]
    }

For each component:
    - exact match (name or IPN), unique     -> matched, BOM line created/updated
    - exact match (name or IPN), multiple   -> flagged as ambiguous, no BOM line created
    - no match                              -> flagged as not_found, no BOM line created

REFACTORED: matching used to check IPN only. A real screenshot of this
project's actual InvenTree "Edit Part" form confirmed IPN is genuinely
never used — empty, not required — while the real part number is typed
into `name` (the actually-required field) instead. IPN-only matching
would never have found a match against any pre-existing part in this
system at all. Checks name first now (the field actually populated in
practice), with IPN checked too since it's harmless and stays
forward-compatible if IPN ever does get used.

Nothing is force-created. Ambiguous/not_found items sit in the review
queue (UnresolvedImportItem) until a human resolves them from the
InvenTree admin/plugin panel — this mirrors the OMG-side PartLink states
so the two systems stay conceptually aligned.
"""

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from part.models import Part, BomItem

from .models import ImportBatch, UnresolvedImportItem


def _find_candidates(part_number):
    """
    Exact match (name or IPN) first; fall back to a loose name/IPN
    search for the ambiguous case. See module docstring for why name is
    checked at all — it's the field this project's real InvenTree usage
    actually populates, not IPN.
    """
    exact = list(Part.objects.filter(Q(name__iexact=part_number) | Q(IPN__iexact=part_number)))
    if exact:
        return exact
    return list(Part.objects.filter(Q(IPN__icontains=part_number) | Q(name__icontains=part_number)))[:10]


def _find_root_part(root_part_number):
    return Part.objects.filter(Q(name__iexact=root_part_number) | Q(IPN__iexact=root_part_number)).first()


@transaction.atomic
def import_harness_bom(root_part_number, components):
    """
    Returns the created ImportBatch, with .items reflecting anything that
    needs human attention. Matched components get a real BomItem on the
    root part (only if the root part itself already exists in InvenTree —
    if it doesn't, every component is flagged rather than guessing at
    creating the root, since that's a bigger decision than this endpoint
    should make silently).
    """
    root_part = _find_root_part(root_part_number)

    batch = ImportBatch.objects.create(root_part_number=root_part_number, root_part=root_part)
    matched = flagged = 0

    for component in components:
        part_number = component["part_number"]
        quantity = component.get("quantity", 1)
        candidates = _find_candidates(part_number)

        if len(candidates) == 1:
            matched += 1
            if root_part is not None:
                BomItem.objects.update_or_create(
                    part=root_part,
                    sub_part=candidates[0],
                    defaults={"quantity": quantity},
                )
            else:
                # Root doesn't exist yet — record as matched-but-unattached so a
                # human can create the root part and re-run rather than losing
                # the resolution work already done.
                UnresolvedImportItem.objects.create(
                    batch=batch,
                    part_number=part_number,
                    quantity=quantity,
                    reason=UnresolvedImportItem.Reason.NOT_FOUND,
                    candidate_pks=[candidates[0].pk],
                    notes="Component matched, but root assembly part does not exist in InvenTree yet.",
                )
                flagged += 1
        elif len(candidates) > 1:
            flagged += 1
            UnresolvedImportItem.objects.create(
                batch=batch,
                part_number=part_number,
                quantity=quantity,
                reason=UnresolvedImportItem.Reason.AMBIGUOUS,
                candidate_pks=[p.pk for p in candidates],
            )
        else:
            flagged += 1
            UnresolvedImportItem.objects.create(
                batch=batch,
                part_number=part_number,
                quantity=quantity,
                reason=UnresolvedImportItem.Reason.NOT_FOUND,
                candidate_pks=[],
            )

    batch.total_items = len(components)
    batch.matched_items = matched
    batch.flagged_items = flagged
    batch.completed_at = timezone.now()
    batch.save()
    return batch


def resolve_item(item, action, part_pk=None, notes=""):
    """
    Human resolution of a single flagged item.
      action == 'link'    -> attach to an existing part (part_pk required)
      action == 'created' -> mark as resolved because the user created a new part in InvenTree
                              (via InvenTree's own Import Part wizard, or manually) (part_pk required)
      action == 'dismiss' -> no BOM line wanted, just clear the flag
    """
    if action in ("link", "created") and part_pk:
        part = Part.objects.get(pk=part_pk)
        item.resolved_part = part
        item.resolution = UnresolvedImportItem.Resolution.LINKED if action == "link" else UnresolvedImportItem.Resolution.CREATED
        if item.batch.root_part is not None:
            BomItem.objects.update_or_create(
                part=item.batch.root_part, sub_part=part, defaults={"quantity": item.quantity}
            )
    elif action == "dismiss":
        item.resolution = UnresolvedImportItem.Resolution.DISMISSED
    else:
        raise ValueError("action must be 'link', 'created', or 'dismiss' (with part_pk for link/created)")

    item.notes = notes or item.notes
    item.resolved_at = timezone.now()
    item.save()
    return item
