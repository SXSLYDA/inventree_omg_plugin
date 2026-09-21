import setuptools

from omg_import_plugin.version import OMG_IMPORT_PLUGIN_VERSION

setuptools.setup(
    name="inventree-omg-harness-import",
    version=OMG_IMPORT_PLUGIN_VERSION,
    author="Tyler / OMG Harness",
    description="InvenTree plugin: reconciles OMG Harness component lists against InvenTree parts, "
                "imports/updates harness BOMs, syncs harness parts with OMG on demand, exports a Sales "
                "Order's parts list to XLSX, and integrates Mouser into InvenTree's native Import Part wizard.",
    packages=setuptools.find_packages(exclude=[
        "sync_panel_source", "sync_panel_source.*",
        "so_export_panel_source", "so_export_panel_source.*",
        "import_harness_panel_source", "import_harness_panel_source.*",
    ]),
    include_package_data=True,
    package_data={
        # Ships both compiled panels ("Sync with OMG" and "Parts List")
        # with the Python package — both source directories
        # (sync_panel_source/, so_export_panel_source/) are NOT
        # included, those are the editable TSX source for rebuilding
        # them, not something InvenTree needs at runtime.
        "omg_import_plugin": ["static/*", "static/.vite/*"],
    },
    install_requires=[
        "requests",
        "openpyxl",  # sales_order_export.py's XLSX generation
        # mouser_supplier.py and resolve_pending.py both import this
        # directly — it's a real dependency, not optional. This is our
        # own package too (see mouser_shared, its own separate repo),
        # never published to PyPI, so it needs a direct URL reference
        # (PEP 508) rather than a plain name — pip resolves this from
        # git during install, no separate manual step needed.
        "mouser-lookup @ git+https://github.com/SXSLYDA/mouser_shared.git",
    ],
    entry_points={
        # This is the entry point group InvenTree scans for installed plugins.
        # Confirm the group name against the InvenTree version you're running —
        # it has been 'inventree_plugins' across recent InvenTree releases.
        "inventree_plugins": [
            "OmgHarnessImportPlugin = omg_import_plugin.core:OmgHarnessImportPlugin",
        ],
    },
)
