"""
OMG's part parameters from a Mouser result - so a part created from Mouser
arrives with OMG's Component Type, gauges, colours, contact count, series
and so on already filled in, under the InvenTree template names OMG uses
(OMG > InvenTree Settings > Part Parameters, read from OMG's
/api/parts/part-profiles/).

Used by:
  - mouser_supplier.get_parameters(): InvenTree's "Import from Supplier"
    wizard. The values appear on its Parameters step, ticked, for a person
    to check, edit or untick before anything is saved.
  - pending_parts.import_pending(): parts OMG picked from Mouser, created
    from the "Mouser Import - Pending Parts" dashboard item - the kind OMG
    sends (connector, wire...) gives the Component Type.

Only what Mouser states clearly is filled; anything unclear is left blank
for the OMG Part Setup panel (which flags missing required values). Only
the roles in the guessed Component Type's profile are filled, so a contact
never gets wire parameters. Pure functions - no InvenTree imports.
"""
import re

from .gauge_autofill import normalise_awg
from .selection_match import join_items, split_items, suggest

# Component Type from the words in a Mouser listing (description + attribute
# values), first match wins - most specific first.
TYPE_WORDS = (
    # Deutsch wedgelocks are the connector's Lock (W2S...); Secondary Lock is
    # for housings that need a separate TPA as well (e.g. Bosch EMS).
    ('Secondary Lock', (r'secondary lock', r'\btpa\b', r'terminal position assur')),
    ('Lock', (r'\block\b', r'wedge ?lock', r'\blatch\b', r'\bwedge\b')),
    ('Boot', (r'\bboot\b',)),
    ('Backshell', (r'back ?shell',)),
    ('Cover', (r'\bcover\b', r'\bcap\b', r'dust cap')),
    ('Seal', (r'\bseal\b', r'grommet', r'wire seal')),
    ('Blank', (r'cavity plug', r'sealing plug', r'\bblank', r'filler plug', r'dummy plug')),
    # a ring lug / spade / eyelet used on its own (pins - + * #), not a contact in a housing
    ('Terminal', (r'ring terminal', r'\blug\b', r'\bspade\b', r'\beyelet\b', r'quick disconnect', r'\bfork terminal')),
    ('Contact', (r'\bcontact\b', r'\bterminal\b', r'\bsocket contact', r'\bpin contact', r'crimp')),
    ('Multicore', (r'multi.?conductor', r'multicore', r'\bcable\b')),
    ('Wire', (r'\bwire\b', r'hook.?up', r'\btxl\b', r'\bgxl\b', r'\bsxl\b')),
    ('Connector', (r'connector', r'housing', r'receptacle', r'\bplug\b')),
)

# OMG's kind for a pending part -> Component Type.
KIND_TYPES = {'connector': 'Connector', 'wire': 'Wire', 'conductor': 'Wire', 'multicore': 'Multicore',
              'contact': 'Contact', 'seal': 'Seal', 'blank': 'Blank'}

ATTR_KEYS = {
    'insulation': ('Insulation Material', 'Jacket Material', 'Insulation', 'Insulation Type'),
    'conductor_material': ('Conductor Material', 'Material - Conductor', 'Conductor'),
    'voltage': ('Voltage Rating', 'Rated Voltage', 'Voltage Rating DC', 'Voltage Rating AC'),
    'temperature_max': ('Maximum Operating Temperature', 'Max Operating Temperature'),
    'temperature_range': ('Operating Temperature Range', 'Operating Temperature'),
    'series': ('Series',),
    'strip_length': ('Strip Length', 'Wire Strip Length'),
    'core_count': ('Number of Conductors', 'Conductors', 'Number of Cores'),
}

