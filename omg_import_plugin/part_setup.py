"""
Backend for the two part panels (PartSetupPanel.tsx):

  OMG Part Setup  - pick a Component Type (Wire, Contact, Seal, Blank,
                    Connector, Multicore) and fill in every parameter that
                    type needs in one go. Gauge mm² / AWG fill each other
                    from OMG's AWG / mm² table; colours are saved as the
                    company colour list's names ("RD" -> "Red").
  OMG Cavities    - a connector's Cavity Map as a grid (cavities, series,
                    sealing, max OD), plus which contacts / seals / blanks
                    are its related parts, with InvenTree's parts of each
                    series offered to add.

What a type needs, the template names, AWG table and colour list all come
from OMG (/api/parts/part-profiles/) - OMG owns the part logic; this only
reads and writes InvenTree parameters and related parts. Templates are never
created here: a missing one is reported (OMG's InvenTree Settings page can
create them).
"""
import logging
import re
import time

import requests
from django.db import transaction
from django.db.models import Q
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import omg_cavity_map as cmap
from .gauge_autofill import awg_for_mm2, mm2_for_awg, normalise_awg
from .inventree_native_lookup import (_part_content_type, get_part_parameter_str, parameter_template,
                                      part_parameter_lookup)

logger = logging.getLogger("inventree")

CONFIG_TTL = 600
_config_cache = {"at": 0.0, "data": None}


# ==================== OMG's part profiles ====================

