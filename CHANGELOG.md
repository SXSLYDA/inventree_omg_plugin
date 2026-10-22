# Changelog

Add notes for the next release under **Unreleased** as you work. `build-and-bump.ps1` turns that heading into the version number and date when you release. Say when a release adds a migration - that decides whether rolling back to the previous tag is safe.

Versions before the first tagged release (up to 0.1.32) were deployed straight from `master` and have no notes here.

## Unreleased

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

