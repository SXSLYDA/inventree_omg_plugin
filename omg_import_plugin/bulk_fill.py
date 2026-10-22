"""
Auto-fill OMG's part parameters from what InvenTree already knows about
each part - its description, name and category - for the "Auto-fill" tab
of the OMG Parts dashboard item.

  preview(plugin, ...) -> for every part (not assemblies) that's missing
                          something OMG needs: the Component Type it is (its
                          own, or worked out) and the values found for its
                          BLANK parameters. Nothing is written.
  apply(plugin, items) -> writes the ticked values. Never overwrites a value a
                          part already has; InvenTree's own checks (units,
                          selection lists) still apply to every value.

The rules read descriptions like these (TE, Deutsch, Bosch, wire stock):
  "Recept., DT, 8 way, A key, gray"             Connector, DT, 8 cavities, Receptacle
  "Wire TXL 16AWG-Tinned Copper 125°C Black/Blue stripe"
                                                Wire, 16 AWG (+ mm² from OMG's table),
                                                TXL, Tinned Copper, 125, Black / Blue
  "Socket, solid, sz. 16, 16-20 AWG [1.50-0.50 mm2], gold"
                                                Contact, 20 AWG .. 16 AWG
  "Wire seal, 1.5K, ..." / "Cavity plug, ..." / "Backshell, HDSCS, ..."
                                                Seal / Blank / Backshell
and the category: "DT SERIES" gives the series, "1.50MM2" a wire's size.
Anything unclear is left blank for a person (OMG Part Setup panel) - a
missing value is flagged by OMG's data check, a wrong one isn't.

Values go through the same OMG config as everything else
(/api/parts/part-profiles/): only roles in the part's Component Type
profile, colours as OMG's colour-list names, and - once OMG's selection
lists have values - each value matched to its list's spelling.
"""
import re

from .gauge_autofill import awg_for_mm2, mm2_for_awg, normalise_awg
from .mouser_parameters import omg_values
from .part_setup import _set_parameter, _template
from .selection_lists import lists_for_roles, role_list
from .selection_match import join_items, split_items, suggest

# Component Type from the description (and category), most specific first.
# Checked before anything looser, so "Recept., S&F, sz. 2.8" is a contact,
# not a connector, and "Cavity plug" a blank, not a plug.
TYPE_RULES = (
    # left for a person: OMG treats these specially, or they aren't one part kind
    ('', (r'splice plug', r'connector seal,', r'^seal, front', r'panel nut', r'lockwasher', r'jam nut')),
    ('Secondary Lock', (r'secondary lock', r'\btpa\b')),
    ('Lock', (r'wedge ?lock', r'^lock\b', r'\block,')),
    ('Holding Plate', (r'holding plate',)),
    ('Backshell', (r'back ?shell', r'\bendbell\b')),
    ('Boot', (r'\bboot\b(?! adapter)',)),
    ('Cover', (r'\bcover\b', r'\bdust cap\b', r'^cap\b')),
    ('Blank', (r'cavity plug', r'sealing plug', r'blanking plug', r'\bdummy (?:terminal|plug)\b', r'filler plug')),
    ('Connector', (r'\bhousing\b',)),          # "Socket housing" is a connector, not a socket contact
    # a way count before the seal rule: "Recept., HDP20, 24 shell, 23 way, black, E seal, ..." is a connector
    ('Connector', (r'\b\d{1,3}\s*-?\s*way\b',)),
    ('Seal', (r'\bwire seal\b', r'^seal\b', r'single wire seal\b', r'\bseal, ')),
    # a ring lug / spade on its own: its "pins" are - + * #, no cavities
    ('Terminal', (r'\blug\b', r'\bspade\b', r'ring terminal', r'\beyelet\b', r'fork terminal')),
    ('Contact', (r'\bsz\. ?\d', r'\bs&f\b', r'^(?:size \d+ )?(?:pin|socket|tab)\b', r'\bcontact pin\b',
                 r'connector pin\b', r'connection socket\b', r'\bfemale\b.*\d(?:mm|mm2|mm²)',
                 r'\bconn socket\b', r'\bcrimp\b', r'\bterminal\b',
                 r'\d(?:[.,]\d+)?\s*mm2?\s*-\s*\d(?:[.,]\d+)?\s*mm', r'\d[.,]\d+\s*-\s*\d(?:[.,]\d+)?\s*mm',
                 r'\b(?:socket|pin)$')),      # a wire-size range: "0.50MM2 - 1.00MM2"
    ('Multicore', (r'multi.?core', r'multi.?conductor', r'\bcore cable\b')),
    ('Wire', (r'^wire\b', r'\b(?:txl|gxl|sxl)\b.*\bawg\b')),
    ('Connector', (r'^recept', r'^plug\b', r'housing', r'\bconnector\b', r'\d+\s*(?:way|pos)\b',
                   r'-pin plug', r'\breceptacle\b', r'\bplug\b')),
)

