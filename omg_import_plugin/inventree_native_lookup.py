"""
Replaces the earlier ConnectorCavityMap plugin model — this uses
InvenTree's own native data instead of a bespoke table:

- Cavity layout: a Part Parameter (default template name "Cavity Layout")
  holding a comma-separated list of every real cavity ID on a connector,
  e.g. "A,B,C,D,E,F,H,J,K" — set on the connector's Part detail page like
  any other parameter, no plugin-specific admin screen needed.

- Component type: a Part Parameter (default template name "Component
  Type") with values like "Blank" / "Contact" on the parts that ARE a
  blank or a contact. This is what disambiguates InvenTree's native
  Related Parts link, which itself carries no role/type — a connector
  can have several related parts, and the only way to know "which one is
  the blank" is by checking the related part's own Component Type value.

Both parameter template names are configurable via plugin settings
(OMG_CAVITY_LAYOUT_PARAM_NAME, OMG_COMPONENT_TYPE_PARAM_NAME) since your
actual InvenTree instance may already use different template names for
these.
"""

from django.conf import settings

DEFAULT_CAVITY_LAYOUT_PARAM = getattr(settings, "OMG_CAVITY_LAYOUT_PARAM_NAME", "Cavity Layout")
DEFAULT_COMPONENT_TYPE_PARAM = getattr(settings, "OMG_COMPONENT_TYPE_PARAM_NAME", "Component Type")


def get_part_parameter_str(part, param_name):
    """Reads an InvenTree part parameter's raw string value by template name. None if unset."""
    param = part.parameters.filter(template__name__iexact=param_name).first()
    return param.data.strip() if param and param.data else None


def get_valid_cavities(part, param_name=None):
    """Returns the connector's real cavity ID list, or None if the parameter isn't set."""
    raw = get_part_parameter_str(part, param_name or DEFAULT_CAVITY_LAYOUT_PARAM)
    if raw is None:
        return None
    return [c.strip() for c in raw.split(",") if c.strip()]


def get_related_parts(part):
    """
    InvenTree's PartRelated is a plain, undirected part<->part link with
    no role field, so both sides need checking to find "the other part"
    regardless of which side of the relation `part` is on.
    """
    from part.models import PartRelated
    from django.db.models import Q

    links = PartRelated.objects.filter(Q(part_1=part) | Q(part_2=part))
    related = []
    for link in links:
        other = link.part_2 if link.part_1_id == part.pk else link.part_1
        related.append(other)
    return related


def get_related_part_by_type(part, type_value, component_type_param=None):
    """
    Finds the single related part whose Component Type parameter matches
    type_value (case-insensitive) — e.g. type_value="Blank" to find a
    connector's blank part.

    Returns (part_or_None, status) where status is one of:
      'found', 'none', 'ambiguous'
    Never guesses between multiple candidates — 'ambiguous' means the
    caller should flag it for a human rather than picking one.

    For "Contact" specifically, prefer get_related_parts_by_type() and
    select_contact_for_gauge() instead — a connector can legitimately
    have MULTIPLE contact variants (different gauges, plating), which
    this single-match function can't represent; it would just report
    them as ambiguous.
    """
    component_type_param = component_type_param or DEFAULT_COMPONENT_TYPE_PARAM
    candidates = [
        p for p in get_related_parts(part)
        if (get_part_parameter_str(p, component_type_param) or "").lower() == type_value.lower()
    ]
    if not candidates:
        return None, "none"
    if len(candidates) > 1:
        return None, "ambiguous"
    return candidates[0], "found"


def get_related_parts_by_type(part, type_value, component_type_param=None):
    """
    Same lookup as get_related_part_by_type(), but returns ALL matches
    as a list instead of requiring exactly one. Use this for "Contact",
    where a connector legitimately having several contact variants
    (different gauge ranges, plating options) is normal, not an error —
    unlike "Blank", where more than one match genuinely is ambiguous.
    """
    component_type_param = component_type_param or DEFAULT_COMPONENT_TYPE_PARAM
    return [
        p for p in get_related_parts(part)
        if (get_part_parameter_str(p, component_type_param) or "").lower() == type_value.lower()
    ]


# --- Contact selection: gauge range + plating preference ---
#
# This is deliberately built as two small, independent filters (gauge
# range, then plating) rather than one combined query, specifically so
# a THIRD criterion can be added later without restructuring what's
# here — add a new plugin setting for the parameter name, add one more
# filter step in select_contact_for_gauge() below, done. That's the
# "extend this easily later" the contact-selection design is meant to
# support, not just today's two dimensions.

