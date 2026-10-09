"""
Part parameters from inside InvenTree (ORM), for the few places the plugin
reads or writes them. InvenTree 1.2+ parameters only: common.models.Parameter /
ParameterTemplate, attached to a part by (model_type = Part, model_id = pk).
(InvenTree 1.1's part.models.PartParameter is gone - this plugin needs 1.2+.)

Which parameter NAMES to use is no longer configured here: OMG owns the
part-parameter mapping (OMG > InvenTree Settings > Part Parameters) and
sends the names with each BOM payload (payload["parameter_names"], role ->
template name). OMG also works out contacts, blanks and wire parts itself
(payload["part_logic"], each wire's "omg_selection"), so the plugin's own
contact / blank / cavity / wire-gauge searches - and its parameter-name
settings - are gone.
"""

# The plugin's own marker on harness parts it manages (value "true"). Fixed:
# it's the plugin's tag, not a mapping to an existing InvenTree parameter.
HARNESS_MARKER_PARAM = "OMG Harness"

def _part_content_type():
    from django.contrib.contenttypes.models import ContentType
    from part.models import Part
    return ContentType.objects.get_for_model(Part)


def parameter_template(name):
    """The ParameterTemplate called `name` (case-insensitive), or None."""
    from common.models import ParameterTemplate
    return ParameterTemplate.objects.filter(name__iexact=name).first()


def part_parameter_lookup(part, template):
    """Filter kwargs for one part's value of one template."""
    return {"model_type": _part_content_type(), "model_id": part.pk, "template": template}


def get_part_parameter_str(part, param_name):
    """A part parameter's raw value by template name (case-insensitive), or None if unset."""
    param = part.parameters.filter(template__name__iexact=param_name).first()
    return param.data.strip() if param and param.data else None


def set_part_parameter(part, param_name, value, description=""):
    """Set a part parameter (creating its template if needed)."""
    from common.models import Parameter, ParameterTemplate
    template = parameter_template(param_name)
    if template is None:
        template = ParameterTemplate.objects.create(name=param_name, description=description)
    Parameter.objects.update_or_create(**part_parameter_lookup(part, template), defaults={"data": str(value)})


def find_parts_by_parameter_native(template_name, value):
    """Part pks with parameter template_name = value (both case-insensitive)."""
    from common.models import Parameter
    return set(Parameter.objects.filter(template__name__iexact=template_name, data__iexact=str(value),
                                        model_type=_part_content_type()).values_list("model_id", flat=True))
