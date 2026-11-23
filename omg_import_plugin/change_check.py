"""
Before InvenTree saves a change OMG's harnesses depend on, ask OMG whether it would break one (OMG's
/api/parts/inventree-change/, its change_guard.py) - the same rules OMG's own Parts page applies:

  a Component Type or Cavity Map value       (phase "check")      a part used as a connector must stay a
                                                                  Connector; a Cavity Map mustn't drop a
                                                                  cavity a wire uses
  a part made inactive or deleted            ("check_and_record") not while a harness uses it
  a related part removed                     ("check_and_record") not while a wire end uses it as its
                                                                  contact or seal on that connector

OMG says what it would break; with the plugin's "OMG harness check" setting on Refuse (the default) InvenTree
refuses the save with that reason, on Warn it's saved and OMG records it. If OMG can't be reached the change is
saved - and the part flagged "Changed in InvenTree - OMG not updated" (unchecked) so OMG catches up and works
out what it broke when it's back (omg_refresh.py). Never fails a save for any other reason.
"""
import logging

logger = logging.getLogger(__name__)

GUARDED_ROLES = ('part.component_type', 'connector.cavity_map')


def _plugin():
    from plugin.registry import registry
    return registry.get_plugin("omg-harness-import")


def _mode():
    plugin = _plugin()
    return (plugin.get_setting("OMG_CHANGE_CHECK") if plugin else "refuse") or "refuse"


def _ask(part, change, phase, user=''):
    """OMG's problems with this change ([] = fine). OMG down: [] and the part flagged unchecked."""
    from django.core.exceptions import ValidationError

    from .omg_refresh import mark_pending
    from .part_setup import omg_call
    mode = _mode()
    if mode == "off":
        return []
    data, error = omg_call("post", "/api/parts/inventree-change/",
                           {"inventree_pk": part.pk, "part_name": part.name, "user": user, "change": change,
                            "phase": phase, "mode": mode}, timeout=10)
    if error:
        logger.info("OMG harness check for part %s not made: %s", part.pk, error)
        mark_pending([part.pk], f"Saved without OMG's harness check - {error}", unchecked=True)
        return []
    problems = (data or {}).get("problems") or []
    if problems and (data or {}).get("refuse"):
        raise ValidationError("OMG: this would break a harness - " + "; ".join(problems))
    return problems


def _role_of(template_name):
    """The guarded OMG role a parameter template is ('' if none), from OMG's config (cached)."""
    from .part_setup import omg_part_config
    config, _error = omg_part_config(_plugin())
    roles = (config or {}).get("roles") or {}
    name = str(template_name or '').strip().lower()
    for role in GUARDED_ROLES:
        if str((roles.get(role) or {}).get("template") or '').strip().lower() == name:
            return role
    return ''


def check_parameter(parameter, data):
    """ValidationMixin.validate_parameter: a Component Type / Cavity Map value changed on a part."""
    template = getattr(parameter, "template", None)
    if template is None:
        return
    role = _role_of(template.name)
    if not role:
        return
    from .gauge_autofill import _part_content_type
    if getattr(parameter, "model_type_id", None) != _part_content_type().pk:
        return
    from part.models import Part
    part = Part.objects.filter(pk=parameter.model_id).first()
    old = None
    if parameter.pk:
        from common.models import Parameter
        old = Parameter.objects.filter(pk=parameter.pk).values_list("data", flat=True).first()
    if part is None or str(old or '') == str(data or ''):
        return
    _ask(part, {"kind": "parameter", "role": role, "template": template.name, "old": old, "new": data}, "check")


def check_instance(instance):
    """ValidationMixin.validate_model_instance: a part being made inactive."""
    from part.models import Part
    if not isinstance(instance, Part) or not instance.pk or instance.active:
        return
    was = Part.objects.filter(pk=instance.pk).values_list("active", flat=True).first()
    if was:
        _ask(instance, {"kind": "part", "action": "deactivate"}, "check_and_record")


def check_delete(instance):
    """ValidationMixin.validate_model_delete: a part, or a related part, being deleted."""
    from part.models import Part, PartRelated
    if isinstance(instance, Part):
        _ask(instance, {"kind": "part", "action": "delete"}, "check_and_record")
    elif isinstance(instance, PartRelated):
        for part, other in ((instance.part_1, instance.part_2), (instance.part_2, instance.part_1)):
            _ask(part, {"kind": "related_removed", "other": other.pk}, "check_and_record")
