"""
Tests for the OMG build check (build_check.py) - in a real InvenTree. From an InvenTree source checkout with this
plugin installed (pip install -e) and plugins on:

    cd src/backend/InvenTree
    INVENTREE_PLUGINS_ENABLED=True INVENTREE_PLUGIN_TESTING=True python manage.py test omg_import_plugin.test_build_check

The sales-order test needs inventree-sales-order-builds installed too (it's skipped otherwise).
"""
from unittest import mock

from build.models import Build
from company.models import Company
from django.core.exceptions import ValidationError
from InvenTree.unit_test import InvenTreeTestCase
from order.models import SalesOrder, SalesOrderLineItem
from order.status_codes import SalesOrderStatus
from part.models import BomItem, Part
from plugin import registry

from . import inventree_native_lookup as native
from .build_check import CHECK_KEY, SYNC_KEY
from .harness_import import record_sync
from .models import ImportBatch, UnresolvedImportItem

SLUG = "omg-harness-import"
FINGERPRINT = "aaaa1111bbbb2222"


def omg_says(fingerprint=FINGERPRINT, revision="4.0", error=None):
    """Patch OMG's design-fingerprint answer (or make it unreachable)."""
    answer = (None, error) if error else ({"harness_id": 7, "part_number": "OMG-H1", "fingerprint": fingerprint,
                                            "revision": revision}, None)
    return mock.patch("omg_import_plugin.part_setup.omg_call", return_value=answer)


