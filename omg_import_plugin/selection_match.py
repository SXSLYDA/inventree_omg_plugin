"""
Matching a typed or imported value to an entry of a selection list - which
entry "dt", "Deutsch DT Series" or "BK" most likely means. Pure: no Django,
no InvenTree. OMG has an identical copy (components/selection_match.py) for
its Selection Lists page, so a value is matched the same way in both places -
change both together.

  suggest("dt", ["DT", "DTM"])                 -> "DT"   (case / spaces / dashes)
  suggest("Deutsch DT Series", ["DT", "DTM"])  -> "DT"   (an entry as a word in it)
  suggest("DT 16", ["DT-16", "DTM-20"])        -> "DT-16"
  suggest("BK", ["Black"], {"BK": "Black"})    -> "Black" (aliases, e.g. colour codes)
  suggest("HDP", ["DT", "DTM"])                -> None   (nothing close)
"""
import re

MULTI_SPLIT = re.compile(r'[,;\n]+')


def norm(value):
    """Letters and digits only, upper case: 'dt-16' == 'DT 16' == 'DT16'."""
    return re.sub(r'[^0-9A-Za-z]+', '', str(value or '')).upper()


def split_items(raw):
    """'DT, DTM' / 'DT; DTM' -> ['DT', 'DTM'] (for parameters holding several values)."""
    return [p.strip() for p in MULTI_SPLIT.split(str(raw or '')) if p.strip()]


def join_items(items):
    """['DT', 'DTM', 'DT'] -> 'DT, DTM' (duplicates dropped, order kept)."""
    seen, out = set(), []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return ', '.join(out)


def suggest(value, entries, aliases=None):
    """
    The entry `value` most likely means, or None. Tried in order:
      1. the same letters and digits ("dt" -> "DT", "DT 16" -> "DT-16")
      2. an alias ({"BK": "Black"} - matched the same way)
      3. an entry that is a whole word (or two or three adjacent words) of
         the value: "Deutsch DT Series" -> "DT". The longest such entry
         wins; two different entries of the same length = unclear = None.
    """
    key = norm(value)
    if not key:
        return None
    for entry in entries:
        if norm(entry) == key:
            return entry
    for alias, entry in (aliases or {}).items():
        if norm(alias) == key and entry in entries:
            return entry
    words = [norm(w) for w in re.split(r'[^0-9A-Za-z]+', str(value)) if w]
    pieces = {''.join(words[i:i + n]) for n in (1, 2, 3) for i in range(len(words) - n + 1)}
    found = [e for e in entries if norm(e) and norm(e) in pieces]
    if not found:
        return None
    longest = max(len(norm(e)) for e in found)
    best = {e for e in found if len(norm(e)) == longest}
    return best.pop() if len(best) == 1 else None
