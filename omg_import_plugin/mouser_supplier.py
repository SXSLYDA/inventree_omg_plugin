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

from django.conf import settings
from django.db.models import Q

from company.models import Company, ManufacturerPart, SupplierPart, SupplierPriceBreak
from part.models import Part
from plugin.base.supplier import helpers as supplier
from plugin.base.supplier.mixins import SupplierMixin

from mouser_lookup import search_by_mpn


class MouserSupplierMixin(SupplierMixin):
    """
    Mix into the plugin class alongside its other mixins (see core.py).
    Method names/signatures follow InvenTree's own documented
    SupplierMixin contract exactly — see
    https://docs.inventree.org/en/stable/plugins/mixins/supplier/
    """

    def get_suppliers(self):
        return [supplier.Supplier(slug="mouser", name="Mouser Electronics")]

    def get_search_results(self, supplier_slug, term):
        api_key = getattr(settings, "OMG_MOUSER_API_KEY", None)
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
        api_key = getattr(settings, "OMG_MOUSER_API_KEY", None)
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
        # first (what's actually populated in practice), IPN too since
        # it's harmless and forward-compatible if it's ever used.
        part = Part.objects.filter(
            Q(name__iexact=data["mpn"]) | Q(IPN__iexact=data["mpn"]), purchaseable=True,
        ).first()
        created = False
        if not part:
            part = Part.objects.create(
                name=data["mpn"],
                IPN=data["mpn"],
                description=data.get("description") or "",
                link=data.get("url") or "",
                category=category,
                purchaseable=True,
                active=True,
                virtual=False,
            )
            created = True

        if created and data.get("image") and self.get_setting("OMG_DOWNLOAD_MOUSER_IMAGES", False):
            try:
                file, fmt = self.download_image(data["image"])
                part.image.save(f"part_{part.pk}_image.{fmt.lower()}", file)
            except Exception:
                pass  # image download failing shouldn't block the part import itself

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