_DASHES = re.compile(r'[‐-―−]')
# cavities: "8 way" first, then "4POS", "70-pin", "2P" (so "196P EMS" + "91 Way" is 91)
_WAYS = (re.compile(r'(\d{1,3})\s*-?\s*way\b', re.I), re.compile(r'(\d{1,3})\s*pos\b', re.I),
         re.compile(r'(\d{1,3})\s*-?\s*pin\b', re.I), re.compile(r'\b(\d{1,2})p\b', re.I))
_TE_SERIES = re.compile(r'^(?:recept\.?(?: housing)?|plug|socket housing|tab housing|backshell|seal(?:, \w+)?|'
                        r'wedgelock|lock|cover|boot)\s*,\s*([A-Za-z][A-Za-z0-9 .\-]{0,18}?)\s*,', re.I)
_CATEGORY_SERIES = re.compile(r'^(.+?)\s+SERIES$', re.I)
_MM2_CATEGORY = re.compile(r'^(\d+(?:[.,]\d+)?)\s*MM2?$', re.I)
_INSULATION = re.compile(r'\b(TXL|GXL|SXL|PVC|XLPE|TEFZEL|SILICONE)\b', re.I)
_MATERIAL = re.compile(r'\b(tinned copper|bare copper|copper|aluminium|aluminum)\b', re.I)
_TEMP = re.compile(r'(\d{2,3})\s*°\s*C', re.I)


# Deutsch cavity maps from the part number, which says the family and the way count:
# DT04-08PA = DT, 8 way; DTM06-12SA = DTM, 12 way; DRC16-70SA = DRC, 70 way;
# HD16-3-96S = HD10, 3 way. DT / DTM / DTP / DRC number their cavities 1..N
# (DRC takes size 16 contacts), HD10 letters them A..; all of them
# seal with the housing's own grommet (OMG: "mat" - no wire seals). The series
# is the Contact Series their contacts get below, so map and contacts match.
DEUTSCH_SIZE_SERIES = {'16': 'DT-16', '20': 'DTM-20', '12': 'DTP-12'}
_DEUTSCH_HOUSING = (
    (re.compile(r'^(DTM|DTP|DT)0[46]-0?(\d{1,2})[PS]', re.I), {'DT': 'DT-16', 'DTM': 'DTM-20', 'DTP': 'DTP-12'}, False),
    (re.compile(r'^(DRC)\d{2}-(\d{2})[PS]', re.I), {'DRC': 'DT-16'}, False),
    (re.compile(r'^(AEC)\d{2}-(\d{2})[PS]', re.I), {'AEC': 'DT-16'}, False),
    (re.compile(r'^(HD1[046])-(\d)-\d+[PS]', re.I), {'HD14': 'DT-16', 'HD16': 'DT-16', 'HD10': 'DT-16'}, True),
)
# Deutsch HD30 / HDP20 (Deutsch HD30 & HDP20 technical manual, insert arrangements):
# HDP24-18-14PE = shell 18, insert 14, pins, E seal. Only the inserts with ONE
# contact size - the mixed ones (18-6, 18-20, 24-9, 24-14, ...) need each
# cavity's size, so they're left for the Cavities panel. Letters skip I, O and Q
# except 24-23 (A-X without I). maxod = the seal's largest wire OD for that size.
_HD_CYLINDRICAL = re.compile(r'^HD(?:P2|[BN]?3)[46]-(18|24)-(\d{1,2})[PS]([NTE])?', re.I)
HD_INSERTS = {
    '18-8': ('A-H', '12'), '18-14': ('A-H, J-N, P', '16'), '18-21': ('1-21', '20'),
    '24-16': ('A-H, J-N, P, R-S', '12'), '24-23': ('A-H, J-X', '16'), '24-31': ('1-31', '16'),
    '24-33': ('1-33', '20'),
}
HD_INSERT_COUNT = {'18-8': 8, '18-14': 14, '18-21': 21, '24-16': 16, '24-23': 23, '24-31': 31, '24-33': 33}
HD_SEAL_MAX_OD = {'20': {'N': 2.41, 'T': 2.41, 'E': 2.41}, '16': {'N': 3.40, 'T': 3.40, 'E': 3.05},
                  '12': {'N': 4.32, 'T': 4.32, 'E': 4.01}}
