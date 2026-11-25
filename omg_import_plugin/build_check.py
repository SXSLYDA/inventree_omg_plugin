"""
OMG harness BOM check on new build orders (setting OMG_BUILD_CHECK: Refuse / Warn / Off, default Refuse).

A new build order of an OMG harness (a part with the "OMG Harness" marker parameter) is checked before it's
saved - whoever makes it: a person, or a plugin such as inventree-sales-order-builds (which catches the
ValidationError, skips the line and tells the order's owner the message):

  1  changed in InvenTree   the harness's BOM no longer matches what the last OMG sync wrote (a line added,
                            removed or a quantity changed)
  2  sync left problems     the last sync's review items aren't all resolved, so the BOM is incomplete
  3  changed in OMG         the harness's design fingerprint in OMG differs from the one synced - InvenTree's
                            BOM is out of date (one small call to OMG, 5 s timeout)

Each sync stores what it wrote on the harness part (metadata "omg_sync" - harness_import.record_sync).
A harness never synced is a problem too ("never synced from OMG").

Refuse: ValidationError({"part": "OMG: ..."}) - the build isn't made. Warn: it's made, with the problems in
its metadata "omg_build_check" {"checked_at", "problems", "warnings"}. OMG can't be reached: checks 1 and 2
(InvenTree only) still apply; check 3 is skipped and noted as a warning on the build - like change_check.py,
work is never stopped because OMG is down. Existing builds (being saved again) and other parts: untouched.
"""
import logging
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)

SYNC_KEY = "omg_sync"
CHECK_KEY = "omg_build_check"
OMG_TIMEOUT = 5


def _mode():
    from plugin.registry import registry
    plugin = registry.get_plugin("omg-harness-import")
    return ((plugin.get_setting("OMG_BUILD_CHECK") if plugin else "refuse") or "refuse").lower()


def is_omg_harness(part):
    from .inventree_native_lookup import HARNESS_MARKER_PARAM, get_part_parameter_str
    return (get_part_parameter_str(part, HARNESS_MARKER_PARAM) or "").strip().lower() == "true"


def _qty(value):
    try:
        return Decimal(str(value)).normalize()
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(0)


def current_bom(part):
    """{sub_part_pk (str): total quantity} - lines for the same part added together."""
    from part.models import BomItem
    totals = {}
    for sub_part_id, quantity in BomItem.objects.filter(part=part).values_list("sub_part_id", "quantity"):
        key = str(sub_part_id)
        totals[key] = totals.get(key, Decimal(0)) + _qty(quantity)
    return totals


def bom_differences(synced, now):
    """How many parts were added, removed or changed quantity since the sync."""
    synced = {k: _qty(v) for k, v in (synced or {}).items()}
    return sum(1 for key in set(synced) | set(now) if synced.get(key) != now.get(key))


def _open_items(sync):
    """Review items still open from the sync (its batch now; the count it left if the batch is gone)."""
    from .models import UnresolvedImportItem
    batch = sync.get("batch")
    if batch:
        items = UnresolvedImportItem.objects.filter(batch_id=batch)
        if items.exists():
            return items.filter(resolution=UnresolvedImportItem.Resolution.UNRESOLVED).count()
    return int(sync.get("open_items") or 0)


def _omg_fingerprint(part, sync):
    """(OMG's current {fingerprint, revision}, None) or (None, why it couldn't be had)."""
    from .part_setup import omg_call
    part_number = sync.get("part_number") or part.IPN or part.name
    data, error = omg_call("get", f"/api/harness/{part_number}/design-fingerprint/",
                           params={"inventree_pk": part.pk}, timeout=OMG_TIMEOUT)
    if error:
        return None, error
    if not (data or {}).get("fingerprint"):
        return None, "OMG didn't give a design fingerprint - update OMG."
    return data, None


def problems_for(part):
    """(problems, warnings) for building this harness now - plain sentences for a notification."""
    sync = part.get_metadata(SYNC_KEY) if hasattr(part, "get_metadata") else None
    if not sync:
        return ["the harness has never been synced from OMG - sync it before building"], []
    problems, warnings = [], []

    differ = bom_differences(sync.get("bom"), current_bom(part))
    if differ:
        problems.append(f"the BOM was changed in InvenTree after the last OMG sync ({differ} line"
                        f"{'s' if differ != 1 else ''} differ{'s' if differ == 1 else ''}) - re-sync the harness "
                        f"from OMG")

    open_items = _open_items(sync)
    if open_items:
        problems.append(f"the last sync left {open_items} review item{'s' if open_items != 1 else ''} unresolved "
                        f"- fix {'them' if open_items != 1 else 'it'} and re-sync")

    if sync.get("fingerprint"):
        omg, error = _omg_fingerprint(part, sync)
        if error:
            warnings.append(f"OMG wasn't checked for design changes since the last sync: {error}")
        elif omg["fingerprint"] != sync["fingerprint"]:
            synced_rev, omg_rev = sync.get("revision"), omg.get("revision")
            revs = (f" (rev {synced_rev} synced, OMG now rev {omg_rev})"
                    if synced_rev and omg_rev and str(synced_rev) != str(omg_rev) else "")
            problems.append(f"the harness has changed in OMG since the last sync{revs} - re-sync before building")
    else:
        warnings.append("the last sync was from an OMG without design fingerprints - re-sync to check OMG changes")
    return problems, warnings


def check_build(instance):
    """ValidationMixin.validate_model_instance: a new build order of an OMG harness (see the module doc)."""
    from build.models import Build
    from django.core.exceptions import ValidationError
    from django.utils import timezone

    if not isinstance(instance, Build) or instance.pk or not getattr(instance, "part_id", None):
        return
    if getattr(instance, "_omg_build_checked", False):   # InvenTree validates on full_clean and again on save
        return
    instance._omg_build_checked = True
    mode = _mode()
    if mode == "off":
        return
    part = instance.part
    if not is_omg_harness(part):
        return
    try:
        problems, warnings = problems_for(part)
    except Exception:   # a bug here must never stop builds being made
        logger.exception("OMG build check failed for part %s", part.pk)
        return
    if problems and mode == "refuse":
        raise ValidationError({"part": "OMG: " + "; ".join(problems)})
    if problems or warnings:
        metadata = dict(instance.metadata or {})
        metadata[CHECK_KEY] = {"checked_at": timezone.now().isoformat(), "problems": problems, "warnings": warnings}
        instance.metadata = metadata