class BuildCheckTest(InvenTreeTestCase):
    """New build orders of OMG harnesses against the last OMG sync."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        if SLUG not in registry.plugins_full:
            registry.reload_plugins(full_reload=True, collect=True)
        registry.set_plugin_state(SLUG, True)
        cls.wire = Part.objects.create(name="Wire 1.5", description="Wire", component=True)
        cls.contact = Part.objects.create(name="DT contact", description="Contact", component=True)
        cls.seal = Part.objects.create(name="Seal", description="Seal", component=True)
        cls.harness = Part.objects.create(name="OMG-H1", IPN="OMG-H1", description="Harness", assembly=True,
                                          salable=True)
        cls.other = Part.objects.create(name="Not OMG", description="Other assembly", assembly=True)
        BomItem.objects.create(part=cls.harness, sub_part=cls.wire, quantity=3)
        BomItem.objects.create(part=cls.harness, sub_part=cls.contact, quantity=4)
        BomItem.objects.create(part=cls.other, sub_part=cls.wire, quantity=1)
        native.set_part_parameter(cls.harness, native.HARNESS_MARKER_PARAM, "true")

    def setUp(self):
        super().setUp()
        self.plugin = registry.get_plugin(SLUG)
        self.assertIsNotNone(self.plugin)
        self.mode("refuse")
        self.harness.refresh_from_db()

    # ---------------------------------------------------------------- helpers

    def mode(self, value):
        self.plugin.set_setting("OMG_BUILD_CHECK", value)

    def sync(self, open_items=0, fingerprint=FINGERPRINT):
        """Record a sync of the harness as it is now (what harness_import does at the end of a sync)."""
        batch = ImportBatch.objects.create(root_part_number="OMG-H1", root_part=self.harness)
        for n in range(open_items):
            UnresolvedImportItem.objects.create(batch=batch, part_number=f"X{n}", quantity=1,
                                                reason=UnresolvedImportItem.Reason.NOT_FOUND)
        record_sync(self.harness, batch, {"harness_id": 7, "harness_part_number": "OMG-H1",
                                          "design_fingerprint": fingerprint, "design_revision": "4.0"},
                    user=self.user)
        self.harness.refresh_from_db()
        return batch

    def set_qty(self, sub_part, quantity):
        """Change a BOM line the way InvenTree does (set_quantity - its raw_amount decides the quantity)."""
        line = BomItem.objects.filter(part=self.harness, sub_part=sub_part).first()
        line.set_quantity(str(quantity))
        line.save()

    def build(self, part=None):
        n = Build.objects.count() + 1
        return Build.objects.create(part=part or self.harness, quantity=1, reference=f"BO-{n:04d}",
                                    title="Test build")

    def refused(self, message):
        with self.assertRaises(ValidationError) as ctx:
            self.build()
        self.assertIn(message, str(ctx.exception))

    def warned(self, message):
        build = self.build()
        build.refresh_from_db()
        self.assertTrue(any(message in p for p in build.metadata[CHECK_KEY]["problems"]), build.metadata)

    # ------------------------------------------------------------------ tests

    def test_sync_records_snapshot_and_validates_bom(self):
        self.sync()
        snap = self.harness.get_metadata(SYNC_KEY)
        self.assertEqual(snap["bom"], {str(self.wire.pk): "3", str(self.contact.pk): "4"})
        self.assertEqual((snap["fingerprint"], snap["open_items"]), (FINGERPRINT, 0))
        self.assertTrue(self.harness.bom_validated)
        self.assertTrue(self.harness.is_bom_valid())
        self.sync(open_items=2)
        self.assertFalse(self.harness.bom_validated)                    # open review items: not validated

    def test_clean_sync_allowed(self):
        self.sync()
        for mode in ("refuse", "warn"):
            self.mode(mode)
            with omg_says():
                build = self.build()
            build.refresh_from_db()
            self.assertNotIn(CHECK_KEY, build.metadata or {})

    def test_never_synced(self):
        self.refused("never been synced from OMG")
        self.mode("warn")
        self.warned("never been synced")

    def test_quantity_edited_after_sync(self):
        self.sync()
        self.set_qty(self.wire, 5)
        with omg_says():
            self.refused("changed in InvenTree after the last OMG sync (1 line differs)")
            self.mode("warn")
            self.warned("changed in InvenTree")

    def test_line_added_after_sync(self):
        self.sync()
        BomItem.objects.create(part=self.harness, sub_part=self.seal, quantity=2)
        with omg_says():
            self.refused("changed in InvenTree")
            self.mode("warn")
            self.warned("1 line differs")

    def test_duplicate_lines_added_together(self):
        self.sync()
        self.set_qty(self.wire, 1)
        BomItem.objects.create(part=self.harness, sub_part=self.wire, quantity=2, reference="extra")
        with omg_says():
            self.build()                                                 # 1 + 2 = the synced 3

    def test_open_review_items(self):
        self.sync(open_items=2)
        with omg_says():
            self.refused("left 2 review items unresolved")
            self.mode("warn")
            self.warned("review items unresolved")

    def test_review_items_resolved_later(self):
        batch = self.sync(open_items=1)
        batch.items.update(resolution=UnresolvedImportItem.Resolution.DISMISSED)
        with omg_says():
            self.build()

    def test_changed_in_omg(self):
        self.sync()
        with omg_says(fingerprint="cccc3333dddd4444", revision="5.0"):
            self.refused("changed in OMG since the last sync (rev 4.0 synced, OMG now rev 5.0)")
            self.mode("warn")
            self.warned("changed in OMG")

    def test_omg_unreachable(self):
        self.sync()
        for mode in ("refuse", "warn"):
            self.mode(mode)
            with omg_says(error="Couldn't reach OMG (timed out)"):
                build = self.build()                                     # allowed - OMG down never stops work
            build.refresh_from_db()
            check = build.metadata[CHECK_KEY]
            self.assertEqual(check["problems"], [])
            self.assertIn("OMG wasn't checked", check["warnings"][0])

    def test_omg_unreachable_still_checks_inventree(self):
        self.sync()
        BomItem.objects.filter(part=self.harness, sub_part=self.wire).delete()
        with omg_says(error="down"):
            self.refused("changed in InvenTree")

    def test_not_an_omg_harness(self):
        with omg_says() as call:
            build = self.build(self.other)
        call.assert_not_called()
        build.refresh_from_db()
        self.assertNotIn(CHECK_KEY, build.metadata or {})

    def test_off(self):
        self.mode("off")
        with omg_says() as call:
            self.build()                                                 # never synced, but not checked
        call.assert_not_called()

    def test_existing_build_saved(self):
        self.sync()
        with omg_says():
            build = self.build()
        self.set_qty(self.wire, 9)
        with omg_says(fingerprint="changed") as call:
            build.title = "Renamed"
            build.save()                                                 # has a pk: untouched
        call.assert_not_called()

    def test_omg_asked_once_per_build(self):
        self.sync()
        with omg_says() as call:
            build = Build(part=self.harness, quantity=1, reference="BO-0099", title="Twice")
            build.full_clean()
            build.save()
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args.kwargs["timeout"], 5)
        self.assertEqual(call.call_args.args[1], "/api/harness/OMG-H1/design-fingerprint/")

    def test_resync_updates_an_existing_line(self):
        """The sync corrects a line's quantity in place - on InvenTree 1.7 that needs set_quantity (raw_amount)."""
        from .harness_import import _upsert_bom_line
        batch = ImportBatch.objects.create(root_part_number="OMG-H1", root_part=self.harness)
        _upsert_bom_line(batch, self.harness, self.wire, 6.5)
        line = BomItem.objects.get(part=self.harness, sub_part=self.wire)
        self.assertEqual((line.quantity, batch.items.count()), (6.5, 0))
        line.save()                                                      # and it stays on the next save
        line.refresh_from_db()
        self.assertEqual(line.quantity, 6.5)

    def test_resolving_adds_to_an_existing_line(self):
        from .resolver import resolve_item
        batch = ImportBatch.objects.create(root_part_number="OMG-H1", root_part=self.harness)
        item = UnresolvedImportItem.objects.create(batch=batch, part_number="DT contact", quantity=2,
                                                   reason=UnresolvedImportItem.Reason.NOT_FOUND)
        resolve_item(item, "link", part_pk=self.contact.pk)
        self.assertEqual(BomItem.objects.get(part=self.harness, sub_part=self.contact).quantity, 6)

    def test_sales_order_line_skipped(self):
        """inventree-sales-order-builds: the drifted harness's line gets no build, with OMG's message."""
        so_slug = "salesorderbuilds"
        if so_slug not in registry.plugins_full:
            self.skipTest("inventree-sales-order-builds isn't installed")
        registry.set_plugin_state(so_slug, True)
        so_plugin = registry.get_plugin(so_slug)
        for key, spec in so_plugin.SETTINGS.items():
            so_plugin.set_setting(key, spec["default"])
        self.sync()
        self.set_qty(self.wire, 7)
        self.harness.validate_bom(self.user)               # InvenTree's own check passes - only OMG's catches it
        customer = Company.objects.create(name="Mine Co", is_customer=True)
        order = SalesOrder.objects.create(customer=customer, reference="SO-0001")
        SalesOrderLineItem.objects.create(order=order, part=self.harness, quantity=2)
        order.status = SalesOrderStatus.IN_PROGRESS.value
        order.save()
        with omg_says(), self.captureOnCommitCallbacks(execute=True):
            result = so_plugin.process_order(order.pk)
        self.assertEqual(result.created, [])
        self.assertEqual(len(result.skipped), 1)
        self.assertIn("OMG: the BOM was changed in InvenTree", result.skipped[0][1])
        self.assertFalse(Build.objects.filter(sales_order=order).exists())

    def test_sync_makes_a_plain_part_an_assembly(self):
        """A loop / splice plug OMG made in InvenTree as a plain part (before plugs were assemblies) takes its BOM:
        the sync makes the part it syncs into an assembly."""
        from .harness_import import import_or_update_harness_bom
        plug = Part.objects.create(name="LP-100", description="Loop plug", component=True, assembly=False)
        with mock.patch("omg_import_plugin.harness_import._sync_harness_identity"):
            batch, _ = import_or_update_harness_bom("LP-100", {"harness_part_number": "LP-100", "harness_id": 9,
                                                                "design_fingerprint": FINGERPRINT})
        plug.refresh_from_db()
        self.assertTrue(plug.assembly)
        self.assertEqual(batch.root_part, plug)

