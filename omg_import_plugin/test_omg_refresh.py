"""
Tests for the push to OMG when parts change here (omg_refresh.py) - in a real InvenTree. From an InvenTree source
checkout with this plugin installed (pip install -e) and plugins on:

    cd src/backend/InvenTree
    INVENTREE_PLUGINS_ENABLED=True INVENTREE_PLUGIN_TESTING=True python manage.py test omg_import_plugin.test_omg_refresh
"""
from unittest import mock

from company.models import Company, ManufacturerPart, SupplierPart
from InvenTree.unit_test import InvenTreeTestCase
from part.models import Part, PartRelated

from . import inventree_native_lookup as native
from .models import OmgSyncPending
from .omg_refresh import notify, part_payload


class PartPushTest(InvenTreeTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.housing = Part.objects.create(name="DT06-2S", IPN="DT06-2S", description="Plug, DT, 2 way",
                                          component=True)
        cls.contact = Part.objects.create(name="0462-201-16141", description="Contact", component=True)
        cls.seal = Part.objects.create(name="Seal", description="Seal", component=True, active=False)
        PartRelated.objects.create(part_1=cls.housing, part_2=cls.contact)
        PartRelated.objects.create(part_1=cls.seal, part_2=cls.housing)
        native.set_part_parameter(cls.housing, "Component Type", "Connector")
        native.set_part_parameter(cls.housing, "Connector Side", "Receptacle")
        maker = Company.objects.create(name="Deutsch", is_manufacturer=True)
        shop = Company.objects.create(name="Mouser", is_supplier=True)
        ManufacturerPart.objects.create(part=cls.housing, manufacturer=maker, MPN="DT06-2S-MFR")
        SupplierPart.objects.create(part=cls.housing, supplier=shop, SKU="654-DT06-2S")

    def test_payload_is_the_part_as_omg_stores_it(self):
        by_pk = {p["pk"]: p for p in part_payload([self.housing.pk, self.seal.pk, 99999])}
        self.assertEqual(set(by_pk), {self.housing.pk, self.seal.pk})          # a part gone is left out
        housing = by_pk[self.housing.pk]
        self.assertEqual(housing["part"], {"name": "DT06-2S", "IPN": "DT06-2S", "description": "Plug, DT, 2 way",
                                           "active": True, "image": None, "thumbnail": None})
        self.assertEqual(housing["parameters"], {"Component Type": "Connector", "Connector Side": "Receptacle"})
        self.assertEqual(housing["related"], sorted([self.contact.pk, self.seal.pk]))   # both directions
        self.assertEqual((housing["mpns"], housing["skus"]), (["DT06-2S-MFR"], ["654-DT06-2S"]))
        self.assertEqual((by_pk[self.seal.pk]["part"]["active"], by_pk[self.seal.pk]["related"]),
                         (False, [self.housing.pk]))

    def test_notify_sends_the_parts(self):
        with mock.patch("omg_import_plugin.part_setup.omg_call", return_value=({}, None)) as call:
            self.assertEqual(notify([self.housing.pk]), "")
        method, path, body = call.call_args.args[:3]
        self.assertEqual((method, path, body["inventree_pks"]), ("post", "/api/parts/refresh/", [self.housing.pk]))
        self.assertEqual(body["parts"][0]["parameters"]["Connector Side"], "Receptacle")

    def test_omg_down_flags_the_part(self):
        with mock.patch("omg_import_plugin.part_setup.omg_call", return_value=(None, "timed out")):
            self.assertEqual(notify([self.contact.pk]), "timed out")
        self.assertTrue(OmgSyncPending.objects.filter(part=self.contact).exists())
        with mock.patch("omg_import_plugin.part_setup.omg_call", return_value=({}, None)):
            notify([self.contact.pk])
        self.assertFalse(OmgSyncPending.objects.filter(part=self.contact).exists())


class GaugeConfigTest(InvenTreeTestCase):
    """The gauge auto-fill reads OMG's part profiles (OMG's old /api/parts/wire-parameters/ is gone - a 404 each
    time a gauge was saved)."""

    def setUp(self):
        super().setUp()
        from . import gauge_autofill
        gauge_autofill._cache.update(at=0.0, names=None, table=None)

    def test_names_and_table_from_part_profiles(self):
        from .gauge_autofill import _omg_config
        profiles = {"roles": {"wire.gauge_mm2": {"template": "Wire mm²"}, "wire.gauge_awg": {"template": "AWG"}},
                    "awg_to_mm2": {"18": "0.8"}}
        with mock.patch("omg_import_plugin.part_setup.omg_part_config", return_value=(profiles, None)):
            names, table = _omg_config(None)
        self.assertEqual((names, table), ({"wire.gauge_mm2": "Wire mm²", "wire.gauge_awg": "AWG"}, {"18": "0.8"}))

    def test_defaults_when_omg_is_down(self):
        from .gauge_autofill import DEFAULT_AWG_TO_MM2, DEFAULT_NAMES, _omg_config
        with mock.patch("omg_import_plugin.part_setup.omg_part_config", return_value=(None, "down")):
            self.assertEqual(_omg_config(None), (DEFAULT_NAMES, DEFAULT_AWG_TO_MM2))
