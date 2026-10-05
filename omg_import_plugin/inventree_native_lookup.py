"""
Part parameters from inside InvenTree (ORM), for the few places the plugin
still reads or writes them - working on both InvenTree parameter systems:

  InvenTree 1.1 and earlier: part.models.PartParameter / PartParameterTemplate
  InvenTree 1.2+:            common.models.Parameter / ParameterTemplate,
                             attached to any model by (model_type, model_id)

Which parameter NAMES to use is no longer configured here: OMG owns the
part-parameter mapping (OMG > InvenTree Settings > Part Parameters) and
sends the names with each BOM payload (payload["parameter_names"], role ->
template name). OMG also works out contacts and blanks itself
(payload["part_logic"]), so the plugin's own contact / blank / cavity
selection - and its parameter-name settings - are gone.
"""

# The plugin's own marker on harness parts it manages (value "true"). Fixed:
# it's the plugin's tag, not a mapping to an existing InvenTree parameter.
HARNESS_MARKER_PARAM = "OMG Harness"

# Fallback names if a payload ever arrives without OMG's names (same as OMG's defaults).
DEFAULT_PARAMETER_NAMES = {
    "wire.gauge": "Gauge",
    "wire.insulation": "Insulation Type",
    "wire.primary_color": "Primary Color",
    "wire.secondary_color": "Secondary Color",
}


def _parameter_models():
    """
    (ParameterTemplate model, Parameter model, generic) for this InvenTree:
    generic=True on 1.2+ (common.models, keyed by model_type/model_id),
    False on 1.1 (part.models.PartParameter*, keyed by part).
    """
    try:
        from part.models import PartParameter, PartParameterTemplate  # InvenTree 1.1 and earlier
        return PartParameterTemplate, PartParameter, False
    except ImportError:
        from common.models import Parameter, ParameterTemplate        # InvenTree 1.2+
        return ParameterTemplate, Parameter, True


def _part_content_type():
    from django.contrib.contenttypes.models import ContentType
    from part.models import Part
    return ContentType.objects.get_for_model(Part)


def get_part_parameter_str(part, param_name):
    """A part parameter's raw value by template name (case-insensitive), or None if unset."""
    # part.parameters exists on both versions (1.1: related manager; 1.2+: property)
    param = part.parameters.filter(template__name__iexact=param_name).first()
    return param.data.strip() if param and param.data else None


def set_part_parameter(part, param_name, value, description=""):
    """Set a part parameter (creating its template if needed) - both InvenTree versions."""
    Template, Parameter, generic = _parameter_models()
    template = Template.objects.filter(name__iexact=param_name).first()
    if template is None:
        template = Template.objects.create(name=param_name, description=description)
    if generic:
        Parameter.objects.update_or_create(model_type=_part_content_type(), model_id=part.pk, template=template,
                                           defaults={"data": str(value)})
    else:
        Parameter.objects.update_or_create(part=part, template=template, defaults={"data": str(value)})


def find_parts_by_parameter_native(template_name, value):
    """Part pks with parameter template_name = value (both case-insensitive) - both InvenTree versions."""
    _Template, Parameter, generic = _parameter_models()
    rows = Parameter.objects.filter(template__name__iexact=template_name, data__iexact=str(value))
    if generic:
        return set(rows.filter(model_type=_part_content_type()).values_list("model_id", flat=True))
    return set(rows.values_list("part_id", flat=True))


def find_conductor_candidates_native(size=None, conductor_type=None, primary_color=None, secondary_color=None,
                                     parameter_names=None):
    """
    InvenTree parts matching a wire by its parameters: gauge first, then
    narrowed by insulation type and colours - each narrowing only kept if it
    doesn't empty the list. parameter_names: OMG's role -> template name
    mapping (payload["parameter_names"]).
    """
    if size is None:
        return []
    names = {**DEFAULT_PARAMETER_NAMES, **(parameter_names or {})}

    candidates = find_parts_by_parameter_native(names["wire.gauge"], size)
    if not candidates:
        return []
    for role, value in (("wire.insulation", conductor_type), ("wire.primary_color", primary_color),
                        ("wire.secondary_color", secondary_color)):
        if not value:
            continue
        narrowed = candidates & find_parts_by_parameter_native(names[role], value)
        if narrowed:
            candidates = narrowed
    return sorted(candidates)
