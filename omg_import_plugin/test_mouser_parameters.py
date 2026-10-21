"""
Tests for OMG parameters from a Mouser result (mouser_parameters.py) - no
InvenTree needed. From the plugin repo root:

    python -m unittest omg_import_plugin.test_mouser_parameters
"""
import unittest

from .mouser_parameters import guess_component_type, omg_parameters, omg_values

CONFIG = {
    'roles': {r: {'template': t} for r, t in {
        'part.component_type': 'Component Type', 'wire.gauge_awg': 'Gauge AWG', 'wire.gauge_mm2': 'Gauge mm2',
        'wire.insulation': 'Insulation Type', 'wire.primary_color': 'Primary Color',
        'wire.secondary_color': 'Secondary Color', 'wire.max_temperature': 'Max Temperature',
        'wire.insulation_od': 'Insulation OD', 'contact.min_gauge': 'Min Gauge', 'contact.max_gauge': 'Max Gauge',
        'connector.contact_count': 'Contact Count', 'connector.series': 'Connector Series',
        'connector.side': 'Side'}.items()},
    'profiles': {
        'Wire': {'required': ['wire.gauge_mm2', 'wire.gauge_awg', 'wire.insulation', 'wire.primary_color'],
                 'optional': ['wire.secondary_color', 'wire.max_temperature', 'wire.insulation_od']},
        'Contact': {'required': ['contact.min_gauge', 'contact.max_gauge'], 'optional': []},
        'Connector': {'required': ['connector.contact_count', 'connector.series', 'connector.side'], 'optional': []},
        'Lock': {'required': ['connector.series', 'connector.side'], 'optional': []},
    },
    'colour_roles': ['wire.primary_color', 'wire.secondary_color'],
    'colours': [['Red', 'RD, R', '#f00'], ['White', 'WH, W', '#fff']],
}

WIRE = {'description': 'Hook-up Wire TXL 18AWG RED', 'primary_color': 'RD', 'outer_diameter': 2.1,
        'attributes': {'Wire Gauge': '18 AWG', 'Insulation Material': 'TXL', 'Color': 'Red',
                       'Operating Temperature Range': '- 40 C to + 125 C'}}
CONTACT = {'description': 'Automotive Connectors SOCKET CONTACT 16-20 AWG',
           'attributes': {'Wire Gauge': '16 AWG to 20 AWG'}}
CONNECTOR = {'description': 'Automotive Connectors DT 4P Receptacle', 'contact_count': 4,
             'attributes': {'Series': 'DT Series', 'Number of Positions': '4 Position'}}
LOCK = {'description': 'Automotive Connectors Wedgelock 4P Plug', 'attributes': {'Series': 'DT'}}


class MouserParameterTests(unittest.TestCase):
    def test_types(self):
        self.assertEqual([guess_component_type(d) for d in (WIRE, CONTACT, CONNECTOR, LOCK)],
                         ['Wire', 'Contact', 'Connector', 'Lock'])
        self.assertEqual(guess_component_type({'description': 'TPA secondary lock 12P'}), 'Secondary Lock')
        self.assertEqual(guess_component_type({'description': 'Fuse 10A'}), '')

    def test_wire(self):
        self.assertEqual(dict(omg_parameters(WIRE, CONFIG)), {
            'Component Type': 'Wire', 'Gauge AWG': '18', 'Insulation Type': 'TXL', 'Primary Color': 'Red',
            'Max Temperature': '125', 'Insulation OD': '2.1'})

    def test_contact_gauge_range_thin_to_thick(self):
        values = omg_values(CONTACT, 'Contact')
        self.assertEqual((values['contact.min_gauge'], values['contact.max_gauge']), ('20 AWG', '16 AWG'))

    def test_connector(self):
        self.assertEqual(dict(omg_parameters(CONNECTOR, CONFIG)), {
            'Component Type': 'Connector', 'Contact Count': '4', 'Connector Series': 'DT', 'Side': 'Receptacle'})

    def test_kind_beats_guess_and_profile_limits_roles(self):
        # a pending "connector" stays a Connector; no wire roles leak in
        params = dict(omg_parameters({**WIRE, 'contact_count': 2}, CONFIG, 'Connector'))
        self.assertEqual(params['Component Type'], 'Connector')
        self.assertNotIn('Gauge AWG', params)

    def test_unknown_type_only_sets_nothing_risky(self):
        self.assertEqual(omg_parameters({'description': 'Fuse 10A', 'attributes': {}}, CONFIG), [])

    def test_no_config(self):
        self.assertEqual(omg_parameters(WIRE, None), [])


class SelectionListTests(unittest.TestCase):
    """Values matched to OMG's selection lists (lists=...)."""
    CONFIG = {**CONFIG, 'selection_lists': {'connector.series': {'list': 'Connector Series', 'per_item': True},
                                            'wire.insulation': {'list': 'Insulation Type', 'per_item': False}}}

    def test_snapped_to_list_spelling(self):
        lists = {'connector.series': ['DT', 'DTM'], 'wire.insulation': ['TXL', 'GXL']}
        connector = {**CONNECTOR, 'attributes': {'Series': 'Deutsch DT Series'}}
        self.assertEqual(dict(omg_parameters(connector, self.CONFIG, lists=lists))['Connector Series'], 'DT')
        wire = {**WIRE, 'attributes': {**WIRE['attributes'], 'Insulation Material': 'txl'}}
        self.assertEqual(dict(omg_parameters(wire, self.CONFIG, lists=lists))['Insulation Type'], 'TXL')

    def test_not_in_list_left_blank(self):
        lists = {'connector.series': ['DTM'], 'wire.insulation': ['GXL']}
        params = dict(omg_parameters(CONNECTOR, self.CONFIG, lists=lists))
        self.assertNotIn('Connector Series', params)
        self.assertNotIn('Insulation Type', dict(omg_parameters(WIRE, self.CONFIG, lists=lists)))

    def test_no_lists_unchanged(self):
        self.assertEqual(dict(omg_parameters(CONNECTOR, self.CONFIG))['Connector Series'], 'DT')


class SnapTests(unittest.TestCase):
    """selection_lists.snap - what the Part Setup panel does to a typed value."""

    def test_exact_only_fixes_case_and_dashes(self):
        from .selection_lists import snap
        self.assertEqual(snap('dt-16', ['DT-16'], exact=True), ('DT-16', []))
        self.assertEqual(snap('Deutsch DT', ['DT'], exact=True), ('Deutsch DT', ['Deutsch DT']))
        self.assertEqual(snap('dt, dtm', ['DT', 'DTM'], per_item=True, exact=True), ('DT, DTM', []))
        self.assertEqual(snap('anything', [], exact=True), ('anything', []))