# Contact plating from the description ("..., gold", "..., nickel", "CuSn, pre-tin"):
# OMG puts gold contacts on twisted pairs and shielded cable cores
_PLATING = re.compile(r'\b(gold|nickel|silver|tin|tinned|pre-tin)\b', re.I)
PLATING_WORDS = {'gold': 'Gold', 'nickel': 'Nickel', 'silver': 'Silver', 'tin': 'Tin', 'tinned': 'Tin', 'pre-tin': 'Tin'}
_SHIELDED = re.compile(r'\b(?:shield(?:ed)?|screen(?:ed)?|j1939|can ?bus)\b', re.I)
# Molded-in contacts (buss bars, terminating resistors, diodes, J1939 "Y" connectors):
# nothing is crimped into their cavities, so no map - it would ask for a blank in each
_MOLDED = re.compile(r'\bbuss(?:es)?\b|terminating resistor|\bmolded\b|\bdiode\b', re.I)
# Caterpillar's own 70-pin ECM plugs (147-1445, 147-1446, 160-7689): 1-70, size 16 contacts
_CAT_70_PIN = re.compile(r'\b(?:cat|caterpillar)\b.*\b70[- ]?pin\b', re.I)
# Deutsch blanking / sealing plugs: 0413-217-1605 = size 16, 0413-204-2005 = size 20;
# 114017 (sz. 12-16) goes in size 16 cavities
_DEUTSCH_BLANK = re.compile(r'^0413-\d{3}-(\d{2})\d{2}$', re.I)
_DEUTSCH_BLANK_PARTS = {'114017': 'DT-16'}
# Deutsch contacts: 0460-202-16141 (solid pin, size 16), 0462-201-2031 (solid socket,
# size 20), 1060-16-0122 / 1062-16-0122 (stamped & formed, size 16)
_DEUTSCH_CONTACT = (re.compile(r'^046[02]-\d{3}-(\d{2})\d', re.I), re.compile(r'^106[02]-(\d{2})-', re.I))


def deutsch_cavity_map(name, ways=None, description=''):
    """('1-8: DT-16 / mat', 8) for a Deutsch DT / DTM / DTP / DRC / HD10 housing, else ('', 0)."""
    if _CAT_70_PIN.search(_norm_text(description)) and (not ways or int(ways) == 70):
        return '1-70: DT-16 / mat', 70
    hd = _HD_CYLINDRICAL.match(str(name or '').strip())
    if hd:
        insert = f"{hd.group(1)}-{hd.group(2)}"
        if insert not in HD_INSERTS or (ways and int(ways) != HD_INSERT_COUNT[insert]):
            return '', 0
        cavities, size = HD_INSERTS[insert]
        text = f"{cavities}: {DEUTSCH_SIZE_SERIES[size]} / mat"
        if hd.group(3):
            text += f" / maxod {HD_SEAL_MAX_OD[size][hd.group(3).upper()]:g}"
        return text, HD_INSERT_COUNT[insert]
    for regex, series_by_family, lettered in _DEUTSCH_HOUSING:
        m = regex.match(str(name or '').strip())
        if not m:
            continue
        count = int(m.group(2))
        if not 1 <= count <= (8 if lettered else 70) or (ways and int(ways) != count):
            return '', 0        # the description disagrees with the part number - leave it for a person
        series = series_by_family[m.group(1).upper()]
        cavities = (f"A-{chr(ord('A') + count - 1)}" if count > 1 else 'A') if lettered else \
            (f"1-{count}" if count > 1 else '1')
        return f"{cavities}: {series} / mat", count
    return '', 0