_AWG_RANGE = re.compile(r'(\d{1,2}|[1-4]/0)\s*(?:AWG)?\s*(?:-|to|~)\s*(\d{1,2}|[1-4]/0)\s*AWG', re.I)
_AWG_ONE = re.compile(r'(\d{1,2}|[1-4]/0)\s*AWG', re.I)
_MM2_RANGE = re.compile(r'(\d+(?:[.,]\d+)?)\s*(?:mm\^?2|mm²)?\s*(?:-|to|~)\s*(\d+(?:[.,]\d+)?)\s*(?:mm\^?2|mm²|sq\.? ?mm)', re.I)
_MM2_ONE = re.compile(r'(\d+(?:[.,]\d+)?)\s*(?:mm\^?2|mm²|sq\.? ?mm)', re.I)
_NUMBER = re.compile(r'-?\d+(?:[.,]\d+)?')


def _text(data):
    attrs = data.get('attributes') or {}
    return ' '.join([str(data.get('description') or '')] + [str(v) for v in attrs.values()]).lower()


def _attr(data, key):
    attrs = data.get('attributes') or {}
    for name in ATTR_KEYS[key]:
        for k, v in attrs.items():
            if k.strip().lower() == name.lower() and str(v or '').strip() not in ('', '-'):
                return str(v).strip()
    return ''


def _num(text):
    m = _NUMBER.search(str(text or ''))
    return f"{float(m.group(0).replace(',', '.')):g}" if m else ''


def guess_component_type(data):
    """Component Type for a Mouser result, or '' if nothing is clear."""
    text = _text(data)
    for ctype, patterns in TYPE_WORDS:
        if any(re.search(p, text) for p in patterns):
            return ctype
    return ''


def _gauges(data):
    """{'awg': '16'} / {'mm2': '1.5'} for a single size, or {'awg_range': (thin, thick)} / {'mm2_range': ...}."""
    attrs = data.get('attributes') or {}
    sources = [v for k, v in attrs.items() if re.search(r'gauge|awg|wire size|conductor size|cross.?section|csa', k, re.I)]
    for value in sources:
        m = _AWG_RANGE.search(str(value))
        if m:
            a, b = normalise_awg(m.group(1)), normalise_awg(m.group(2))
            if a and b:
                # thin end = bigger AWG number
                thin, thick = sorted((a, b), key=lambda x: -(1 - int(x[0]) if '/' in x else int(x)))
                return {'awg_range': (thin, thick)} if thin != thick else {'awg': thin}
        m = _MM2_RANGE.search(str(value))
        if m:
            lo, hi = sorted((float(m.group(1).replace(',', '.')), float(m.group(2).replace(',', '.'))))
            return {'mm2_range': (f"{lo:g}", f"{hi:g}")} if lo != hi else {'mm2': f"{lo:g}"}
    for value in sources:
        m = _AWG_ONE.search(str(value))
        if m and normalise_awg(m.group(1)):
            return {'awg': normalise_awg(m.group(1))}
        m = _MM2_ONE.search(str(value))
        if m:
            return {'mm2': f"{float(m.group(1).replace(',', '.')):g}"}
    return {}


def _side(data):
    text = _text(data)
    if re.search(r'receptacle|\brcpt\b|\brecp\b', text):
        return 'Receptacle'
    if re.search(r'\bplug\b', text):
        return 'Plug'
    return ''


def _mating_side(data):
    """
    A splice plug's Side: like every accessory, the side of the connector it
    fits - a splice plug that is a plug fits a receptacle. "Splice plug" /
    "jumper plug" in the name doesn't count as saying it's a plug.
    """
    text = re.sub(r'\b(?:splice|jumper|buss(?:ed)?)\s+plugs?\b', '', _text(data))
    if re.search(r'receptacle|\brcpt\b|\brecp\b', text):
        return 'Plug'
    if re.search(r'\bplug\b', text):
        return 'Receptacle'
    return ''


def _max_temperature(data):
    direct = _num(_attr(data, 'temperature_max'))
    if direct:
        return direct
    numbers = _NUMBER.findall(_attr(data, 'temperature_range').replace(' ', ''))
    return f"{float(numbers[-1].replace(',', '.')):g}" if numbers else ''


def _series(data):
    value = re.sub(r'\bseries\b', '', _attr(data, 'series'), flags=re.I).strip(' -')
    return value