def omg_part_config(plugin, force=False):
    """(config, error) - OMG's /api/parts/part-profiles/ (cached CONFIG_TTL), or (None, message)."""
    if not force and _config_cache["data"] and time.time() - _config_cache["at"] < CONFIG_TTL:
        return _config_cache["data"], None
    base_url = plugin.get_setting("OMG_HARNESS_API_URL") if plugin else None
    token = plugin.get_setting("OMG_HARNESS_API_TOKEN") if plugin else None
    if not base_url or not token:
        return None, "Set the OMG Harness API URL and token in this plugin's settings first."
    try:
        resp = requests.get(f"{base_url.rstrip('/')}/api/parts/part-profiles/",
                            headers={"Authorization": f"Token {token}"}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        if _config_cache["data"]:
            return _config_cache["data"], None      # stale beats nothing
        return None, f"Couldn't read OMG's part setup ({exc}) - update OMG, or check the URL/token."
    _config_cache.update(at=time.time(), data=data)
    return data, None


def _plugin():
    from plugin.registry import registry
    return registry.get_plugin("omg-harness-import")


# ==================== Parameters ====================

def _template(name):
    return parameter_template(name)


def _set_parameter(part, template, value):
    """Set (or clear, if value is blank) one parameter on a part."""
    from common.models import Parameter
    lookup = part_parameter_lookup(part, template)
    if value in (None, ""):
        Parameter.objects.filter(**lookup).delete()
        return
    param = Parameter.objects.filter(**lookup).first()
    if param is None:
        param = Parameter(**lookup)
    param.data = str(value)
    param.full_clean()      # runs InvenTree's own checks (units, choices) and plugin validation
    param.save()


def _colour_lookup(colours):
    """{any spelling/code (upper, no separators): list name} from OMG's colour list."""
    lookup = {}
    for name, codes, _hex in colours or []:
        for word in [name] + [c for c in str(codes or "").split(",")]:
            key = re.sub(r"[\s_/\-]+", "", str(word)).upper()
            if key:
                lookup[key] = name
    return lookup


def _clean_value(role, raw, config):
    """(value, error) for one submitted role value - gauges and colours tidied."""
    value = str(raw if raw is not None else "").strip()
    if value == "":
        return "", None
    if role in config.get("awg_roles", []):
        awg = normalise_awg(value)
        if awg is None:
            return None, f"'{value}' isn't an AWG size - use 40 to 1, or 1/0 to 4/0."
        return awg, None
    if role in config.get("colour_roles", []):
        key = re.sub(r"[\s_/\-]+", "", value).upper()
        return _colour_lookup(config.get("colours")).get(key, value), None
    return value, None


def _role_template(config, role):
    return (config.get("roles", {}).get(role) or {}).get("template") or ""


def part_setup_state(part, config):
    """Everything the Setup panel shows for one part."""
    roles = config.get("roles", {})
    type_name = _role_template(config, "part.component_type")
    component_type = get_part_parameter_str(part, type_name) if type_name else None
    profiles = config.get("profiles", {})
    profile_key = next((k for k in profiles if k.lower() == (component_type or "").strip().lower()), None)

    wanted = {"part.component_type"}
    for profile in profiles.values():
        wanted.update(profile.get("required", []))
        wanted.update(profile.get("optional", []))
    fields = {}
    for role in sorted(wanted):
        meta = roles.get(role) or {}
        name = meta.get("template") or ""
        template = _template(name) if name else None
        fields[role] = {
            "template": name,
            "label": meta.get("label", role),
            "units": (getattr(template, "units", "") or meta.get("units", "")) if template else meta.get("units", ""),
            "exists": template is not None,
            "choices": [c.strip() for c in str(getattr(template, "choices", "") or "").split(",") if c.strip()]
            if template else [],
            "value": get_part_parameter_str(part, name) if template else None,
        }
    missing = []
    if profile_key:
        missing = [r for r in profiles[profile_key].get("required", []) if not (fields.get(r) or {}).get("value")]
    return {
        "part": {"pk": part.pk, "name": part.name, "ipn": part.IPN or "", "description": part.description or ""},
        "component_type": component_type or "",
        "profile": profile_key,
        "profiles": profiles,
        "fields": fields,
        "missing_required": missing,
        "standard_awg": config.get("standard_awg", []),
        "colours": [row[0] for row in config.get("colours") or []],
        "colour_roles": config.get("colour_roles", []),
        "awg_roles": config.get("awg_roles", []),
    }


def save_part_setup(part, config, component_type, values):
    """
    Write Component Type and the submitted role values (blank = remove).
    Gauge mm² / AWG fill each other when only one is given. Returns
    (errors {role: message}, saved [roles]). All or nothing.
    """
    errors, cleaned = {}, {}
    if component_type is not None:
        values = {**values, "part.component_type": component_type}
    for role, raw in values.items():
        name = _role_template(config, role)
        if not name:
            errors[role] = "OMG doesn't know this field."
            continue
        value, error = _clean_value(role, raw, config)
        if error:
            errors[role] = error
            continue
        cleaned[role] = value

    # mm² <-> AWG: fill the blank one from OMG's table
    table = config.get("awg_to_mm2") or {}
    mm2, awg = cleaned.get("wire.gauge_mm2"), cleaned.get("wire.gauge_awg")
    if "wire.gauge_mm2" in cleaned or "wire.gauge_awg" in cleaned:
        if mm2 and not awg:
            cleaned["wire.gauge_awg"] = awg_for_mm2(mm2, table) or ""
        elif awg and not mm2:
            cleaned["wire.gauge_mm2"] = mm2_for_awg(awg, table) or ""

    templates = {}
    for role, value in cleaned.items():
        template = _template(_role_template(config, role))
        if template is None:
            if value:
                errors[role] = (f"No '{_role_template(config, role)}' parameter template in InvenTree - create it "
                                f"(OMG > InvenTree Settings > Part Parameters can) and try again.")
            continue
        templates[role] = template
    if errors:
        return errors, []

    from django.core.exceptions import ValidationError
    saved = []
    try:
        with transaction.atomic():
            for role, template in templates.items():
                try:
                    _set_parameter(part, template, cleaned[role])
                except ValidationError as exc:
                    errors[role] = "; ".join(exc.messages)
                    raise
                saved.append(role)
    except ValidationError:
        return errors, []
    return {}, saved


# ==================== Cavities ====================

def _related_parts(part):
    from part.models import PartRelated
    rows = PartRelated.objects.filter(Q(part_1=part) | Q(part_2=part)).select_related("part_1", "part_2")
    return [r.part_2 if r.part_1_id == part.pk else r.part_1 for r in rows]


def _part_info(part, config):
    type_name = _role_template(config, "part.component_type")
    series_name = _role_template(config, "part.contact_series")
    size_name = _role_template(config, "contact.size")
    series = (get_part_parameter_str(part, series_name) if series_name else None) or \
             (get_part_parameter_str(part, size_name) if size_name else None) or ""
    info = {"pk": part.pk, "name": part.name, "ipn": part.IPN or "", "description": part.description or "",
            "component_type": (get_part_parameter_str(part, type_name) if type_name else None) or "",
            "series": series}
    for role in ("contact.min_gauge", "contact.max_gauge", "seal.min_od", "seal.max_od"):
        name = _role_template(config, role)
        if name:
            info[role] = get_part_parameter_str(part, name) or ""
    return info


def _parts_of_series(config, series_list):
    """{norm series: [part info]} - every InvenTree part whose Contact Series is one of these."""
    from part.models import Part
    from common.models import Parameter
    name = _role_template(config, "part.contact_series")
    wanted = {cmap.norm_series(s) for s in series_list if s}
    if not name or not wanted:
        return {}
    pairs = (Parameter.objects.filter(template__name__iexact=name, model_type=_part_content_type())
             .values_list("model_id", "data"))
    pks_by_series = {}
    for pk, data in pairs:
        key = cmap.norm_series(data)
        if key in wanted:
            pks_by_series.setdefault(key, []).append(pk)
    result = {}
    parts = {p.pk: p for p in Part.objects.filter(pk__in=[pk for pks in pks_by_series.values() for pk in pks],
                                                 active=True)}
    for key, pks in pks_by_series.items():
        result[key] = [_part_info(parts[pk], config) for pk in pks if pk in parts]
    return result


def cavity_state(part, config):
    """Everything the Cavity panel shows for one connector."""
    map_name = _role_template(config, "connector.cavity_map")
    raw = (get_part_parameter_str(part, map_name) if map_name else None) or ""
    groups_name = _role_template(config, "connector.cavity_groups")
    from_groups = False
    ranges = cmap.parse_cavity_map(raw)
    if not raw and groups_name:
        old = get_part_parameter_str(part, groups_name) or ""
        if old:
            ranges, from_groups = cmap.parse_cavity_groups_as_map(old), True
    related = [_part_info(p, config) for p in _related_parts(part)]
    series = [r.series for r in ranges if r.series]
    return {
        "part": {"pk": part.pk, "name": part.name, "ipn": part.IPN or ""},
        "map_template": map_name,
        "map_template_exists": bool(map_name and _template(map_name)),
        "raw": raw,
        "from_cavity_groups": from_groups,
        "rows": [{"cavities": ", ".join(r.tokens), "series": r.series, "sealing": r.sealing,
                  "max_od": r.max_od, "problems": r.problems} for r in ranges],
        "related": related,
        "by_series": _parts_of_series(config, series),
        "sealings": list(cmap.SEALINGS),
    }


def format_cavity_map(rows):
    """[{cavities, series, sealing, max_od}] -> Cavity Map text; (text, errors)."""
    entries, errors = [], []
    for i, row in enumerate(rows or [], start=1):
        cavities = ", ".join(t.strip() for t in str(row.get("cavities") or "").split(",") if t.strip())
        series = str(row.get("series") or "").strip()
        if not cavities and not series:
            continue
        if not cavities or not series:
            errors.append(f"Row {i}: give both the cavities and the series.")
            continue
        if "/" in series or ";" in series or ":" in series:
            errors.append(f"Row {i}: the series can't contain / ; or :")
            continue
        sealing = str(row.get("sealing") or cmap.SEALING_NONE).lower()
        if sealing not in cmap.SEALINGS:
            errors.append(f"Row {i}: sealing must be individual, mat or none.")
            continue
        parts = [series, sealing]
        if row.get("max_od") not in (None, ""):
            try:
                od = float(str(row["max_od"]).replace(",", "."))
                if od <= 0:
                    raise ValueError
                parts.append(f"maxod {od:g}")
            except ValueError:
                errors.append(f"Row {i}: max OD must be a positive number (mm).")
                continue
        entries.append(f"{cavities}: " + " / ".join(parts))
    return "; ".join(entries), errors


def save_cavities(part, config, rows, related_add, related_remove):
    """Write the Cavity Map parameter and add / remove related parts. Returns errors (list)."""
    from django.core.exceptions import ValidationError
    from part.models import Part, PartRelated
    text, errors = format_cavity_map(rows)
    if errors:
        return errors
    template = _template(_role_template(config, "connector.cavity_map"))
    if template is None:
        return [f"No '{_role_template(config, 'connector.cavity_map')}' parameter template in InvenTree - create "
                f"it (OMG > InvenTree Settings > Part Parameters can) and try again."]
    if len(text) > 500:
        return ["The cavity map is longer than InvenTree's 500-character parameter limit - "
                "combine rows into ranges (e.g. 1-12)."]
    try:
        with transaction.atomic():
            _set_parameter(part, template, text)
            current = {p.pk for p in _related_parts(part)}
            for pk in related_add or []:
                if int(pk) not in current and int(pk) != part.pk:
                    other = Part.objects.filter(pk=int(pk)).first()
                    if other is not None:
                        PartRelated.objects.create(part_1=part, part_2=other)
            for pk in related_remove or []:
                PartRelated.objects.filter(Q(part_1=part, part_2_id=int(pk)) | Q(part_2=part, part_1_id=int(pk))).delete()
    except ValidationError as exc:
        return list(exc.messages)
    return []


# ==================== API ====================

def _can_edit(user):
    return user.is_superuser or user.has_perm("part.change_part")


def _get_part(pk):
    from part.models import Part
    return Part.objects.filter(pk=pk).first()


class PartSetupView(APIView):
    """GET/POST /plugin/omg-harness-import/part-setup/<pk>/ - see module docstring."""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        part = _get_part(pk)
        if part is None:
            return Response({"detail": "Part not found."}, status=status.HTTP_404_NOT_FOUND)
        config, error = omg_part_config(_plugin(), force=request.query_params.get("refresh") == "1")
        if error:
            return Response({"error": error})
        return Response({"error": None, "can_edit": _can_edit(request.user), **part_setup_state(part, config)})

    def post(self, request, pk):
        part = _get_part(pk)
        if part is None:
            return Response({"detail": "Part not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _can_edit(request.user):
            return Response({"detail": "You don't have permission to change parts."}, status=status.HTTP_403_FORBIDDEN)
        config, error = omg_part_config(_plugin())
        if error:
            return Response({"detail": error}, status=status.HTTP_400_BAD_REQUEST)
        values = request.data.get("values") or {}
        if not isinstance(values, dict):
            return Response({"detail": "values must be an object."}, status=status.HTTP_400_BAD_REQUEST)
        errors, saved = save_part_setup(part, config, request.data.get("component_type"), values)
        if errors:
            return Response({"detail": "Some fields need fixing.", "errors": errors}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"error": None, "saved": saved, "can_edit": True, **part_setup_state(part, config)})


class CavitySetupView(APIView):
    """GET/POST /plugin/omg-harness-import/cavity-setup/<pk>/ - see module docstring."""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        part = _get_part(pk)
        if part is None:
            return Response({"detail": "Part not found."}, status=status.HTTP_404_NOT_FOUND)
        config, error = omg_part_config(_plugin())
        if error:
            return Response({"error": error})
        series = [s for s in request.query_params.get("series", "").split("|") if s]
        state = cavity_state(part, config)
        if series:   # the panel asks for parts of series typed but not saved yet
            state["by_series"].update(_parts_of_series(config, series))
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
        errors = save_cavities(part, config, request.data.get("rows") or [],
                               request.data.get("related_add") or [], request.data.get("related_remove") or [])
        if errors:
            return Response({"detail": " ".join(errors), "errors": errors}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"error": None, "can_edit": True, **cavity_state(part, config)})
