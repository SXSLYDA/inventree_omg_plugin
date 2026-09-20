"""
InvenTree's AppMixin registers this plugin as a real Django app, so a
normal admin.py here gets picked up the standard way — no special plugin
admin API needed.

Nothing to register here for cavity layout / component type / blank /
contact — those all live as standard InvenTree Part Parameters and
Related Parts now (see inventree_native_lookup.py), managed from each
Part's own detail page in InvenTree's normal UI, not from a plugin-
specific admin screen.
"""

from django.contrib import admin

from .models import ImportBatch, OmgUserCredential, UnresolvedImportItem


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = ["root_part_number", "created_at", "total_items", "matched_items", "flagged_items"]
    readonly_fields = ["created_at", "completed_at"]


@admin.register(UnresolvedImportItem)
class UnresolvedImportItemAdmin(admin.ModelAdmin):
    list_display = ["part_number", "reason", "omg_object_type", "omg_object_id", "resolution", "created_at"]
    list_filter = ["reason", "resolution", "omg_object_type"]
    search_fields = ["part_number", "notes"]


@admin.register(OmgUserCredential)
class OmgUserCredentialAdmin(admin.ModelAdmin):
    """
    Only reachable by staff/admin users at all, since that's how Django
    admin already works — no extra permission code needed to satisfy
    "only an InvenTree admin can set this."
    """
    list_display = ["user", "has_token", "omg_base_url_override", "updated_at"]
    search_fields = ["user__username", "user__email"]
    autocomplete_fields = ["user"]

    def has_token(self, obj):
        return bool(obj.omg_api_token)
    has_token.boolean = True
    has_token.short_description = "Token set?"
