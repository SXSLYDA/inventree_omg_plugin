"""
Wire gauge auto-fill: when a part's "Gauge mm2" or "Gauge AWG" parameter is
saved in InvenTree, fill the OTHER one from OMG's AWG / mm² table (OMG
Default Settings, your company's crossovers - e.g. 18 AWG = 0.8 mm²) - only
if it's still blank, so a value someone typed is never overwritten (and the
second save can't bounce back and forth). The AWG value is tidied to just
the size ("16 AWG" -> "16"), and validate_gauge_value() refuses an AWG that
isn't a standard size (40..1, 1/0..4/0) or a mm² that isn't a number.

Parameter names and the AWG <-> mm² table come from OMG
(/api/parts/wire-parameters/, OMG > InvenTree Settings > Part Parameters),
cached for NAMES_TTL; the built-in copies below are used if OMG can't be
reached. Driven by InvenTree's own model events (EventMixin), so it works
however the parameter was saved - the part page, the API, an import
(events common_parameter.created / .saved - InvenTree 1.2+ parameters).

Needs InvenTree's "Enable event integration" plugin setting on, and the
background worker running (events are processed there).
"""
import logging
import math
import re
import time

import requests

from .inventree_native_lookup import (_part_content_type, get_part_parameter_str, parameter_template,
                                      part_parameter_lookup)

logger = logging.getLogger("inventree")

EVENTS = {"common_parameter.created", "common_parameter.saved"}

NAMES_TTL = 600  # seconds

# Same as OMG's harness/wire_gauges.py - used only if OMG can't be reached.
DEFAULT_NAMES = {"wire.gauge_mm2": "Gauge mm2", "wire.gauge_awg": "Gauge AWG"}
DEFAULT_AWG_TO_MM2 = {
    "22": "0.35", "20": "0.5", "18": "0.75", "17": "1", "16": "1.5", "14": "2.5", "12": "4",
    "10": "6", "8": "10", "6": "16", "4": "25", "2": "35", "1": "50",
}

_cache = {"at": 0.0, "names": None, "table": None}


def _omg_config(plugin):
    """(parameter names, AWG->mm² table) from OMG, cached; built-in defaults if OMG can't be reached."""
    if _cache["names"] and time.time() - _cache["at"] < NAMES_TTL:
        return _cache["names"], _cache["table"]
    names, table = dict(DEFAULT_NAMES), dict(DEFAULT_AWG_TO_MM2)
    base_url = plugin.get_setting("OMG_HARNESS_API_URL") if plugin else None
    token = plugin.get_setting("OMG_HARNESS_API_TOKEN") if plugin else None
    if base_url and token:
        try:
            resp = requests.get(f"{base_url.rstrip('/')}/api/parts/wire-parameters/",
                                headers={"Authorization": f"Token {token}"}, timeout=10)
            resp.raise_for_status()
            body = resp.json()
            names.update({k: v for k, v in (body.get("parameter_names") or {}).items() if v})
            table = body.get("awg_to_mm2") or table
        except (requests.RequestException, ValueError) as exc:
            logger.warning("OMG gauge auto-fill: couldn't read OMG's wire parameter names (%s) - using defaults", exc)
    _cache.update(at=time.time(), names=names, table=table)
    return names, table


_NUMBER = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
_AWG_RE = re.compile(r"^(?:(0{2,4})|([1-4])/0|(\d{1,2}))$")   # "00" before plain digits


def _number(raw):
    match = _NUMBER.search(str(raw or ""))
    return float(match.group(0).replace(",", ".")) if match else None


# Same standard sizes as OMG (harness/wire_gauges.py STANDARD_AWG).
STANDARD_AWG = [str(n) for n in range(40, 0, -1)] + ["1/0", "2/0", "3/0", "4/0"]


def normalise_awg(raw):
    """
    One of STANDARD_AWG, or None (same rules as OMG): '16', '16 AWG', '#16'
    -> '16'; '2/0', '00' -> '2/0'; '0' -> '1/0'; '41', '1.5' -> None.
    """
    text = str(raw or "").upper().replace("AWG", "").replace("#", "").replace(" ", "")
    if text.endswith(".0"):
        text = text[:-2]
    match = _AWG_RE.match(text)
    if not match:
        return None
    if match.group(1) is not None:
        awg = f"{len(match.group(1))}/0"      # "00" -> "2/0"
    elif match.group(2) is not None:
        awg = f"{match.group(2)}/0"
    else:
        awg = "1/0" if int(match.group(3)) == 0 else str(int(match.group(3)))
    return awg if awg in STANDARD_AWG else None


