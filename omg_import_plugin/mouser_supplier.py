"""
Mouser search inside InvenTree's own native "Import Part" wizard —
Parts screen -> Add Parts -> Import from Supplier. This is what actually
answers "extend the add-part form with Mouser search": InvenTree already
has a purpose-built multi-step wizard for exactly this (search supplier
-> pick category -> match parameters -> create initial stock), and
SupplierMixin is the plugin hook that plugs a supplier into it. No
custom panel or sidebar needed — this is the real add-part flow, not a
bolted-on UI.

The supplier "company" record (a Company with is_supplier=True
representing Mouser in your InvenTree data) is configured via this
plugin's own settings once enabled — SupplierMixin adds that setting
automatically, no extra code needed here.

Uses the same mouser_lookup package as everything else in this project —
no separate Mouser API client, no duplicated parsing logic.
"""

import io
import logging

from django.db.models import Q

from company.models import Company, ManufacturerPart, SupplierPart, SupplierPriceBreak
from part.models import Part
from plugin.base.supplier import helpers as supplier
from plugin.base.supplier.mixins import SupplierMixin

from mouser_lookup import search_by_mpn

logger = logging.getLogger(__name__)

# InvenTree's own SupplierMixin.download_image() calls
# download_image_from_url() with its defaults: NO User-Agent (so the
# request goes out as "python-requests/x"), a 2.5 s timeout and a 1 MB
# limit - and InvenTree has no setting to change them. Mouser's image
# server sits behind bot protection that commonly refuses non-browser
# clients, and a 2.5 s timeout is tight from a small droplet. These are
# what this plugin uses instead (download_image() override below).
IMAGE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
IMAGE_TIMEOUT_SECONDS = 10
IMAGE_MAX_BYTES = 5 * 1024 * 1024


def _usable_image_url(value):
    """
    The image URL, or '' if there isn't a real one. mouser_lookup's
    _clean_str(None) returns the literal string "None" for parts with no
    image, which used to be treated as a URL and fail every time.
    """
    url = (value or "").strip()
    if url.lower() in ("", "none", "null"):
        return ""
    if url.startswith("//"):
        url = "https:" + url
    return url if url.lower().startswith(("http://", "https://")) else ""


def _log_image_error(part, url):
    """
    Record a failed image download in InvenTree's Settings -> System ->
    Error Logs (with the reason), not just the server log - logger.warning
    alone never showed up there, so failures were invisible.
    Call from inside an except block.
    """
    logger.warning("Failed to download/save Mouser image for part %s from %s", part.pk, url, exc_info=True)
    try:
        from InvenTree.exceptions import log_error
        log_error(path=f"omg-harness-import: Mouser image for part {part.pk} ({part.name}) from {url}",
                  plugin="omg-harness-import")
    except Exception:  # older InvenTree without log_error(plugin=...) - server log above still has it
        pass