def deutsch_contact_series(name):
    """'DT-16' for a Deutsch size 16 contact (pin or socket, solid or stamped), else ''."""
    for regex in _DEUTSCH_CONTACT:
        m = regex.match(str(name or '').strip())
        if m:
            return DEUTSCH_SIZE_SERIES.get(m.group(1), '')
    return ''


def deutsch_blank_series(name):
    """'DT-16' for a Deutsch size 16 blanking / sealing plug, else ''."""
    name = str(name or '').strip()
    m = _DEUTSCH_BLANK.match(name)
    return DEUTSCH_SIZE_SERIES.get(m.group(1), '') if m else _DEUTSCH_BLANK_PARTS.get(name, '')


def _norm_text(text):
    return re.sub(r'\s+', ' ', _DASHES.sub('-', str(text or ''))).strip()


def guess_type(description, category=''):
    text = _norm_text(description).lower()
    for ctype, patterns in TYPE_RULES:
        if any(re.search(p, text) for p in patterns):
            return ctype       # '' = deliberately left for a person
    return ''


def _colours(text, colour_rows):
    """
    Primary / secondary colour from OMG's colour list, in the order they're
    written: "Black/Blue stripe" -> Black, Blue; "Dark Green" -> Dark Green.
    Names and the longer codes (GRAY, PURPLE) count; 1-2 letter codes don't
    (too easy to hit by accident in a description).
    """
    names = {}
    for row in colour_rows or []:
        name, codes = row[0], (row[1] if len(row) > 1 else '')
        for word in [name] + [c for c in str(codes or '').split(',') if len(c.strip()) > 2]:
            if word and word.strip():
                names[word.strip().lower()] = name
    words = re.findall(r'[A-Za-z]+', text)
    found, i = [], 0
    while i < len(words):
        pair = f"{words[i]} {words[i + 1]}".lower() if i + 1 < len(words) else ''
        if pair in names:
            match, i = names[pair], i + 2
        else:
            match, i = names.get(words[i].lower()), i + 1
        if match and match not in found:
            found.append(match)
    return (found + ['', ''])[:2]


def mouser_like(part, config):
    """
    The part as a Mouser-style result ({description, attributes, ...}) so
    OMG's own mapping (mouser_parameters.omg_values) can read it: the
    description, plus attributes worked out from it and the category.
    """
    description = _norm_text(part.description)
    category = part.category.name if part.category_id else ''
    attrs = {'Wire Size': description}
    cat_series = _CATEGORY_SERIES.match(category or '')
    te_series = _TE_SERIES.match(description)
    if te_series:
        attrs['Series'] = te_series.group(1).strip()
    elif cat_series:
        attrs['Series'] = cat_series.group(1).strip()
    cat_mm2 = _MM2_CATEGORY.match(category or '')
    if cat_mm2:
        attrs['Conductor Size'] = f"{float(cat_mm2.group(1).replace(',', '.')):g} mm2"
    for key, regex in (('Insulation', _INSULATION), ('Conductor Material', _MATERIAL)):
        m = regex.search(description)
        if m:
            attrs[key] = m.group(1).upper() if key == 'Insulation' else m.group(1).title()
    m = _TEMP.search(description)
    if m:
        attrs['Maximum Operating Temperature'] = m.group(1)
    data = {'description': re.sub(r'\brecei?pt\.?', 'receptacle', description, flags=re.I),
            'attributes': attrs, 'mpn': part.name}
    for regex in _WAYS:
        ways = regex.search(description)
        if ways:
            data['contact_count'] = int(ways.group(1))
            break
    primary, secondary = _colours(description, config.get('colours'))
    data['primary_color'], data['secondary_color'] = primary, secondary
    return data


def _profile(config, ctype):
    return (config.get('profiles') or {}).get(ctype) or {}


