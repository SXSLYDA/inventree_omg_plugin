"""
InvenTree plugin: OMG Harness Import.

Reconciles OMG Harness component lists and harness BOMs against
InvenTree's real part database, imports/updates harness BOMs pulled from
OMG's design tool, and integrates Mouser into InvenTree's own native
part-creation wizard.

Mixins used:
- AppMixin:            ships this plugin's own DB models (ImportBatch,
                        UnresolvedImportItem, OmgUserCredential,
                        HarnessImportJob). No migrations folder - the
                        tables are created by syncdb during `invoke
                        update`, which adds new tables but never changes
                        existing ones; see models.py's docstring before
                        changing any model.
                        Blanks-needed and contacts-consumed data live as
                        native InvenTree Part Parameters + Related Parts
                        instead of a bespoke plugin model — see
                        inventree_native_lookup.py for why and how.
- UrlsMixin:            exposes api.py's urlpatterns under /plugin/<slug>/...
- SettingsMixin:        renders the settings below in InvenTree's own
                        Plugin Settings UI — this is where OMG credentials
                        get set up from the InvenTree side, no separate
                        config file needed.
- MouserSupplierMixin (extends SupplierMixin): plugs Mouser into
                        InvenTree's own native "Import Part" wizard
                        (Parts screen -> Add Parts -> Import from
                        Supplier) — search, category selection,
                        parameter matching, and initial stock creation
                        all come from InvenTree's existing multi-step
                        flow, not a custom panel. See mouser_supplier.py.
                        Superseded an earlier UserInterfaceMixin-based
                        Mouser search/create panel that duplicated this
                        same capability less completely — that ONE panel
                        was removed once this native mechanism was
                        confirmed to exist.
- EventMixin:           wire gauge auto-fill - saving "Gauge mm2" or "Gauge AWG"
                        on a part fills the other one if blank, from OMG's AWG / mm²
                        table (gauge_autofill.py).
- ValidationMixin:      refuses a Gauge AWG that isn't a standard size, or a
                        Gauge mm2 that isn't a number (gauge_autofill.py).
- UserInterfaceMixin:   re-added for a DIFFERENT, smaller panel than the
                        one removed above — "Sync with OMG" on a
                        harness's own part detail page. There's no
                        native InvenTree mechanism for "trigger a custom
                        backend action from a part's page", so this one
                        genuinely needs a panel. Shows on any assembly
                        part; the button re-runs import_or_update_harness_bom
                        for that part's IPN — same action whether it's the
                        first import or a re-sync after OMG design
                        changes. The import runs on InvenTree's background
                        worker (harness_import_job.py) and the panel polls
                        for the result, so a big harness can't hit the web
                        server's request timeout. See sync_panel/ for source, static/ for
                        the compiled output (built and verified the same
                        way as the earlier Mouser panel was).

Install: pip install -e this plugin, enable it from InvenTree's Plugins
admin page, run `invoke update` (creates this plugin's tables via syncdb -
there are no migrations, see models.py), then set
the OMG_* settings below from InvenTree's Plugin Settings UI — including
picking which Company record represents Mouser as a supplier (added
automatically by SupplierMixin as its own "SUPPLIER" setting).
"""

from plugin import InvenTreePlugin
from plugin.mixins import AppMixin, EventMixin, SettingsMixin, UrlsMixin, UserInterfaceMixin, ValidationMixin

from .mouser_supplier import MouserSupplierMixin
from .version import OMG_IMPORT_PLUGIN_VERSION


