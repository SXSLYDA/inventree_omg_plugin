"""
"Mouser Import - Pending Parts" (dashboard item, PendingPartsPanel.tsx):
parts someone picked from Mouser inside OMG because InvenTree didn't have
them yet. OMG keeps them as pending parts until they exist here.

  GET  /plugin/omg-harness-import/pending-parts/
       OMG's pending parts still used on a harness (/api/parts/pending/unresolved/),
       each with where it's used and any InvenTree part that already matches
       it exactly; plus the pending category and the parts still sitting in it.
  POST /plugin/omg-harness-import/pending-parts/  {"ids": [pending part ids]}
       Imports them: an exact match (name / IPN / MPN / SKU) is linked as it
       is; otherwise the part is created from Mouser - with its manufacturer
       part, Mouser supplier part and price breaks, by the Mouser Integration
       plugin (inventree-mouser - the methods its Import from Supplier wizard
       uses) - plus OMG's parameters. Several matches = left for a
       person to choose (reported, nothing created).

New parts go into the PENDING category (plugin setting "Pending parts
category", created as "OMG Pending Parts" the first time if not set), not
a guessed one: a person moves each to its real category in InvenTree.
InvenTree's own Import from Supplier wizard is untouched - it still puts a
part in the category it was started from.

OMG is told about each one (the reconciliation webhook), per harness that
uses it, so its pending part becomes the real InvenTree part.
"""
import logging
from types import SimpleNamespace

from django.db import transaction
from django.db.models import Q

from .mouser_link import mouser_plugin, mouser_search_key_set, require_mouser_plugin

logger = logging.getLogger("inventree")

PENDING_CATEGORY_SETTING = "OMG_PENDING_CATEGORY"
PENDING_CATEGORY_NAME = "OMG Pending Parts"


# ==================== Category ====================

def pending_category(plugin, create=False):
    """The pending-parts PartCategory (setting), created as "OMG Pending Parts" when create=True and not set."""
    from part.models import PartCategory
    pk = plugin.get_setting(PENDING_CATEGORY_SETTING) if plugin else None
    category = PartCategory.objects.filter(pk=pk).first() if pk else None
    if category is None and create:
        category = PartCategory.objects.filter(name=PENDING_CATEGORY_NAME, parent=None).first() or \
            PartCategory.objects.create(name=PENDING_CATEGORY_NAME,
                                        description="Parts imported from Mouser for OMG - move each to its own category.")
        plugin.set_setting(PENDING_CATEGORY_SETTING, category.pk)
    return category


# ==================== Matching ====================

def exact_matches(mpn, spn=""):
    """InvenTree parts that are exactly this part: name / IPN = MPN, or a manufacturer MPN / supplier SKU."""
    from company.models import ManufacturerPart, SupplierPart
    from part.models import Part
    keys = [k for k in {(mpn or "").strip(), (spn or "").strip()} if k]
    if not keys:
        return []
    q_part, q_mpn, q_sku = Q(), Q(), Q()
    for k in keys:
        q_part |= Q(name__iexact=k) | Q(IPN__iexact=k)
        q_mpn |= Q(MPN__iexact=k)
        q_sku |= Q(SKU__iexact=k) | Q(manufacturer_part__MPN__iexact=k)
    pks = set(Part.objects.filter(q_part).values_list("pk", flat=True))
    pks |= set(ManufacturerPart.objects.filter(q_mpn).values_list("part_id", flat=True))
    pks |= set(SupplierPart.objects.filter(q_sku).values_list("part_id", flat=True))
    pks.discard(None)
    return list(Part.objects.filter(pk__in=pks).order_by("name", "pk"))


def _part_row(part):
    return {"pk": part.pk, "name": part.name, "description": part.description or "",
            "category": part.category.pathstring if part.category_id else ""}


# ==================== Reading ====================

def _omg_pending():
    """(rows, error) - OMG's pending parts still used on a harness."""
    from .part_setup import omg_call
    rows, error = omg_call("get", "/api/parts/pending/unresolved/")
    if error:
        return None, error
    return rows or [], None


def pending_state(plugin):
    """Everything the dashboard item shows."""
    rows, error = _omg_pending()
    category = pending_category(plugin)
    in_category = []
    if category is not None:
        from part.models import Part
        in_category = [_part_row(p) for p in Part.objects.filter(category=category).order_by("name")]
    items = []
    for row in rows or []:
        data = row.get("mouser_data") or {}
        matches = exact_matches(row.get("mpn"), data.get("spn"))
        items.append({
            "id": row["id"], "kind": row.get("kind") or "", "mpn": row.get("mpn") or "",
            "spn": data.get("spn") or "", "manufacturer": data.get("manufacturer") or "",
            "description": data.get("description") or "", "url": data.get("url") or "",
            "used_by": row.get("used_by") or [],
            "matches": [_part_row(p) for p in matches],
        })
    return {
        "error": error,
        "items": items,
        "category": {"pk": category.pk, "name": category.pathstring} if category else None,
        "in_category": in_category,
        "mouser_key_set": mouser_search_key_set(),
    }


# ==================== Importing ====================

def pending_kind(mpn, spn=""):
    """
    The kind OMG gave this part if OMG is waiting for it (one of its pending
    parts: same MPN or Mouser part number) - "" when pending with no kind -
    else None (not pending, or OMG can't be reached).
    """
    rows, _error = _omg_pending()
    keys = {k.strip().lower() for k in (mpn, spn) if k and k.strip()}
    for row in rows or []:
        stored = row.get("mouser_data") or {}
        if keys & {str(v).strip().lower() for v in (row.get("mpn"), stored.get("spn")) if v}:
            return row.get("kind") or ""
    return None


