# Changelog

Add notes for the next release under **Unreleased** as you work. `build-and-bump.ps1` turns that heading into the version number and date when you release. Say when a release adds a migration - that decides whether rolling back to the previous tag is safe.

Versions before the first tagged release (up to 0.1.32) were deployed straight from `master` and have no notes here.

## Unreleased

## 0.6.9 — 2026-10-23

- **Mouser moved to its own plugin, [inventree-mouser](https://github.com/SXSLYDA/inventree-mouser) (Mouser Integration)** - needs v0.1.0 or newer, with its own line in plugins.txt (replaces `mouser-lookup` / mouser_shared). The two plugins update independently: `setup.py` asks for a minimum version, not a pin, and `build-and-bump.ps1` no longer moves a pin. `tools/server-update.sh` stops before changing anything if plugins.txt has no inventree-mouser line. It now does the Import from Supplier wizard, creating parts from Mouser for the pending-parts dashboard item, and its new Mouser order import and cart features. Removed here: `mouser_supplier.py`, the `OMG_MOUSER_API_KEY` and `OMG_DOWNLOAD_MOUSER_IMAGES` settings. OMG's parameters still come in: the wizard's Parameters step asks this plugin (`mouser_import_parameters` hook), and parts the Mouser order import creates get them set straight away (`mouser_part_created` hook). The "Mouser Key" badge and the pending parts' key check read the Mouser plugin's Search API key. No panel rebuild needed.
  Parts the Mouser order import creates only get OMG's parameters when they're harness parts - one OMG is waiting for (a pending part: its kind gives the Component Type) or one Mouser files under a connector / terminal / contact / wire / cable / seal / boot / backshell category; a fuse, IC or tool on the same order is left alone instead of getting a guessed Component Type. OMG's parameters only fill blanks - a value already on a part (pending parts too) is never overwritten. Needs inventree-mouser with the `category` field (v0.1.0).
  **Before updating:** install the Mouser plugin - `sudo inventree-mouser-update v0.1.0` (adds its plugins.txt line). **After updating:** activate **Mouser Integration** in Admin Center > Plugins, enter its *Search API key* (your old Mouser key), *Order API key* and *Supplier* (the Mouser company), and *Download part images* if you had it on - the old settings here aren't copied across.

## 0.6.8 — 2026-10-22

- **Splice plugs** (needs OMG with the Splice Plug type): a new Component Type, Splice Plug - a plug whose pins are bussed together, bought complete or built from its own BOM, either way its own part and one line on the harness BOM. It fits connectors like other accessories (Connector Series, Ways, and Side = the side of the connector it plugs into, so a splice plug that's a plug has Side Receptacle), but it's only fitted where the wiring diagram has one: the OMG Accessories panel never offers it as a connector's Required Accessory. New optional parameter **Bussed Pins** ("A, B, C; D, E" - groups joined together) for later OMG checks; OMG's Part Data Check flags groups it can't read. Auto-fill types "splice plug" / "jumper plug" / "bussed" parts as Splice Plug and sets the Side the right way round. Run OMG's InvenTree Settings > "Set up lists" (Component Type gets Splice Plug) and "Create missing templates" (Bussed Pins) first.

- **Caps that are connector housings** (a plug fitted as a cap): the harness BOM now gets a blank for every cavity of the cap (from its Cavity Map and related blank) and the cap's own Required Accessories (its lock) - the "cap_accessories" lines OMG sends. A finished cap (Component Type Cover) is just its own line, as before.

- **Solder-cup connectors** (needs OMG with the Solder series): on the OMG Cavities panel pick the series "Solder" for cavities whose wire is soldered straight to the connector's pin - OMG then wants no contact, wire seal or blank for them (no more "no related contact" problems). Sealing can be mat or none, not individual. Set Strip Length and Min / Max Gauge on the connector itself (OMG Part Setup, now offered for connectors) and OMG checks the wires against them. Auto-fill sets "1-N: Solder / none" and Contact Count for a connector whose description says solder (solder cup, solder termination ...) and gives its way count - not PCB / board connectors.

- **Contact Min / Max Gauge shown in both units:** OMG Part Setup says how to write them (mm², e.g. 0.5, or AWG with "AWG", e.g. 20 AWG - a plain number is mm²) and shows the crossover as you type ("20 AWG = 0.5 mm²", "1.5 mm² ≈ 16 AWG"); the OMG Cavities parts table shows each contact's range the same way. Needs OMG with the AWG / mm² table used for contacts.

- **OMG Part Setup is a 3-step form:** 1 What it is (Component Type - saved first, "Save type and continue"), 2 Details (its parameters), 3 for a connector, Cavities & accessories (buttons to the OMG Cavities / OMG Accessories panels). Those panels only appear on a part InvenTree knows is a connector, so the type is saved before you're sent there - and the page reloads to show them when the type was only just saved.

- `build-and-bump.ps1` stages the built panel files (`omg_import_plugin/static`, new hashed files and removed old ones) so PyCharm's commit always includes them, and stops if `manifest.json` names a panel file that isn't there.

## 0.6.6 — 2026-10-22

- **Parts shown like InvenTree's own tables:** the OMG Cavities panel's related contacts / seals / blanks and the OMG Accessories panel's parts to use are now tables with each part's picture, part number (opens the part), IPN and description, a tick box and a search box - instead of a line of part numbers or a dropdown.
- **OMG Part Setup on a connector:** Cavity Map, Contact Count, Required and Preferred Accessories are shown with a button to the panel that sets them (OMG Cavities / OMG Accessories), where they're picked from the parts that fit, instead of free-text boxes.

## 0.6.5 — 2026-10-22

- **Review list: set-up problems point at the part, not at Mouser.** When the part is already linked and the problem is how it's set up (no related contacts or blank, a lock to choose, a Cavity Map problem), the item shows a button straight to the fix - "Fix related parts / cavities" (OMG Cavities panel) or "Choose it on the part (Preferred Accessories)" (OMG Part Setup) - instead of Import from Mouser / Search / Create part. Those are still there under "Use a different part...". Rebuild the panels for this release (no `-SkipBuild`).

- **Fixed: parameter events never reached the plugin.** InvenTree names model events after the database table, and part parameters are still in `part_partparameter`, so the plugin listened for `common_parameter.saved`, which never fires. It now asks InvenTree for the table name. This also fixes the gauge auto-fill (Gauge mm2 <-> AWG), which had stopped filling for the same reason, and makes OMG's refresh on a Cavity Map / Plating change work.

- **The release commit is tagged for you:** `build-and-bump.ps1` installs a git hook (`tools/git-hooks/post-commit`) that tags the commit changing `version.py` as `vX.Y.Z` - commit in PyCharm, push with "Push tags", done. Running the script again is safe: before committing it keeps the version it bumped and only rebuilds; after committing but not pushing, or with nothing new since the last release, it stops and says what to do; a release committed without a tag gets tagged instead of bumped again.

## 0.6.3 — 2026-10-22

- **OMG sees part changes straight away** (needs OMG with /api/parts/refresh/): saving a part parameter (Cavity Map, Contact Count, Plating ...) or adding a related part tells OMG, which re-reads that part and re-works the wire ends of the harnesses using it - instead of showing the old values until its cache expired (15 minutes to an hour). Removing a related part on the OMG Cavities panel tells OMG too; for a parameter or related part deleted elsewhere in InvenTree use OMG's Sync Check "Re-read InvenTree".

## 0.6.2 — 2026-10-22

- **Gold or nickel contacts per wire** (needs OMG with Plating): Auto-fill fills the new **Plating** parameter on contacts from their description (gold, nickel, tin, silver) and **Shielded** on multicore cables (shielded, screened, CAN, J1939). OMG puts the gold contact on twisted pairs and shielded multicore cores and the other one on everything else, so a connector can carry both. Run OMG's "Create missing templates" and "Set up lists" first.
- **Needs setup: "Connector related parts missing"** (needs OMG with it): a connector whose related parts don't cover its Cavity Map - no Contact of a series it uses, not exactly one Blank of it, or no Seal for individually sealed cavities. The part link opens the OMG Cavities panel, where related parts are added.

- Auto-fill gives no Cavity Map to connectors with molded-in contacts (buss bars, terminating resistors, diodes, J1939 "Y" connectors) - nothing is crimped into them, and a map would ask for a blank in every cavity.

## 0.6.1 — 2026-10-22

- **Auto-fill gives Deutsch connectors a Cavity Map** from the part number: DT, DTM and DTP are numbered (`DT04-08PA` = `1-8: DT-16 / mat`, `DTM06-12SA` = `1-12: DTM-20 / mat`), DRC numbered with size 16 (`DRC16-70SA` = `1-70: DT-16 / mat`), the CAT 70-pin ECM plugs (147-1445, 147-1446, 160-7689) `1-70: DT-16 / mat`, HD10 lettered (`HD16-3-96S` = `A-C: DT-16 / mat`), sealed by the housing's grommet (`mat`), with Contact Count to match. Deutsch contacts get the matching Contact Series from theirs (`0460-202-16141` = DT-16, `0462-201-2031` = DTM-20, `1060-16-0122` = DT-16), and Deutsch blanks too (`0413-217-1605` and `114017` = DT-16, `0413-204-2005` = DTM-20). Skipped when the description's way count or the part's own Contact Count disagrees, or the Contact Series list has no matching series. AEC is `1-40: DT-16 / mat`. HD30 / HDP20 from the insert arrangement in the part number (Deutsch's technical manual), for the inserts with one contact size: 18-8, 18-14, 18-21, 24-16, 24-23, 24-31, 24-33 - lettered ones skip I / O / Q as Deutsch does (`HDP24-18-14PE` = `A-H, J-N, P: DT-16 / mat / maxod 3.05`, the E seal's largest wire). Mixed-size inserts (18-6, 18-20, 24-9, 24-14, 24-18, 24-19, 24-21, 24-29, 24-35, 24-47, 24-91) and other makes (TE, Bosch, PT) are left for the Cavities panel. Fixed: an HDP20 described "..., 23 way, black, E seal, ..." was taken for a Seal.

- `build-and-bump.ps1` no longer commits or tags: it builds, bumps and stamps the changelog, then lists the changes and prints the commit, tag and push commands so you can review first.

- **Auto-fill warns before filling** when a template's choices would refuse a value (e.g. Component Type still on its old "Wire, Multicore, Connector ..." choices refusing Lock and Terminal), and says to run OMG's "Set up lists". Refused values after a fill are grouped ("Component Type: Invalid choice - 58 parts: ...") at the top of the tab instead of a cut-off list under the button.
- CI deploy: the SSH user defaults to root, and a missing host or key secret gives a clear error.

## 0.5.0 — 2026-10-22

- **`inventree-omg-update` installs the plugin itself** with InvenTree's pip (output shown) before `invoke update`, which hid pip's output and left 0.2.0 installed on the 0.4.0 update. Download the command again once (see README) - the copy in `/usr/local/bin` doesn't update itself.

## 0.4.0 — 2026-10-22

- **Auto-fill tab** in the OMG Parts widget: works out each part's Component Type and OMG's parameters from its description and category ("Recept., DT, 8 way" = Connector, DT, 8 cavities, Receptacle; "Wire TXL 16AWG ... Red/Blue stripe" = Wire, 16 AWG / 1.5 mm², TXL, Tinned Copper, 125, Red / Blue; contact wire ranges, seal ODs, accessory ways and exit angles). Shows everything first, grouped by type, to untick; only fills parameters a part doesn't have, never overwrites. Then use OMG's "Fill lists from parts" to start the selection lists.
- **New Component Type "Terminal"** (needs OMG with it) for ring lugs, spades and eyelets used on their own: no Cavity Map or Contact Count - their "pins" are the terminal symbols `- + * #`, one terminal per symbol. Just the wire range (Min / Max Gauge, checked by OMG against the wires crimped into it) and Strip Length. Auto-fill and the Mouser import recognise lugs and spades as Terminals.
- **Part links open inside InvenTree** on the panel that fixes them - OMG Part Setup, or OMG Cavities for cavity problems - instead of a new tab that reloaded InvenTree (and checked the login) every click. Ctrl / middle-click still opens a new tab.
- **No more "timeout of 5000ms exceeded"** in the OMG Parts widget and the OMG part panels: every call that reaches OMG (or Mouser) gets a longer timeout, like the sync panel.

## 0.2.0 — 2026-10-21

First tagged release - release as a minor version (`.\build-and-bump.ps1 -Bump minor`, 0.1.32 -> 0.2.0). No new migrations.

- **Releases and deploys:**
  - The server's `plugins.txt` now pins the plugin to a release tag (`...inventree_omg_plugin.git@vX.Y.Z`), so every update really reinstalls - no more `--force-reinstall`.
  - Only a tag deploys. CI (`.github/workflows/ci.yml`, replacing `deploy.yml`) checks the tag matches `version.py` and this changelog, publishes a GitHub release with these notes, and updates InvenTree. A push to `master` or a branch no longer touches the server.
  - `build-and-bump.ps1` builds the panels, moves the mouser_shared pin, checks the Python compiles, bumps the version, stamps this changelog, then commits and tags `vX.Y.Z`. New `-Bump`, `-Version`, `-SkipBuild` and `-Yes` options.
  - `tools/server-update.sh` (install as `inventree-omg-update`): backs up, pins `plugins.txt` to a tag, updates and restarts InvenTree, and confirms the installed version. `--check-only` and `--allow-downgrade` for checking and rolling back.
- **BOM quantities are exact on every sync:**
  - Each InvenTree part's quantity is added up across connectors, contacts, seals, blanks, wires, multicores, accessories, junctions and sub-harnesses, and written once. A part in two categories (e.g. a terminal sent as a connector and as a contact) used to keep only the last category's count.
  - An existing BOM line is corrected in place - nobody has to delete it first. Duplicate lines for one part, or InvenTree refusing the save, go in the review list instead of passing silently.
  - Terminals: one per different terminal symbol (`-` `+` `*` `#`), so a battery connector with `+` and `-` is 2.
- **Review queue:**
  - A flagged connector carries its real quantity, so resolving it adds the right count.
  - Resolving a review item **adds** its quantity to the part's existing line instead of replacing it.
  - Notes and warnings about a connector (quantity 0) never change the BOM when resolved.
- **Selection lists (restored):** the selection-list work from 13 October never reached GitHub - it's back. The OMG Part Setup panel offers OMG's lists as dropdowns (several values for an accessory's Connector Series), snaps typed values to the list's spelling, and InvenTree refuses a several-value parameter with a value not in its list. Parts created from Mouser (wizard or OMG pending parts) get OMG's parameters filled in.
- **Cavities - one source:** the Cavity Map is the one list of a connector's cavities, as ranges (`1-12`, `A-D`) or single cavities (`A, B, C, E`). The cavity grid's Series is picked from the Contact Series list (a series not in it is refused), and saving sets **Contact Count** to the number of cavities in the map. Needs OMG with Cavity Layout removed. **Rebuild the panels** for this release (don't use `-SkipBuild`).
- **New dashboard item "OMG Parts"**, two tabs. **Needs setup** shows OMG's Part Data Check: parts OMG's harnesses use that were never set up (no Component Type) or no longer exist, missing required parameters, Cavity Maps OMG can't read, Contact Count not matching the map, and cavity series no contact has - parts on a harness first, with where each is used. OMG keeps the last check and re-checks in the background ("Check again"). Each part's **OMG Part Setup** panel shows that part's own problems at the top. Needs OMG with the stored data check. **Pending (Mouser)** lists the parts picked from Mouser in OMG that aren't in InvenTree yet, with where each is used and whether InvenTree already has it. Tick and import: an exact match (name, MPN or Mouser number) is linked; anything else is created from Mouser - manufacturer part, Mouser supplier part, prices and OMG's parameters - in the **pending category** (new setting *Pending parts category*, "OMG Pending Parts" created the first time), then moved by hand to its own category. OMG is told so its pending parts become the real InvenTree parts. InvenTree's Import from Supplier wizard is unchanged. The unused `resolve-pending/` endpoint (it could never report back to OMG) is replaced by `pending-parts/`. Needs OMG with the new pending-parts list (where each part is used).
- **Matching by manufacturer / supplier part number:** a part number that isn't an InvenTree part's name or IPN is matched by an exact MPN or SKU before the loose search - the same rule OMG uses - so accessories carrying the maker's number (Deutsch, TE ...) link in the sync. Several parts sharing it go to review.

