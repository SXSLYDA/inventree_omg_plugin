"""
Tell OMG when a part changes here, so its caches don't keep the old values
(OMG re-reads a part only every 15 minutes to an hour otherwise): a part
parameter created or saved (Cavity Map, Contact Count, Plating ...) or a
related part added. OMG re-reads those parts in the background and re-works
the wire ends of the harnesses using them (OMG's /api/parts/refresh/).

Runs from InvenTree's event handling (a background worker), so nobody waits
on it. Deletions aren't reported - InvenTree's delete events only carry the
deleted row's id, not the part it belonged to; use Sync Check's
"Re-read InvenTree" in OMG after removing a parameter or a related part.
"""
import logging

logger = logging.getLogger(__name__)

PARAMETER_EVENTS = {"common_parameter.created", "common_parameter.saved"}
RELATED_EVENTS = {"part_partrelated.created", "part_partrelated.saved"}
EVENTS = PARAMETER_EVENTS | RELATED_EVENTS


def changed_parts(event, object_id):
    """The InvenTree part pks an event changed ([] if it isn't about a part)."""
    if event in PARAMETER_EVENTS:
        from .gauge_autofill import _parameter_and_part
        _param, part = _parameter_and_part(object_id)
        return [part.pk] if part is not None else []
    if event in RELATED_EVENTS:
        from part.models import PartRelated
        row = PartRelated.objects.filter(pk=object_id).first()
        return [row.part_1_id, row.part_2_id] if row else []
    return []


def notify(pks):
    """Ask OMG to re-read these parts (in its background). Never raises."""
    pks = sorted({int(pk) for pk in pks if pk})
    if not pks:
        return
    from .part_setup import omg_call
    _data, error = omg_call("post", "/api/parts/refresh/", {"inventree_pks": pks}, timeout=10)
    if error:
        logger.info("OMG part refresh for %s not sent: %s", pks, error)


def process_refresh_event(event, **kwargs):
    try:
        notify(changed_parts(event, kwargs.get("id")))
    except Exception:       # an event handler must never fail InvenTree's worker
        logger.exception("OMG part refresh for event %s failed", event)