class OmgHarnessImportPlugin(MouserSupplierMixin, AppMixin, UrlsMixin, SettingsMixin, UserInterfaceMixin, EventMixin,
                             ValidationMixin, InvenTreePlugin):
    NAME = "OmgHarnessImport"
    SLUG = "omg-harness-import"
    TITLE = "OMG Harness Import"
    DESCRIPTION = "Imports/updates harness BOMs from OMG Harness, reconciles components against InvenTree, and integrates Mouser into the native Import Part wizard."
    VERSION = OMG_IMPORT_PLUGIN_VERSION
    # InvenTree 1.2+ only (generic common.models parameters; the 1.1 support
    # was removed). InvenTree refuses to load the plugin on anything older.
    MIN_VERSION = "1.2.0"
    AUTHOR = "Tyler / OMG Harness"

    SETTINGS = {
        # --- OMG credentials — set these up here, from InvenTree's side ---
        "OMG_HARNESS_API_URL": {
            "name": "OMG Harness API URL",
            "description": "The web address of your OMG Harness account, e.g. https://omg.example.com",
            "default": "",
        },
        "OMG_HARNESS_API_TOKEN": {
            "name": "OMG InvenTree User Token",
            "description": "Lets this plugin search and pull harness designs from your OMG Harness "
                            "account. Generate this on OMG's own InvenTree Settings page and paste it "
                            "here. This is a different credential from the InvenTree Webhook Token "
                            "below — they're not interchangeable, so don't paste one where the other "
                            "goes.",
            "default": "",
            "protected": True,
        },
        "OMG_INBOUND_WEBHOOK_TOKEN": {
            "name": "InvenTree Webhook Token",
            "description": "Lets OMG know it's really this plugin reporting back after an import — "
                            "which parts matched, and which need a person to look at them. Copy this "
                            "exact value from OMG's own InvenTree Settings page (it's generated "
                            "automatically there, not something you make up yourself). Different "
                            "credential from the OMG InvenTree User Token above — copying the wrong "
                            "one here will make imports work fine but reports back to OMG silently "
                            "fail.",
            "default": "",
            "protected": True,
        },
        # --- Mouser ---
        "OMG_MOUSER_API_KEY": {
            "name": "Mouser API Key",
            "description": "Your Mouser Electronics API key. Used when searching Mouser from the "
                            "Import Part wizard, and when a harness import needs to look up a part "
                            "that OMG has but InvenTree doesn't have yet.",
            "default": "",
            "protected": True,
        },
        "OMG_DOWNLOAD_MOUSER_IMAGES": {
            "name": "Download part images from Mouser",
            "description": "During Import Part, download the part image from Mouser onto the new part.",
            "validator": bool,
            "default": False,
        },
        "AMBIGUOUS_SEARCH_LIMIT": {
            "name": "Ambiguous candidate limit",
            "description": "When a part number matches several InvenTree parts (by name/IPN containing it), "
                           "the most candidates to list on its 'ambiguous' review item for you to pick from (1-100).",
            "default": 10,
            "validator": int,
        },
    }

    # --- EventMixin: wire gauge auto-fill (gauge_autofill.py) ---
    # Needs InvenTree's "Enable event integration" plugin setting and the
    # background worker. Only parameter-save events are taken.

    def wants_process_event(self, event):
        from .gauge_autofill import EVENTS
        return event in EVENTS

    def process_event(self, event, *args, **kwargs):
        from .gauge_autofill import process_gauge_event
        process_gauge_event(self, event, **kwargs)

    # --- ValidationMixin: gauge values (gauge_autofill.validate_gauge_value) ---

    def validate_parameter(self, parameter, data):
        from .gauge_autofill import validate_gauge_value
        return validate_gauge_value(self, parameter, data)

    def _is_connector(self, part):
        """Component Type = Connector (OMG's template name), or it already has a cavity map."""
        from .inventree_native_lookup import get_part_parameter_str
        from .part_setup import _config_cache
        roles = ((_config_cache.get("data") or {}).get("roles") or {})
        type_name = (roles.get("part.component_type") or {}).get("template") or "Component Type"
        if (get_part_parameter_str(part, type_name) or "").strip().lower() == "connector":
            return True
        for role, default in (("connector.cavity_map", "Cavity Map"), ("connector.cavity_groups", "Cavity Groups")):
            name = (roles.get(role) or {}).get("template") or default
            if get_part_parameter_str(part, name):
                return True
        return False

    def _is_accessory(self, part):
        """Component Type is an accessory kind - Lock, Secondary Lock, Boot, Cover, Backshell, Holding Plate, Hardware."""
        from .accessory_setup import is_accessory_part
        from .part_setup import _config_cache
        return is_accessory_part(part, _config_cache.get("data") or {})

    def setup_urls(self):
        from . import api
        return api.urlpatterns

    def get_ui_dashboard_items(self, request, context: dict, **kwargs):
        """
        "Import harness from OMG" — a genuine gap found on review:
        HarnessSearchProxyView and HarnessImportView (search-and-import)
        existed as working backend endpoints with no frontend calling
        them anywhere in this project. Built now, as a dashboard item
        rather than a panel — importing by name/IPN creates or finds
        its own Part (see harness_import.py's auto-create), unrelated
        to whatever record a get_ui_panels target_model would scope to,
        so there's no natural existing-record page this belongs on.
        get_ui_dashboard_items itself is confirmed working, not
        inferred — fetched directly from InvenTree's own real sample
        plugin source (plugin/samples/integration/user_interface_sample.py).
        """
        return [{
            "key": "omg-import-harness",
            "title": "Import harness from OMG",
            "description": "Search OMG's harness part numbers and import them into InvenTree.",
            "icon": "ti:file-import:outline",
            "source": self.plugin_static_file("ImportHarnessPanel.js:RenderOMGImportHarnessDashboardItem"),
            # Height bumped again (3 -> 5 -> 8): even at 5, the success/
            # error Alert (which can include the "Review in InvenTree"
            # button) pushed the results list itself out of the visible
            # area whenever both were showing at once - the widget never
            # scrolls its own outer height, only the results list does
            # (see ImportHarnessPanel.tsx's own maxHeight on that Stack),
            # so the fixed dashboard-grid height has to actually fit
            # credential badges + search row + message + a few results
            # together, not just whichever one the person is looking at
            # in the moment.
            "options": {"width": 4, "height": 8},
        }]

    def get_ui_panels(self, request, context: dict, **kwargs):
        """
        Two panels, dispatched by target_model:
          'part'       -> "OMG Harness" — link or sync, depending on state
          'salesorder' -> "Parts List" (see confidence note below)

        "OMG Harness" — shown on every assembly part (not gated by the
        marker parameter anymore). Panel.tsx checks
        HARNESS_MARKER_PARAM itself and renders one of two
        flows: not yet linked -> search OMG and link this exact part;
        already linked -> sync/re-import its BOM. One panel with two
        states, so linking and then syncing reads as one continuous
        flow on the part's own page rather than two separate things to
        discover (the dashboard-only import flow that predated this).

        "Parts List" (Sales Order) — shown for every Sales Order
        unconditionally (no equivalent marker check needed — a sales
        order can contain both harness and non-harness line items, so
        gating this the same way wouldn't make sense). `target_model ==
        'salesorder'` is confirmed, not inferred: a real, current
        official InvenTree sample plugin explicitly uses `target_model
        == 'purchaseorder'` for a working panel, and InvenTree's own
        Report Context docs independently list `salesorder` as a
        canonical model-type keyword. SalesOrderLineItem's exact field
        names (`part`/`quantity`) are confirmed too, via a real
        third-party plugin's production code — see
        so_export_panel_source/README.md for the full source trail.
        """
        panels = []
        target_model = context.get("target_model")

        if target_model == "salesorder" and context.get("target_id"):
            panels.append({
                "key": "omg-so-parts-list",
                "title": "Parts List",
                "icon": "ti:file-spreadsheet:outline",
                "source": self.plugin_static_file("SOExportPanel.js:RenderOMGSalesOrderPartsListPanel"),
            })
            return panels

        if target_model != "part" or not context.get("target_id"):
            return panels

        from .inventree_native_lookup import HARNESS_MARKER_PARAM, get_part_parameter_str

        from part.models import Part
        part = Part.objects.filter(pk=context["target_id"]).first()
        if not part:
            return panels

        # "OMG Part Setup" on every non-assembly part (wires, connectors,
        # contacts, seals, blanks): pick a Component Type, fill in what it
        # needs. "OMG Cavities" on connectors - Component Type is read from
        # the part with OMG's template name (cached; falls back to showing
        # it whenever the part has a Cavity Map / Cavity Groups parameter).
        if not part.assembly:
            panels.append({
                "key": "omg-part-setup",
                "title": "OMG Part Setup",
                "icon": "ti:list-check:outline",
                "source": self.plugin_static_file("PartSetupPanel.js:RenderOMGPartSetupPanel"),
            })
            is_connector = self._is_connector(part)
            if is_connector:
                panels.append({
                    "key": "omg-cavities",
                    "title": "OMG Cavities",
                    "icon": "ti:grid-dots:outline",
                    "source": self.plugin_static_file("PartSetupPanel.js:RenderOMGCavityPanel"),
                })
            # "OMG Accessories": on a connector, which locks / boots / covers
            # fit and which are required; on a lock, boot... which
            # connectors it fits (accessory_setup.py).
            if is_connector or self._is_accessory(part):
                panels.append({
                    "key": "omg-accessories",
                    "title": "OMG Accessories",
                    "icon": "ti:lock:outline",
                    "source": self.plugin_static_file("PartSetupPanel.js:RenderOMGAccessoryPanel"),
                })

        is_omg_harness = (get_part_parameter_str(part, HARNESS_MARKER_PARAM) or "").strip().lower() == "true"

        # Shown for any assembly now, not just ones already linked —
        # Panel.tsx itself checks is_omg_harness (via the same part
        # parameter, queried through context.api) and renders either
        # the "link this part to an OMG harness" flow or the existing
        # "sync/re-import BOM" flow depending on what it finds. This is
        # deliberately one panel with two states rather than two
        # separate panels, so linking and then syncing feels like one
        # continuous flow on the same part page instead of two things
        # to separately discover.
        if part.assembly:
            panels.append({
                "key": "omg-harness-sync",
                "title": "OMG Harness" if is_omg_harness else "Link to OMG",
                "icon": "ti:refresh:outline" if is_omg_harness else "ti:link:outline",
                "source": self.plugin_static_file("Panel.js:RenderOMGHarnessSyncPanel"),
            })
        return panels
