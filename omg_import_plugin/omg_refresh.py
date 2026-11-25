"""
Tell OMG when a part is made or changed here, so OMG's copy (its part library) keeps up: a part created or
saved, a part parameter created or saved (Cavity Map, Contact Count, Plating ...) or a related part added. The
push carries each part as it is now (part_payload: its fields, every parameter, related parts, manufacturer /
supplier part numbers and picture paths), so OMG saves it without calling InvenTree back - a new one is added
to its library - then re-works the wire ends of the harnesses using them (OMG's /api/parts/refresh/).

If OMG can't be reached the change is still saved here, and the part is flagged "Changed in InvenTree - OMG
not updated" (models.OmgSyncPending): retried every few minutes by the plugin's scheduled task
(retry_pending), by the Retry buttons (api.py sync views), and cleared once OMG has it. OMG's own nightly
copy catches anything that slips through.

Runs from InvenTree's event handling (a background worker), so nobody waits on it. Deletions aren't reported
- InvenTree's delete events only carry the deleted row's id, not the part it belonged to; OMG's nightly copy
picks those up.
"""
import logging

logger = logging.getLogger(__name__)


def related_events():
    """InvenTree's events for a related part being added (named after its table, like every model event)."""
    from part.models import PartRelated
    table = PartRelated._meta.db_table
    return {f"{table}.created", f"{table}.saved"}


def part_events():
    """A part made or saved here."""
    from part.models import Part
    table = Part._meta.db_table
    return {f"{table}.created", f"{table}.saved"}


def events():
    from .gauge_autofill import parameter_events
    return parameter_events() | related_events() | part_events()


def changed_parts(event, object_id):
    """The InvenTree part pks an event changed ([] if it isn't about a part)."""
    from .gauge_autofill import parameter_events
    if event in part_events():
        return [int(object_id)] if object_id else []
    if event in parameter_events():
        from .gauge_autofill import _parameter_and_part
        _param, part = _parameter_and_part(object_id)
        return [part.pk] if part is not None else []
    if event in related_events():
        from part.models import PartRelated
        row = PartRelated.objects.filter(pk=object_id).first()
        return [row.part_1_id, row.part_2_id] if row else []
    return []


def mark_pending(pks, error, unchecked=False):
    """Flag these parts "Changed in InvenTree - OMG not updated" (or update the flag's reason)."""
    from part.models import Part

    from .models import OmgSyncPending
    for part in Part.objects.filter(pk__in=list(pks)):
        row, _made = OmgSyncPending.objects.get_or_create(part=part)
        row.error = (error or '')[:2000]
        row.unchecked = row.unchecked or unchecked
        row.attempts += 1
        row.save()


def _picture(part):
    """(image, thumbnail) paths as InvenTree's API gives them (None, None without a picture)."""
    if not part.image:
        return None, None
    try:
        return part.image.url, part.get_thumbnail_url()
    except (ValueError, OSError):     # a missing file mustn't stop the push - the nightly copy tries again
        return None, None


def part_payload(pks):
    """[{pk, part: {name, IPN, description, active, image, thumbnail}, parameters: {template: value}, related: [pk],
    mpns, skus}] - each part as OMG stores it (the shape of its nightly copy). Parts gone are left out."""
    from common.models import Parameter
    from company.models import ManufacturerPart, SupplierPart
    from django.db.models import Q
    from part.models import Part, PartRelated

    from .inventree_native_lookup import _part_content_type

    parts = list(Part.objects.filter(pk__in=list(pks)))
    found = [p.pk for p in parts]
    parameters = {pk: {} for pk in found}
    for pk, name, data in Parameter.objects.filter(model_type=_part_content_type(), model_id__in=found).values_list(
            "model_id", "template__name", "data"):
        if name:
            parameters[pk][name] = data
    related = {pk: set() for pk in found}
    for a, b in PartRelated.objects.filter(Q(part_1_id__in=found) | Q(part_2_id__in=found)).values_list(
            "part_1_id", "part_2_id"):
        if a in related and b != a:
            related[a].add(b)
        if b in related and a != b:
            related[b].add(a)
    mpns, skus = {pk: set() for pk in found}, {pk: set() for pk in found}
    for pk, mpn in ManufacturerPart.objects.filter(part_id__in=found).values_list("part_id", "MPN"):
        if mpn and str(mpn).strip():
            mpns[pk].add(str(mpn).strip())
    for pk, sku in SupplierPart.objects.filter(part_id__in=found).values_list("part_id", "SKU"):
        if sku and str(sku).strip():
            skus[pk].add(str(sku).strip())
    out = []
    for part in parts:
        image, thumbnail = _picture(part)
        out.append({
            "pk": part.pk,
            "part": {"name": part.name, "IPN": part.IPN or "", "description": part.description or "",
                     "active": bool(part.active), "image": image, "thumbnail": thumbnail},
            "parameters": parameters[part.pk],
            "related": sorted(related[part.pk]),
            "mpns": sorted(mpns[part.pk]),
            "skus": sorted(skus[part.pk]),
        })
    return out


def notify(pks):
    """Send OMG these parts as they are now (OMG saves them and re-works their harnesses in its background).
    Never raises. Returns '' when OMG took it, else why not - the parts are then flagged (mark_pending); taken,
    any flag on them is cleared."""
    from .models import OmgSyncPending
    pks = sorted({int(pk) for pk in pks if pk})
    if not pks:
        return ''
    from .part_setup import omg_call
    _data, error = omg_call("post", "/api/parts/refresh/", {"inventree_pks": pks, "parts": part_payload(pks)},
                            timeout=10)
    if error:
        logger.info("OMG part refresh for %s not sent: %s", pks, error)
        mark_pending(pks, error)
        return error
    OmgSyncPending.objects.filter(part_id__in=pks).delete()
    return ''


def retry_pending(pks=None):
    """Report the flagged parts to OMG again (all, or these). Returns (sent, still waiting)."""
    from .models import OmgSyncPending
    rows = OmgSyncPending.objects.all()
    if pks is not None:
        rows = rows.filter(part_id__in=list(pks))
    waiting = sorted(rows.values_list('part_id', flat=True))
    if not waiting:
        return 0, 0
    error = notify(waiting)
    return (0, len(waiting)) if error else (len(waiting), 0)


def process_refresh_event(event, **kwargs):
    try:
        notify(changed_parts(event, kwargs.get("id")))
    except Exception:       # an event handler must never fail InvenTree's worker
        logger.exception("OMG part refresh for event %s failed", event)