def _proposals(part, config, lists, current):
    """(component type, type_known, {role: value}) for one part - only roles that are blank on it."""
    type_template = config['roles']['part.component_type']['template']
    own_type = (current.get(type_template.lower()) or '').strip()
    profiles = config.get('profiles') or {}
    ctype = next((k for k in profiles if k.lower() == own_type.lower()), '') if own_type else ''
    if not ctype:
        ctype = guess_type(part.description, part.category.name if part.category_id else '')
    if not ctype or ctype not in profiles:
        return '', bool(own_type), {}
    data = mouser_like(part, config)
    values = omg_values(data, ctype)
    text = data['description'].lower()
    accessory = ctype not in ('Wire', 'Multicore', 'Connector', 'Terminal', 'Contact', 'Seal', 'Blank')
    if accessory:
        if 'plug' in text and 'receptacle' in text:
            values['connector.side'] = 'Both'      # "for plug or recept."
        if data.get('contact_count'):
            values['accessory.ways'] = str(data['contact_count'])
        angle = re.search(r'(\d{2,3})\s*°', text)
        if angle:
            values['accessory.exit_angle'] = {'90': '90', '45': '45', '180': 'Straight', '0': 'Straight'}.get(
                angle.group(1), '')
    if ctype == 'Connector' and not _MOLDED.search(data['description']):
        cavity_map, count = deutsch_cavity_map(part.name, data.get('contact_count'), part.description)
        own_count = (current.get((((config.get('roles') or {}).get('connector.contact_count') or {})
                                  .get('template') or '').lower()) or '').strip()
        if cavity_map and own_count and own_count != str(count):
            cavity_map = ''     # its Contact Count says otherwise - leave the map for a person
        series_list = lists.get('part.contact_series')
        if cavity_map and series_list:
            series = cavity_map.split(': ', 1)[1].split(' / ')[0]
            snapped = suggest(series, series_list)
            cavity_map = cavity_map.replace(f": {series} /", f": {snapped} /") if snapped else ''
        if cavity_map:
            values['connector.cavity_map'] = cavity_map
            values['connector.contact_count'] = str(count)
    if ctype == 'Contact' and not values.get('part.contact_series'):
        values['part.contact_series'] = deutsch_contact_series(part.name)
    if ctype == 'Contact':
        plating = _PLATING.search(text)
        if plating:
            values['contact.plating'] = PLATING_WORDS[plating.group(1).lower()]
    if ctype == 'Multicore' and _SHIELDED.search(text):
        values['multicore.shielded'] = 'Yes'
    if ctype == 'Blank' and not values.get('part.contact_series'):
        values['part.contact_series'] = deutsch_blank_series(part.name)
    if ctype == 'Seal':
        od = re.search(r'\[(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\]\s*ins', text) or \
            re.search(r'(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\s*od\b', text)
        if od:
            values['seal.min_od'], values['seal.max_od'] = od.group(1), od.group(2)
    if ctype in ('Wire', 'Multicore'):
        table = config.get('awg_to_mm2') or {}
        mm2 = re.match(r'([\d.]+)', data['attributes'].get('Conductor Size', ''))
        if not values.get('wire.gauge_mm2') and mm2:
            values['wire.gauge_mm2'] = mm2.group(1)
        if values.get('wire.gauge_awg') and not values.get('wire.gauge_mm2'):
            values['wire.gauge_mm2'] = mm2_for_awg(values['wire.gauge_awg'], table) or ''
        if values.get('wire.gauge_mm2') and not values.get('wire.gauge_awg'):
            values['wire.gauge_awg'] = awg_for_mm2(values['wire.gauge_mm2'], table) or ''
        if values.get('wire.gauge_awg'):
            values['wire.gauge_awg'] = normalise_awg(values['wire.gauge_awg']) or ''
    allowed = set(_profile(config, ctype).get('required', [])) | set(_profile(config, ctype).get('optional', []))
    allowed.add('part.component_type')
    out = {}
    for role, value in values.items():
        value = str(value or '').strip()
        template = ((config.get('roles') or {}).get(role) or {}).get('template')
        if not value or role not in allowed or not template or (current.get(template.lower()) or '').strip():
            continue
        if lists.get(role):     # once the list has values, only its own spelling
            meta = role_list(config, role) or {}
            value = (join_items(i for i in (suggest(x, lists[role]) for x in split_items(value)) if i)
                     if meta.get('per_item') else (suggest(value, lists[role]) or ''))
            if not value:
                continue
        out[role] = value
    return ctype, bool(own_type), out