class MouserSupplierMixin(SupplierMixin):
    """
    Mix into the plugin class alongside its other mixins (see core.py).
    Method names/signatures follow InvenTree's own documented
    SupplierMixin contract exactly — see
    https://docs.inventree.org/en/stable/plugins/mixins/supplier/
    """

    def download_image(self, img_url):
        """
        Same as InvenTree's SupplierMixin.download_image() - still uses
        InvenTree's download_image_from_url(), so its SSRF protection,
        redirect checks and image validation all still apply - but with a
        browser User-Agent, a longer timeout and a larger size limit (see
        IMAGE_* above for why). Returns (ContentFile, format).
        """
        from django.core.files.base import ContentFile
        from InvenTree.helpers_model import download_image_from_url

        img = download_image_from_url(
            img_url,
            timeout=IMAGE_TIMEOUT_SECONDS,
            user_agent=IMAGE_USER_AGENT,
            max_size=IMAGE_MAX_BYTES,
        )
        fmt = img.format or "PNG"
        buffer = io.BytesIO()
        img.save(buffer, format=fmt)
        return ContentFile(buffer.getvalue()), fmt

    def get_suppliers(self):
        return [supplier.Supplier(slug="mouser", name="Mouser Electronics")]

    def get_search_results(self, supplier_slug, term):
        # get_setting(), not django.conf.settings - this is InvenTree's
        # own PLUGIN settings API (same one line 125 below already uses
        # correctly for OMG_DOWNLOAD_MOUSER_IMAGES). OMG_MOUSER_API_KEY
        # is declared as a plugin setting in core.py, not a Django
        # settings.py value - django.conf.settings.OMG_MOUSER_API_KEY
        # was never going to exist there, so this always silently
        # returned None -> "if not api_key: return []" -> empty search
        # results with no error, which is exactly the reported "Mouser
        # import isn't working, not sure why" symptom. Confirmed
        # directly against api.py's own is_set() helper, which uses
        # plugin.get_setting() for this identical setting.
        api_key = self.get_setting("OMG_MOUSER_API_KEY")
        if not api_key:
            return []

        try:
            results = search_by_mpn(term, api_key)
        except Exception:
            return []

        search_results = []
        for r in results:
            existing = ManufacturerPart.objects.filter(MPN__iexact=r["mpn"]).first()
            search_results.append(supplier.SearchResult(
                sku=r["mpn"],
                name=r["mpn"],
                description=r.get("description") or "",
                exact=(r["mpn"].strip().lower() == term.strip().lower()),
                price=(f"{r['price_breaks'][0]['price']:.2f} {r['price_breaks'][0]['currency']}"
                       if r.get("price_breaks") else None),
                link=r.get("url") or "",
                image_url=r.get("image") or "",
                existing_part=getattr(existing, "part", None),
            ))
        return search_results

    def get_import_data(self, supplier_slug, part_id):
        """
        part_id here is the mpn we used as `sku` in get_search_results —
        re-searching by it gets the same normalized dict, no separate
        cache needed since mouser_lookup's own search() already caches
        short-term (see inventree_lookup.py's equivalent pattern).
        """
        api_key = self.get_setting("OMG_MOUSER_API_KEY")
        results = search_by_mpn(part_id, api_key) if api_key else []
        exact = [r for r in results if r["mpn"].strip().lower() == part_id.strip().lower()]
        if exact:
            return exact[0]
        if len(results) == 1:
            return results[0]
        raise supplier.PartNotFoundError()

    def get_pricing_data(self, data):
        return {
            pb["quantity"]: (pb["price"], pb["currency"])
            for pb in data.get("price_breaks", [])
        }

    def get_parameters(self, data):
        return [
            supplier.ImportParameter(name=name, value=value)
            for name, value in (data.get("attributes") or {}).items()
            if value
        ]

    def import_part(self, data, *, category, creation_user):
        # REFACTORED: was get_or_create(IPN__iexact=data["mpn"], ...) — a
        # real screenshot of this project's actual InvenTree "Edit Part"
        # form confirmed IPN is genuinely never used (empty, not
        # required) while the real part number goes into `name` (the
        # actually-required field). get_or_create() can't take a Q
        # object in its lookup kwargs, so this is a manual find-then-
        # create instead of a single get_or_create() call — checks name
        # first (what's actually populated in practice); IPN is still
        # checked too (harmless if a part happens to have it set for
        # some other reason), but is no longer WRITTEN on create -
        # explicitly not wanted, confirmed directly.
        part = Part.objects.filter(
            Q(name__iexact=data["mpn"]) | Q(IPN__iexact=data["mpn"]), purchaseable=True,
        ).first()
        if not part:
            part = Part.objects.create(
                name=data["mpn"],
                description=data.get("description") or "",
                link=data.get("url") or "",
                category=category,
                purchaseable=True,
                active=True,
                virtual=False,
            )

        # Was `if created and data.get("image") and ...` - only ever
        # downloaded an image for a part created THIS exact call.
        # Confirmed as the actual bug: the setting was already enabled,
        # yet no image appeared - because the part being imported
        # already existed in InvenTree (matched by the name/IPN lookup
        # above), so `created` was False and this whole block was
        # skipped, even though that existing part had no image of its
        # own yet. `not part.image` instead - downloads whenever the
        # part is missing an image, regardless of whether the Part
        # record itself was just created this call or already existed
        # from an earlier import/pending-part resolution.
        image_url = _usable_image_url(data.get("image"))
        if not part.image and image_url and self.get_setting("OMG_DOWNLOAD_MOUSER_IMAGES", False):
            try:
                file, fmt = self.download_image(image_url)
                part.image.save(f"part_{part.pk}_image.{fmt.lower()}", file)
            except Exception:
                # A failed image must never block the part import itself,
                # but the reason has to be findable: logger.warning alone
                # only reached the server log, never InvenTree's Error Logs
                # page, so failures looked like "no image, no trace".
                # _log_image_error records it in Settings -> System ->
                # Error Logs (reason + URL) as well as the server log.
                _log_image_error(part, image_url)

        return part

    def import_manufacturer_part(self, data, *, part):
        manufacturer_name = data.get("manufacturer") or "Unknown"
        mfr, _ = Company.objects.get_or_create(
            name__iexact=manufacturer_name,
            defaults={"name": manufacturer_name, "is_manufacturer": True, "is_supplier": False},
        )
        mfr_part, _ = ManufacturerPart.objects.get_or_create(
            MPN=data["mpn"], manufacturer=mfr, part=part,
        )
        return mfr_part

    def import_supplier_part(self, data, *, part, manufacturer_part):
        supplier_part, _ = SupplierPart.objects.get_or_create(
            SKU=data.get("spn") or data["mpn"],
            supplier=self.supplier_company,
            part=part,
            manufacturer_part=manufacturer_part,
            defaults={"link": data.get("url") or ""},
        )

        SupplierPriceBreak.objects.filter(part=supplier_part).delete()
        SupplierPriceBreak.objects.bulk_create([
            SupplierPriceBreak(
                part=supplier_part, quantity=pb["quantity"],
                price=pb["price"], price_currency=pb["currency"],
            )
            for pb in data.get("price_breaks", [])
        ])

        return supplier_part