def _set_omg_parameters(part, mouser_data, kind):
    """
    OMG's parameters on a part just created from Mouser (mouser_parameters.py) -
    the kind OMG sent gives the Component Type. Only parameters the part
    doesn't have yet are set: a value already on the part (typed by a person,
    or there before) is never overwritten. Returns ["Template: reason"] for
    values InvenTree refused (or a note if OMG couldn't be reached).
    """
    from .inventree_native_lookup import get_part_parameter_str
    from .mouser_parameters import KIND_TYPES, omg_parameters
    from .part_setup import _plugin, _set_parameter, _template, omg_part_config
    from .selection_lists import lists_for_roles
    try:
        config, error = omg_part_config(_plugin())
    except Exception as exc:
        config, error = None, str(exc)
    if not config:
        return [f"OMG's parameter names couldn't be read ({error})"]
    skipped = []
    for template_name, value in omg_parameters(mouser_data, config, KIND_TYPES.get(str(kind or "").lower()),
                                               lists=lists_for_roles(config)):
        template = _template(template_name)
        if template is None:
            skipped.append(f"{template_name}: no such template in InvenTree")
            continue
        if get_part_parameter_str(part, template.name):
            continue        # already set - left as it is
        try:
            _set_parameter(part, template, value)
        except Exception as exc:      # InvenTree's own checks (units, choices) refused it
            skipped.append(f"{template_name} = {value}: {exc}")
    return skipped


def _mouser_data(row):
    """Today's Mouser listing for a pending part (fresh prices), else what OMG stored when it was picked."""
    stored = row.get("mouser_data") or {}
    if mouser_search_key_set():
        try:
            return mouser_plugin().get_import_data("mouser", stored.get("spn") or row["mpn"])
        except Exception:
            logger.info("Mouser lookup for pending part %s failed - using OMG's stored data", row.get("id"))
    return {**stored, "mpn": stored.get("mpn") or row["mpn"]}


def _create_from_mouser(data, category, user):
    """
    The part, its manufacturer part and Mouser supplier part - the wizard's own
    steps, in order, done by the Mouser Integration plugin (inventree-mouser).
    """
    mouser = require_mouser_plugin()
    with transaction.atomic():
        part = mouser.import_part(data, category=category, creation_user=user)
        manufacturer_part = mouser.import_manufacturer_part(data, part=part)
        mouser.import_supplier_part(data, part=part, manufacturer_part=manufacturer_part)
    return part


def import_pending(plugin, ids, user):
    """
    Import the chosen pending parts (see module docstring). Returns
    {"results": [{id, mpn, status, part, message}], "reported": bool,
     "report_errors": [..], "category": {...}}. status: linked (already in
    InvenTree), created, ambiguous, failed.
    """
    from .reconciliation import push_reconciliation_to_omg_detailed
    rows, error = _omg_pending()
    if error:
        raise ValueError(error)
    wanted = {int(i) for i in ids or []}
    rows = [r for r in rows if r["id"] in wanted]
    category = pending_category(plugin, create=True)

    results, by_harness = [], {}
    for row in rows:
        stored = row.get("mouser_data") or {}
        result = {"id": row["id"], "mpn": row["mpn"], "status": "", "part": None, "message": ""}
        matches = exact_matches(row["mpn"], stored.get("spn"))
        try:
            if len(matches) > 1:
                result.update(status="ambiguous", message="Several InvenTree parts match "
                              + ", ".join(p.name for p in matches[:5]) + " - link the right one from the review list.")
                results.append(result)
                continue
            if matches:
                part = matches[0]
                result.update(status="linked", part=_part_row(part), message="Already in InvenTree - linked.")
            else:
                data = _mouser_data(row)
                part = _create_from_mouser(data, category, user)
                skipped = _set_omg_parameters(part, data, row.get("kind"))
                result.update(status="created", part=_part_row(part),
                              message=("Some parameters weren't set: " + "; ".join(skipped)
                                       + " - fill them in the OMG Part Setup panel.") if skipped else "")
        except Exception as exc:     # one bad part mustn't stop the rest
            logger.exception("Importing OMG pending part %s failed", row["id"])
            result.update(status="failed", message=str(exc))
            results.append(result)
            continue
        results.append(result)
        # OMG's webhook is authenticated per harness - report each part
        # under a harness that uses it.
        harness = next((u["harness"] for u in row.get("used_by") or [] if u.get("harness")), None)
        if harness:
            by_harness.setdefault(harness, []).append(
                {"pending_part_id": row["id"], "inventree_pk": part.pk, "mpn": row["mpn"]})

    from .models import UnresolvedImportItem
    report_errors = []
    for harness, resolved in by_harness.items():
        batch = SimpleNamespace(root_part_number=harness, items=UnresolvedImportItem.objects.none())
        ok, data, err = push_reconciliation_to_omg_detailed(batch, resolved_pending_parts=resolved)
        if not ok:
            report_errors.append(f"{harness}: {err}")
        for rejected in (data or {}).get("rejected_pending_parts", []):
            report_errors.append(f"OMG didn't accept pending part {rejected.get('pending_part_id')}: "
                                 f"{rejected.get('reason')}")
    return {"results": results, "reported": not report_errors, "report_errors": report_errors,
            "category": {"pk": category.pk, "name": category.pathstring}}