def _current_values(parts):
    """{part pk: {template name (lower): value}} in one query."""
    from common.models import Parameter
    from .inventree_native_lookup import _part_content_type
    out = {}
    for model_id, name, data in Parameter.objects.filter(model_type=_part_content_type(),
                                                        model_id__in=[p.pk for p in parts]) \
            .values_list('model_id', 'template__name', 'data'):
        out.setdefault(model_id, {})[str(name).lower()] = data
    return out


def preview(plugin, config, category_pk=None):
    """
    {'items': [{part: {pk, name, description, category}, component_type,
    type_known, values: {role: value}}], 'labels': {role: label},
    'templates': {role: template name}, 'missing_templates': [names]} -
    only parts with something to fill.
    """
    from part.models import Part
    parts = Part.objects.filter(active=True, assembly=False).select_related('category').order_by('name')
    if category_pk:
        parts = parts.filter(category_id=category_pk)
    parts = list(parts)
    current = _current_values(parts)
    lists = lists_for_roles(config)
    items, used_roles = [], set()
    for part in parts:
        ctype, known, values = _proposals(part, config, lists, current.get(part.pk, {}))
        if not values:
            continue
        used_roles.update(values)
        items.append({
            'part': {'pk': part.pk, 'name': part.name, 'description': part.description or '',
                     'category': part.category.pathstring if part.category_id else ''},
            'component_type': ctype, 'type_known': known, 'values': values,
        })
    roles = config.get('roles') or {}
    return {
        'items': items,
        'refused': _refused(items, roles),
        'labels': {r: (roles.get(r) or {}).get('label', r) for r in used_roles},
        'templates': {r: (roles.get(r) or {}).get('template', '') for r in used_roles},
        'missing_templates': sorted({(roles.get(r) or {}).get('template', r) for r in used_roles
                                     if not _template((roles.get(r) or {}).get('template', ''))}),
        'parts_checked': len(parts),
    }


def _allowed(template):
    """The values `template` accepts (its selection list or choices), or None = anything."""
    choices = [c for c in (template.get_choices() or []) if str(c).strip()]
    return choices or None


def _refused(items, roles):
    """
    Proposed values InvenTree would refuse because the template's choices /
    selection list doesn't have them - e.g. Component Type still carrying its
    old "Wire, Multicore, Connector, ..." choices, which refuses Lock and
    Terminal. [{template, allowed, values: {value: [part names]}}].
    """
    out = {}
    for item in items:
        for role, value in item['values'].items():
            name = (roles.get(role) or {}).get('template', '')
            template = _template(name) if name else None
            if template is None:
                continue
            if name not in out:
                out[name] = {'template': template.name, 'allowed': _allowed(template), 'values': {}}
            allowed = out[name]['allowed']
            if allowed is None or str(value).lower() in {str(a).lower() for a in allowed}:
                continue
            out[name]['values'].setdefault(str(value), []).append(item['part']['name'])
    return [r for r in out.values() if r['values']]


def apply(plugin, config, items):
    """
    items: [{part: pk, values: {role: value}}] (what the person ticked).
    Writes each value only if the part still has none. Returns
    {'written': n, 'parts': n, 'errors': [{part, name, role, message}]}.
    """
    from django.core.exceptions import ValidationError
    from part.models import Part
    roles = config.get('roles') or {}
    parts = {p.pk: p for p in Part.objects.filter(pk__in=[int(i['part']) for i in items])}
    current = _current_values(list(parts.values()))
    written, touched, errors = 0, set(), []
    for item in items:
        part = parts.get(int(item['part']))
        if part is None:
            continue
        for role, value in (item.get('values') or {}).items():
            name = (roles.get(role) or {}).get('template')
            template = _template(name) if name else None
            if template is None:
                errors.append({'part': part.pk, 'name': part.name, 'role': role,
                               'message': f"No '{name or role}' template in InvenTree"})
                continue
            if (current.get(part.pk, {}).get(name.lower()) or '').strip():
                continue        # filled since the preview - never overwrite
            try:
                _set_parameter(part, template, str(value))
            except ValidationError as exc:
                errors.append({'part': part.pk, 'name': part.name, 'role': role, 'message': '; '.join(exc.messages)})
                continue
            written += 1
            touched.add(part.pk)
    return {'written': written, 'parts': len(touched), 'errors': errors}
