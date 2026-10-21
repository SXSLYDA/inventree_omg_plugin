"""
OMG's selection lists inside InvenTree (OMG sets them up - see OMG's
components/selection_lists.py and its InvenTree Settings page).

OMG's part-profiles config says which list each parameter role uses:
config["selection_lists"] = {role: {"list": "Connector Series", "per_item": bool}}.

  - One value per part (Contact Series, Side...): OMG links the template to
    the list and InvenTree itself refuses anything else - nothing to do here.
  - Several values per part (Connector Series on an accessory, "DT, DTM"):
    InvenTree's lists hold one value, so validate_list_items() checks each
    value against the list when a part parameter is saved.

Also used to offer the lists in the OMG Part Setup panel, and to match
Mouser values to them (mouser_parameters.omg_parameters).
"""
from .selection_match import join_items, norm, split_items, suggest


def list_values(name):
    """Active values of the selection list called `name` ([] if there's no such list, or it's off)."""
    if not name:
        return []
    from common.models import SelectionList
    lst = SelectionList.objects.filter(name__iexact=name, active=True).first()
    return lst.get_choices(active=True) if lst else []


def role_list(config, role):
    """{"list", "per_item"} for a role, or None."""
    return ((config or {}).get("selection_lists") or {}).get(role)


def lists_for_roles(config):
    """{role: [values]} for every role whose list has values - for the Mouser auto-fill."""
    out = {}
    for role, meta in ((config or {}).get("selection_lists") or {}).items():
        values = list_values(meta.get("list"))
        if values:
            out[role] = values
    return out


def snap(value, values, per_item=False, exact=False):
    """
    `value` as the list's own spelling: "dt" -> "DT", "Deutsch DT" -> "DT"
    (exact=True: only differences of case / spaces / dashes). Several-value
    parameters are matched item by item. Returns (snapped text, [items that
    didn't match]). No list (values empty) = unchanged.
    """
    if not values or not str(value or "").strip():
        return value, []
    items = split_items(value) if per_item else [str(value).strip()]
    out, unknown = [], []
    for item in items:
        if exact:
            match = next((v for v in values if norm(v) == norm(item)), None)
        else:
            match = suggest(item, values)
        if match:
            out.append(match)
        else:
            unknown.append(item)
            out.append(item)
    return (join_items(out) if per_item else out[0]), unknown


def _role_for_template(config, template_name):
    key = str(template_name or "").strip().lower()
    for role, meta in ((config or {}).get("roles") or {}).items():
        if str(meta.get("template") or "").strip().lower() == key:
            return role
    return None


def validate_list_items(plugin, parameter, data):
    """
    ValidationMixin hook: a several-value parameter (e.g. Connector Series
    "DT, DTM") must only hold values from its selection list. Skipped when
    OMG's config can't be read, the list doesn't exist yet or is empty, so
    a missing setup never blocks saving. Returns None (other checks still run).
    """
    from django.core.exceptions import ValidationError

    from .part_setup import omg_part_config
    template = getattr(parameter, "template", None)
    if template is None or data in (None, ""):
        return None
    config, _error = omg_part_config(plugin)
    if not config:
        return None
    role = _role_for_template(config, template.name)
    meta = role_list(config, role) if role else None
    if not meta or not meta.get("per_item"):
        return None
    values = list_values(meta.get("list"))
    if not values:
        return None
    unknown = [item for item in split_items(data) if item not in values]
    if unknown:
        hints = [f"'{u}' - did you mean '{suggest(u, values)}'?" if suggest(u, values) else f"'{u}'" for u in unknown]
        raise ValidationError(
            f"Not in the {meta['list']} list: {', '.join(hints)}. Use values from the list, separated by commas "
            f"(add new ones on OMG's InvenTree Settings page, Selection Lists).")
    return None
