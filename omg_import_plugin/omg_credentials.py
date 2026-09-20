"""
Resolves which OMG base URL/token to use for a given InvenTree request —
checks that user's personal OmgUserCredential first, falls back to the
plugin-wide OMG_HARNESS_API_URL/OMG_HARNESS_API_TOKEN settings if they
don't have one. This is what makes per-user credentials optional rather
than mandatory: a small deployment can just use the shared settings and
never touch OmgUserCredential at all; a larger one can give specific
users their own OMG identity as needed, without an all-or-nothing switch.
"""

from django.conf import settings

from .models import OmgUserCredential


def get_omg_credentials(user, plugin=None):
    """
    Returns (base_url, token). Either may be None if nothing's
    configured anywhere — callers already handle that case (see
    HarnessSearchProxyView/HarnessImportView's existing "credentials
    aren't configured" responses).

    plugin: optional already-fetched plugin instance (from
    plugin.registry.registry.get_plugin(...)), to avoid re-fetching it
    if the caller already has it. Falls back to raw Django settings if
    not provided — matches the pattern already used elsewhere for the
    OMG_* values before this per-user layer existed.
    """
    personal = OmgUserCredential.objects.filter(user=user).first() if user and user.is_authenticated else None

    if plugin:
        global_url = plugin.get_setting("OMG_HARNESS_API_URL")
        global_token = plugin.get_setting("OMG_HARNESS_API_TOKEN")
    else:
        global_url = getattr(settings, "OMG_HARNESS_API_URL", None)
        global_token = getattr(settings, "OMG_HARNESS_API_TOKEN", None)

    if personal and personal.omg_api_token:
        base_url = personal.omg_base_url_override or global_url
        return base_url, personal.omg_api_token

    return global_url, global_token