def omg_values(data, component_type):
    """{role: value} OMG can fill for this Mouser result, given its Component Type."""
    values = {'part.component_type': component_type} if component_type else {}
    gauges = _gauges(data)
    if component_type in ('Wire', 'Multicore'):
        if 'awg' in gauges:
            values['wire.gauge_awg'] = gauges['awg']
        if 'mm2' in gauges:
            values['wire.gauge_mm2'] = gauges['mm2']
        values['wire.insulation'] = _attr(data, 'insulation')
        values['wire.conductor_material'] = _attr(data, 'conductor_material')
        values['wire.primary_color'] = data.get('primary_color') or ''
        values['wire.secondary_color'] = data.get('secondary_color') or ''
        values['wire.max_temperature'] = _max_temperature(data)
        values['wire.voltage_rating'] = _num(_attr(data, 'voltage'))
        if data.get('outer_diameter'):
            values['wire.insulation_od'] = f"{float(data['outer_diameter']):g}"
        if component_type == 'Multicore':
            values['multicore.core_count'] = _num(_attr(data, 'core_count'))
    elif component_type in ('Contact', 'Terminal'):
        if 'awg_range' in gauges:
            values['contact.min_gauge'], values['contact.max_gauge'] = (f"{a} AWG" for a in gauges['awg_range'])
        elif 'awg' in gauges:
            values['contact.min_gauge'] = values['contact.max_gauge'] = f"{gauges['awg']} AWG"
        elif 'mm2_range' in gauges:
            values['contact.min_gauge'], values['contact.max_gauge'] = gauges['mm2_range']
        elif 'mm2' in gauges:
            values['contact.min_gauge'] = values['contact.max_gauge'] = gauges['mm2']
        values['contact.strip_length'] = _num(_attr(data, 'strip_length'))
    elif component_type == 'Connector':
        if data.get('contact_count'):
            values['connector.contact_count'] = str(data['contact_count'])
        values['connector.series'] = _series(data)
        values['connector.side'] = _side(data)
    elif component_type == 'Splice Plug':
        values['connector.series'] = _series(data)
        values['connector.side'] = _mating_side(data)
    elif component_type:      # an accessory kind (Lock, Boot, Cover...)
        values['connector.series'] = _series(data)
        values['connector.side'] = _side(data)
    return {role: value for role, value in values.items() if str(value or '').strip()}


def _to_list(value, values, per_item):
    """
    `value` as its selection list's own spelling ("Deutsch DT Series" -> "DT"),
    or '' if nothing in the list matches - better blank (the Part Setup panel
    flags it) than a near-miss that OMG's matching would never find.
    """
    if per_item:
        items = [suggest(item, values) for item in split_items(value)]
        return join_items(item for item in items if item)
    return suggest(value, values) or ''


def omg_parameters(data, config, component_type=None, lists=None):
    """
    [(template name, value)] for a Mouser result, using OMG's config
    (/api/parts/part-profiles/): only roles in the Component Type's profile,
    colours mapped to OMG's colour list names. component_type: known type
    (a pending part's kind), else guessed from the listing. lists: {role:
    [values]} - OMG's selection lists (selection_lists.lists_for_roles);
    a value is matched to its list, and left out if nothing matches.
    """
    if not config:
        return []
    ctype = component_type or guess_component_type(data)
    profile = (config.get('profiles') or {}).get(ctype) or {}
    allowed = set(profile.get('required', [])) | set(profile.get('optional', [])) | {'part.component_type'}
    colours = {}
    for name, codes, _hex in config.get('colours') or []:
        for word in [name] + str(codes or '').split(','):
            key = re.sub(r'[\s_/\-]+', '', str(word)).upper()
            if key:
                colours[key] = name
    out = []
    for role, value in omg_values(data, ctype).items():
        if role not in allowed:
            continue
        if role in (config.get('colour_roles') or []):
            value = colours.get(re.sub(r'[\s_/\-]+', '', value).upper(), value)
        if lists and lists.get(role):
            per_item = bool(((config.get('selection_lists') or {}).get(role) or {}).get('per_item'))
            value = _to_list(value, lists[role], per_item)
            if not value:
                continue
        template = ((config.get('roles') or {}).get(role) or {}).get('template')
        if template:
            out.append((template, value))
    return out
