"""
The Mouser Integration plugin (inventree-mouser), which this plugin uses for
everything Mouser: the Import from Supplier wizard, creating parts from
Mouser (pending parts), and the API keys. OMG's own part data reaches it
through the hooks on OmgHarnessImportPlugin (core.py):
mouser_import_parameters and mouser_part_created.
"""

MOUSER_PLUGIN_SLUG = "mouser"
NOT_ACTIVE = ("The Mouser Integration plugin (inventree-mouser) isn't active - it creates parts from Mouser "
              "now. Activate it in Admin Center > Plugins and set its Search API key and Supplier.")


def mouser_plugin():
    """The active Mouser Integration plugin, or None."""
    from plugin.registry import registry
    return registry.get_plugin(MOUSER_PLUGIN_SLUG)


def require_mouser_plugin():
    plugin = mouser_plugin()
    if plugin is None:
        raise ValueError(NOT_ACTIVE)
    return plugin


def mouser_search_key_set():
    """True when the Mouser plugin is active and has its Search API key."""
    plugin = mouser_plugin()
    return bool(plugin and plugin.get_setting("SEARCH_API_KEY"))
