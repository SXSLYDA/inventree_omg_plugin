"""
InvenTree plugin: OMG Harness Import.

Reconciles OMG Harness component lists and harness BOMs against
InvenTree's real part database, imports/updates harness BOMs pulled from
OMG's design tool, and integrates Mouser into InvenTree's own native
part-creation wizard.

Mixins used:
- AppMixin:            ships this plugin's own DB models (ImportBatch,
                        UnresolvedImportItem) with real migrations.
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
- UserInterfaceMixin:   re-added for a DIFFERENT, smaller panel than the
                        one removed above — "Sync with OMG" on a
                        harness's own part detail page. There's no
                        native InvenTree mechanism for "trigger a custom
                        backend action from a part's page", so this one
                        genuinely needs a panel. Shows on any assembly
                        part; the button re-runs import_or_update_harness_bom
                        for that part's IPN — same action whether it's the
                        first import or a re-sync after OMG design
                        changes. See sync_panel/ for source, static/ for
                        the compiled output (built and verified the same
                        way as the earlier Mouser panel was).

Install: pip install -e this plugin, enable it from InvenTree's Plugins
admin page, run migrations (AppMixin needs its tables created), then set
the OMG_* settings below from InvenTree's Plugin Settings UI — including
picking which Company record represents Mouser as a supplier (added
automatically by SupplierMixin as its own "SUPPLIER" setting).
"""

from plugin import InvenTreePlugin
from plugin.mixins import AppMixin, SettingsMixin, UrlsMixin, UserInterfaceMixin

from .mouser_supplier import MouserSupplierMixin
from .version import OMG_IMPORT_PLUGIN_VERSION


class OmgHarnessImportPlugin(MouserSupplierMixin, AppMixin, UrlsMixin, SettingsMixin, UserInterfaceMixin, InvenTreePlugin):
    NAME = "OmgHarnessImport"
    SLUG = "omg-harness-import"
    TITLE = "OMG Harness Import"
    DESCRIPTION = "Imports/updates harness BOMs from OMG Harness, reconciles components against InvenTree, and integrates Mouser into the native Import Part wizard."
    VERSION = OMG_IMPORT_PLUGIN_VERSION
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
        # --- InvenTree parameter mapping: connector-level ---
        "OMG_CAVITY_LAYOUT_PARAM_NAME": {
            "name": "Cavity Layout Parameter Name",
            "description": "InvenTree part parameter name holding a connector's real, comma-separated "
                            "cavity ID list, e.g. 'A,B,C,D,E,F,H,J,K' — set this per connector on its "
                            "Part detail page. Skipped letters (I/O/Q etc.) just aren't listed. Used for "
                            "an exact blanks-needed calculation instead of a count-based guess.",
            "default": "Cavity Layout",
        },
        "OMG_CONTACT_COUNT_PARAM_NAME": {
            "name": "Contact Count Parameter Name (fallback only)",
            "description": "InvenTree part parameter name holding a connector's total cavity count. "
                            "Only used as an APPROXIMATE blanks-needed fallback for connectors that "
                            "don't have a Cavity Layout parameter set yet — once Cavity Layout exists "
                            "for a connector, this is ignored for it.",
            "default": "Contact Count",
        },
        "OMG_COMPONENT_TYPE_PARAM_NAME": {
            "name": "Component Type Parameter Name",
            "description": "InvenTree part parameter name used to tag a part as 'Blank' or 'Contact' — "
                            "set this on the blank/contact parts themselves, then link them to the "
                            "connector via InvenTree's native Related Parts. This is what tells the "
                            "plugin which related part is which, since Related Parts itself has no "
                            "role/type field.",
            "default": "Component Type",
        },
        # --- InvenTree parameter mapping: conductor-level ---
        "OMG_GAUGE_PARAM_NAME": {
            "name": "Gauge Parameter Name",
            "description": "InvenTree part parameter name holding a conductor's wire gauge/size — "
                            "used as the primary filter when matching a wire with no typed part number.",
            "default": "Gauge",
        },
        "OMG_INSULATION_TYPE_PARAM_NAME": {
            "name": "Insulation Type Parameter Name",
            "description": "InvenTree part parameter name holding a conductor's insulation type — "
                            "used to narrow a gauge-based match, when set.",
            "default": "Insulation Type",
        },
        "OMG_PRIMARY_COLOR_PARAM_NAME": {
            "name": "Primary Color Parameter Name",
            "description": "InvenTree part parameter name holding a conductor's primary wire color.",
            "default": "Primary Color",
        },
        "OMG_SECONDARY_COLOR_PARAM_NAME": {
            "name": "Secondary Color Parameter Name",
            "description": "InvenTree part parameter name holding a conductor's secondary (stripe) "
                            "wire color, for two-color wires. Leave the default if you don't use these.",
            "default": "Secondary Color",
        },
        # --- Per-pin contact selection, when a connector has more than one
        # contact variant (different gauge ranges, different plating) ---
        "OMG_CONTACT_MIN_GAUGE_PARAM_NAME": {
            "name": "Contact Min Gauge Parameter Name",
            "description": "InvenTree part parameter name (on a CONTACT part, not the connector) "
                            "holding the minimum wire gauge that contact accepts. Only consulted when "
                            "a connector has more than one Contact-tagged Related Part — a single "
                            "contact type per connector doesn't need this set at all.",
            "default": "Min Gauge",
        },
        "OMG_CONTACT_MAX_GAUGE_PARAM_NAME": {
            "name": "Contact Max Gauge Parameter Name",
            "description": "InvenTree part parameter name (on a CONTACT part, not the connector) "
                            "holding the maximum wire gauge that contact accepts. Same conditions as "
                            "Contact Min Gauge above — only relevant when a connector has more than "
                            "one Contact-tagged Related Part.",
            "default": "Max Gauge",
        },
        # --- Harness import behavior ---
        "OMG_HARNESS_MARKER_PARAM_NAME": {
            "name": "OMG Harness Marker Parameter Name",
            "description": "InvenTree part parameter name used to mark a part as an OMG-managed "
                            "harness — set to 'true' automatically the first time this part is linked "
                            "or synced with OMG. The 'OMG Harness' panel shows on every assembly part "
                            "either way, but this marker decides whether that panel shows a link-to-OMG "
                            "search or the sync/re-import view for an already-linked harness. You can "
                            "also set this manually on a part you created yourself in InvenTree, before "
                            "its first sync, if you want it treated as already linked.",
            "default": "OMG Harness",
        },
        "AMBIGUOUS_SEARCH_LIMIT": {
            "name": "Ambiguous candidate limit",
            "description": "Max number of candidate parts to record when a component match is ambiguous.",
            "default": 10,
            "validator": int,
        },
    }

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
            "options": {"width": 4, "height": 3},
        }]

    def get_ui_panels(self, request, context: dict, **kwargs):
        """
        Two panels, dispatched by target_model:
          'part'       -> "OMG Harness" — link or sync, depending on state
          'salesorder' -> "Parts List" (see confidence note below)

        "OMG Harness" — shown on every assembly part (not gated by the
        marker parameter anymore). Panel.tsx checks
        OMG_HARNESS_MARKER_PARAM_NAME itself and renders one of two
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

        from .inventree_native_lookup import get_part_parameter_str

        from part.models import Part
        part = Part.objects.filter(pk=context["target_id"]).first()
        if not part:
            return panels

        marker_param = self.get_setting("OMG_HARNESS_MARKER_PARAM_NAME") or "OMG Harness"
        is_omg_harness = (get_part_parameter_str(part, marker_param) or "").strip().lower() == "true"

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
