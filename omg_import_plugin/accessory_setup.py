"""
Backend for the "OMG Accessories" panel (PartSetupPanel.tsx) - locks,
boots, covers... matched to connectors by Connector Series / Side / Ways.

OMG owns the matching rules (its components/accessory_rules.py) - this
module has no copy of them. It asks OMG what to show
(GET /api/parts/accessory-setup/<pk>/) and what to write
(POST, same URL), then does the InvenTree side itself:

  On a connector:  saving writes Required Accessories and Preferred
                   Accessories (the text OMG worked out), then links the
                   connector to every fitting part as InvenTree related
                   parts (reference only - never removes links).
  On an accessory: shows every connector it fits (read only).
"""
from django.db import transaction
from django.db.models import Q
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .inventree_native_lookup import get_part_parameter_str
from .part_setup import _can_edit, _get_part, _plugin, _role_template, _set_parameter, _template, omg_call, omg_part_config

# roles the panel needs templates for (exit angle / direction are optional)
NEEDED_ROLES = ('part.component_type', 'connector.series', 'connector.side', 'accessory.ways',
                'connector.contact_count', 'connector.required_accessories', 'connector.preferred_accessories')


def _missing_templates(config):
    names = [_role_template(config, role) or role for role in NEEDED_ROLES]
    return [n for n in names if not _template(n)]


def accessory_state(part, config, refresh=False):
    """(what the panel shows, error) - OMG's answer plus this part's identity and template checks."""
    data, error = omg_call("get", f"/api/parts/accessory-setup/{part.pk}/", params={"refresh": "1"} if refresh else None)
    if error:
        return None, error
    return {**data, "part": {"pk": part.pk, "name": part.name, "ipn": part.IPN or ""},
            "missing_templates": _missing_templates(config)}, None


def save_connector_accessories(part, config, kinds, link_related=True):
    """
    kinds: [{kind, required, quantity, preferred_pks}] -> OMG works out the
    Required / Preferred Accessories text, written here; then every fitting
    part is linked as a related part. Returns (state after, errors).
    """
    from django.core.exceptions import ValidationError
    from part.models import PartRelated
    values, error = omg_call("post", f"/api/parts/accessory-setup/{part.pk}/", {"kinds": kinds or []})
    if error:
        return None, [error]
    templates = {}
    for role, key in (("connector.required_accessories", "required_accessories"),
                      ("connector.preferred_accessories", "preferred_accessories")):
        name = _role_template(config, role)
        template = _template(name) if name else None
        if template is None:
            return None, [f"No '{name or role}' parameter template in InvenTree - create it "
                          f"(OMG > InvenTree Settings > Part Parameters can) and try again."]
        templates[key] = template
    try:
        with transaction.atomic():
            for key, template in templates.items():
                _set_parameter(part, template, values.get(key, ""))
    except ValidationError as exc:
        return None, list(exc.messages)

    # re-read from OMG (refresh - it caches InvenTree for a few minutes)
    state, error = accessory_state(part, config, refresh=True)
    if error:
        return None, [f"Saved, but couldn't re-read from OMG: {error}"]
    if link_related:
        linked = {r.part_2_id if r.part_1_id == part.pk else r.part_1_id
                  for r in PartRelated.objects.filter(Q(part_1=part) | Q(part_2=part))}
        for k in state.get("kinds", []):
            for p in k.get("options", []):
                if p["pk"] not in linked and p["pk"] != part.pk:
                    try:
                        PartRelated.objects.create(part_1=part, part_2_id=p["pk"], note=k["kind"])
                        linked.add(p["pk"])
                    except ValidationError:
                        pass       # already linked the other way round - fine
    return state, []


class AccessorySetupView(APIView):
    """GET/POST /plugin/omg-harness-import/accessory-setup/<pk>/ - see module docstring."""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        part = _get_part(pk)
        if part is None:
            return Response({"detail": "Part not found."}, status=status.HTTP_404_NOT_FOUND)
        config, error = omg_part_config(_plugin())
        if error:
            return Response({"error": error})
        state, error = accessory_state(part, config, refresh=request.query_params.get("refresh") == "1")
        if error:
            return Response({"error": error})
        return Response({"error": None, "can_edit": _can_edit(request.user), **state})

    def post(self, request, pk):
        part = _get_part(pk)
        if part is None:
            return Response({"detail": "Part not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _can_edit(request.user):
            return Response({"detail": "You don't have permission to change parts."}, status=status.HTTP_403_FORBIDDEN)
        config, error = omg_part_config(_plugin())
        if error:
            return Response({"detail": error}, status=status.HTTP_400_BAD_REQUEST)
        state, errors = save_connector_accessories(part, config, request.data.get("kinds") or [],
                                                   link_related=request.data.get("link_related", True) is not False)
        if errors:
            return Response({"detail": " ".join(errors), "errors": errors}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"error": None, "can_edit": True, **state})


def is_accessory_part(part, config):
    """Component Type is one of OMG's accessory kinds (Lock, Boot... - from OMG's part profiles)."""
    config = config or {}
    name = ((config.get("roles") or {}).get("part.component_type") or {}).get("template") or "Component Type"
    value = (get_part_parameter_str(part, name) or "").strip().lower()
    return bool(value) and value in {t.lower() for t in config.get("accessory_types") or []}