def _awg_number(awg_text):
    return 1 - int(awg_text.split("/")[0]) if "/" in awg_text else int(awg_text)


def _awg_exact_mm2(awg):
    diameter_mm = 0.127 * 92 ** ((36 - awg) / 39)
    return math.pi / 4 * diameter_mm ** 2


def mm2_for_awg(raw, table):
    """'16' -> '1.5' (table), else the exact cross-section; None if unreadable."""
    awg = normalise_awg(raw)
    if awg is None:
        return None
    if awg in table:
        return str(table[awg])
    return f"{round(_awg_exact_mm2(_awg_number(awg)), 2):g}"


def awg_for_mm2(raw, table):
    """'1.5' -> '16' (table, first listed wins), else the nearest size; None if unreadable."""
    mm2 = _number(raw)
    if not mm2 or mm2 <= 0:
        return None
    for awg, value in table.items():
        if abs(float(value) - mm2) < 1e-6:
            return str(awg)
    return min(STANDARD_AWG, key=lambda awg: abs(_awg_exact_mm2(_awg_number(awg)) - mm2))


def _parameter_and_part(parameter_id):
    """(parameter, Part) for an event's parameter id, or (None, None) if it isn't a part parameter."""
    from common.models import Parameter
    from part.models import Part
    param = Parameter.objects.filter(pk=parameter_id).select_related("template").first()
    if param is None or param.model_type_id != _part_content_type().pk:
        return None, None
    return param, Part.objects.filter(pk=param.model_id).first()


def process_gauge_event(plugin, event, **kwargs):
    """Fill the other gauge parameter if this event saved one of them. Never raises."""
    try:
        param, part = _parameter_and_part(kwargs.get("id"))
        if param is None or part is None or not param.data:
            return
        names, table = _omg_config(plugin)
        saved = param.template.name.strip().lower()
        mm2_name, awg_name = names["wire.gauge_mm2"], names["wire.gauge_awg"]
        if saved == awg_name.strip().lower():
            tidy = normalise_awg(param.data)
            if tidy and tidy != param.data.strip():
                type(param).objects.filter(pk=param.pk).update(data=tidy)   # "16 AWG" -> "16", no new event
        if saved == mm2_name.strip().lower():
            other, value = awg_name, awg_for_mm2(param.data, table)
        elif saved == awg_name.strip().lower():
            other, value = mm2_name, mm2_for_awg(param.data, table)
        else:
            return
        if value is None or get_part_parameter_str(part, other):
            return  # unreadable, or already set by someone - never overwrite
        from common.models import Parameter
        template = parameter_template(other)
        if template is None:
            logger.info("OMG gauge auto-fill: no '%s' parameter template in InvenTree - nothing filled", other)
            return
        Parameter.objects.get_or_create(**part_parameter_lookup(part, template), defaults={"data": value})
        logger.info("OMG gauge auto-fill: %s %s = %s (from %s = %s)", part, other, value, param.template.name, param.data)
    except Exception:
        logger.exception("OMG gauge auto-fill failed for event %s %s", event, kwargs)


def validate_gauge_value(plugin, parameter, data):
    """
    ValidationMixin hook (InvenTree refuses the save if this raises):
    a value for the Gauge AWG parameter must be a standard AWG size (40..1,
    1/0..4/0, just the size); a Gauge mm2 value must be a positive number.
    Other parameters aren't touched. Returns None so other plugins still run.
    """
    from django.core.exceptions import ValidationError
    template = getattr(parameter, "template", None)
    if template is None or data in (None, ""):
        return None
    names, _table = _omg_config(plugin)
    name = template.name.strip().lower()
    if name == names["wire.gauge_awg"].strip().lower() and normalise_awg(data) is None:
        raise ValidationError(f"'{data}' isn't an AWG size - use 40 to 1, or 1/0 to 4/0 (just the size, e.g. 16).")
    if name == names["wire.gauge_mm2"].strip().lower() and not ((_number(data) or 0) > 0):
        raise ValidationError(f"'{data}' isn't a wire size in mm² (e.g. 1.5).")
    return None