DEFAULT_CONTACT_MIN_GAUGE_PARAM = getattr(settings, "OMG_CONTACT_MIN_GAUGE_PARAM_NAME", "Min Gauge")
DEFAULT_CONTACT_MAX_GAUGE_PARAM = getattr(settings, "OMG_CONTACT_MAX_GAUGE_PARAM_NAME", "Max Gauge")


def _contact_gauge_range(contact_part, min_param, max_param):
    """Returns (min_gauge, max_gauge) as floats, or (None, None) if either isn't set/parseable."""
    raw_min = get_part_parameter_str(contact_part, min_param)
    raw_max = get_part_parameter_str(contact_part, max_param)
    try:
        return (float(raw_min) if raw_min is not None else None,
                float(raw_max) if raw_max is not None else None)
    except (TypeError, ValueError):
        return None, None


def select_contact_for_gauge(contact_candidates, gauge, min_gauge_param=None, max_gauge_param=None):
    """
    Picks the correct contact variant for one wire's gauge, from a
    connector's full list of Contact-tagged related parts (see
    get_related_parts_by_type). Never guesses when it genuinely can't
    tell — returns 'ambiguous' rather than picking arbitrarily.

    Returns (contact_part_or_None, status): 'found' / 'none' / 'ambiguous'.

    - 'none': no candidate's gauge range covers this wire's gauge (or no
      candidate has a gauge range set at all).
    - 'ambiguous': more than one candidate covers this gauge — e.g. two
      variants both claim the same range, which is a data problem in
      InvenTree worth fixing (narrow the ranges, or remove the extra
      Related Part), not something to pick between blindly.
    """
    min_gauge_param = min_gauge_param or DEFAULT_CONTACT_MIN_GAUGE_PARAM
    max_gauge_param = max_gauge_param or DEFAULT_CONTACT_MAX_GAUGE_PARAM

    if gauge is None:
        return None, "none"  # caller should flag "wire has no gauge set yet" separately

    matching = []
    for candidate in contact_candidates:
        lo, hi = _contact_gauge_range(candidate, min_gauge_param, max_gauge_param)
        if lo is None or hi is None:
            continue
        if lo <= gauge <= hi:
            matching.append(candidate)

    if not matching:
        return None, "none"
    if len(matching) == 1:
        return matching[0], "found"
    return None, "ambiguous"


DEFAULT_GAUGE_PARAM = getattr(settings, "OMG_GAUGE_PARAM_NAME", "Gauge")
DEFAULT_INSULATION_TYPE_PARAM = getattr(settings, "OMG_INSULATION_TYPE_PARAM_NAME", "Insulation Type")
DEFAULT_PRIMARY_COLOR_PARAM = getattr(settings, "OMG_PRIMARY_COLOR_PARAM_NAME", "Primary Color")
DEFAULT_SECONDARY_COLOR_PARAM = getattr(settings, "OMG_SECONDARY_COLOR_PARAM_NAME", "Secondary Color")


def find_parts_by_parameter_native(template_name, value):
    """ORM equivalent of pending_parts.inventree_lookup.find_parts_by_parameter — for use inside the plugin process."""
    from part.models import PartParameter
    return set(
        PartParameter.objects.filter(template__name__iexact=template_name, data__iexact=str(value))
        .values_list("part_id", flat=True)
    )


def find_conductor_candidates_native(size=None, conductor_type=None, primary_color=None, secondary_color=None,
                                      gauge_param=None, type_param=None, primary_param=None, secondary_param=None):
    """
    ORM equivalent of pending_parts.inventree_lookup.find_conductor_candidates
    — same progressive-narrow-but-don't-empty logic, run via direct ORM
    queries instead of REST calls since this runs inside InvenTree's own
    process during a harness import.
    """
    if size is None:
        return []

    gauge_param = gauge_param or DEFAULT_GAUGE_PARAM
    type_param = type_param or DEFAULT_INSULATION_TYPE_PARAM
    primary_param = primary_param or DEFAULT_PRIMARY_COLOR_PARAM
    secondary_param = secondary_param or DEFAULT_SECONDARY_COLOR_PARAM

    candidates = find_parts_by_parameter_native(gauge_param, size)
    if not candidates:
        return []

    for param_name, value in [(type_param, conductor_type), (primary_param, primary_color), (secondary_param, secondary_color)]:
        if not value:
            continue
        narrowed = candidates & find_parts_by_parameter_native(param_name, value)
        if narrowed:
            candidates = narrowed

    return sorted(candidates)
