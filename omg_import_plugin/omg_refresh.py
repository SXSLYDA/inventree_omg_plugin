"""
Tell OMG when a part is made or changed here, so OMG's copy (its part library) keeps up: a part created or
saved, a part parameter created or saved (Cavity Map, Contact Count, Plating ...) or a related part added. OMG
re-reads those parts in the background - a new one is added to its library - and re-works the wire ends of the
harnesses using them (OMG's /api/parts/refresh/).

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


def notify(pks):
    """Ask OMG to re-read these parts (in its background). Never raises. Returns '' when OMG took it, else why
    not - the parts are then flagged (mark_pending); taken, any flag on them is cleared."""
    from .models import OmgSyncPending
    pks = sorted({int(pk) for pk in pks if pk})
    if not pks:
        return ''
    from .part_setup import omg_call
    _data, error = omg_call("post", "/api/parts/refresh/", {"inventree_pks": pks}, timeout=10)
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
