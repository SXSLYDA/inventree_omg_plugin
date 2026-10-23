import setuptools

from omg_import_plugin.version import OMG_IMPORT_PLUGIN_VERSION

setuptools.setup(
    name="inventree-omg-harness-import",
    version=OMG_IMPORT_PLUGIN_VERSION,
    author="Tyler / OMG Harness",
    description="InvenTree plugin: reconciles OMG Harness component lists against InvenTree parts, "
                "imports/updates harness BOMs, syncs harness parts with OMG on demand, exports a Sales "
                "Order's parts list to XLSX, and adds OMG's part data to parts made by the Mouser Integration plugin.",
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
        # The Mouser Integration plugin (its own repo, inventree-mouser): the
        # Import from Supplier wizard, creating parts from Mouser (pending
        # parts use it) and Mouser orders. A MINIMUM version by name, not a
        # git pin: plugins.txt has its own pinned line
        #   inventree-mouser @ git+https://github.com/SXSLYDA/inventree-mouser.git@vX.Y.Z
        # (its server-update script adds and moves it), so each plugin updates
        # on its own. A pin here would make pip refuse any other Mouser version.
        # Raise this only when this plugin needs something newer from it.
        "inventree-mouser>=0.1.0",
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
