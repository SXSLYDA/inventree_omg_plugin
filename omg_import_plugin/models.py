"""
Requires the plugin to use AppMixin (see core.py) so InvenTree registers
this as a real Django app with its own migrations.

After first install, from the InvenTree server:
    invoke migrate
(InvenTree's own migrate wrapper picks up plugin apps automatically.)
"""

from django.conf import settings
from django.db import models


class ImportBatch(models.Model):
    """One 'import this harness's parts into InvenTree' run, triggered from OMG Harness."""

    root_part_number = models.CharField(
        max_length=128,
        help_text="The harness/assembly part number this batch was imported for.",
    )
    root_part = models.ForeignKey(
        "part.Part", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    total_items = models.PositiveIntegerField(default=0)
    matched_items = models.PositiveIntegerField(default=0)
    flagged_items = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"Import batch for {self.root_part_number} ({self.created_at:%Y-%m-%d %H:%M})"


class UnresolvedImportItem(models.Model):
    """
    A single component from an OMG Harness BOM that could not be confidently
    matched to an existing InvenTree part. Sits in a review queue until a
    human resolves or dismisses it — never auto-retried silently, since a
    human decision here (link vs. create new) shouldn't be second-guessed
    by the next import run.
    """

    class Reason(models.TextChoices):
        NOT_FOUND = "not_found", "No matching InvenTree part found"
        AMBIGUOUS = "ambiguous", "Multiple candidate InvenTree parts found"

    class Resolution(models.TextChoices):
        UNRESOLVED = "unresolved", "Needs review"
        LINKED = "linked", "Linked to an existing part"
        CREATED = "created", "New part created"
        DISMISSED = "dismissed", "Dismissed — no action needed"

    batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="items")
    part_number = models.CharField(max_length=128)
    quantity = models.DecimalField(max_digits=12, decimal_places=4, default=1)
    reason = models.CharField(max_length=20, choices=Reason.choices)
    candidate_pks = models.JSONField(default=list, blank=True)  # populated when reason=ambiguous

    # Set when this flag traces back to a specific OMG object (a
    # Connector or a WiringDiagram wire) rather than being a general
    # InvenTree-side issue (e.g. a stale BOM line). This is what lets
    # push_reconciliation_to_omg() report the flag back to the exact
    # object OMG should show a warning on, instead of OMG having to
    # parse it out of a human-readable note string.
    class OmgObjectType(models.TextChoices):
        CONNECTOR = "connector", "Connector"
        WIRE = "wire", "Wire"
        MULTICORE = "multicore", "Multicore Cable"
        ACCESSORY = "accessory", "Connector Accessory"
        SUB_HARNESS = "sub_harness", "Linked Sub-Harness"
        JUNCTION = "junction", "Connector Junction"

    omg_object_type = models.CharField(max_length=15, choices=OmgObjectType.choices, null=True, blank=True)
    omg_object_id = models.PositiveIntegerField(null=True, blank=True)

    resolution = models.CharField(max_length=20, choices=Resolution.choices, default=Resolution.UNRESOLVED)
    resolved_part = models.ForeignKey(
        "part.Part", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["resolution"])]

    def __str__(self):
        return f"{self.part_number} ({self.reason})"


class OmgUserCredential(models.Model):
    """
    Per-InvenTree-user OMG credentials — each InvenTree user who works
    with harnesses can have their own OMG API token, so their harness
    searches/imports go through as THEIR OMG account (respecting
    whatever company-scoping OMG applies to that user) rather than one
    shared service-account token for the whole InvenTree instance.

    Deliberately a real model, not InvenTree's native per-user plugin
    setting support (SettingsMixin's get_setting/set_setting do accept a
    `user` parameter) — that's a real mechanism, but I couldn't confirm
    it's exposed through any admin UI for assigning arbitrary per-user
    values, only documented as a programmatic parameter. This model is
    explicit and transparent instead: a normal Django admin screen,
    which is admin-only by Django's own permission system already — no
    extra access control code needed for "only an InvenTree admin can
    set this."

    Getting an OMG token still requires the InvenTree user to actually
    log into OMG themselves and generate one there — this is NOT a live
    single-sign-on handshake. It's a one-time manual step: user logs
    into OMG, generates their own API token, gives it to an InvenTree
    admin, who pastes it in here. Same as any token-based integration.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="omg_credential",
    )
    omg_api_token = models.CharField(
        max_length=255, blank=True,
        help_text="This InvenTree user's personal OMG API token — generated by that user in OMG, "
                   "then set here by an admin.",
    )
    omg_base_url_override = models.URLField(
        blank=True,
        help_text="Only needed if this user's OMG account lives on a different instance than the "
                   "plugin-wide OMG_HARNESS_API_URL setting. Leave blank to use that setting.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"OMG credential for {self.user}"
