"""
COPY of OMG's components/cavity_map.py (pure Python, no Django) so the
Cavity panel reads and writes the Cavity Map with exactly the same rules.
Keep the two in step.

Connector cavity maps - which contact series each cavity takes, how it's
sealed and the largest wire it accepts. Read from the connector's InvenTree
Cavity Map parameter (role connector.cavity_map):

    1-12: DT-16 / individual / maxod 3.2; A-D: DT-8 / mat; C: DT-12

  cavities   "1-12", "A-D", "1,3,5" or one cavity; a single cavity listed
             later overrides the range it sits in ("C" above)
  series     the Contact Series its contacts, seals and blanks must have
  sealing    individual (a wire seal per used cavity), mat (sealed as a
             whole - no wire seals), none (default)
  maxod      optional: largest wire insulation OD in mm ("maxod 3.2",
             "max od 3.2", "od<=3.2")

Entries are split by ";" or new lines, options by "/". The older Cavity
Groups parameter ("1-12: 16; A-D: 8") still works when there's no map - its
value is read as the series, with no sealing and no OD limit.
"""
import re
from dataclasses import dataclass, field

SEALING_INDIVIDUAL = 'individual'
SEALING_MAT = 'mat'
SEALING_NONE = 'none'
SEALINGS = (SEALING_INDIVIDUAL, SEALING_MAT, SEALING_NONE)

_OD_RE = re.compile(r'^(?:max\s*od|maxod|od\s*<=?|max)\s*[:=]?\s*(\d+(?:[.,]\d+)?)\s*(?:mm)?$', re.I)


@dataclass
class CavityRange:
    tokens: list
    series: str
    sealing: str = SEALING_NONE
    max_od: float = None
    problems: list = field(default_factory=list)

    def covers(self, cavity):
        return any(token_matches(t, cavity) for t in self.tokens)

    def describe(self):
        bits = [', '.join(self.tokens) + ': ' + self.series, self.sealing]
        if self.max_od is not None:
            bits.append(f"max OD {self.max_od:g} mm")
        return ' / '.join(bits)


def norm_series(value):
    """Series compared on letters and digits only: 'DT-16' == 'dt 16' == 'DT16'."""
    return re.sub(r'[^0-9A-Za-z]+', '', str(value or '')).upper()


def token_matches(token, cavity):
    """'1-12' / 'A-D' ranges, or a single cavity name; case-insensitive."""
    token, cavity = str(token).strip(), str(cavity).strip()
    if not token or not cavity:
        return False
    if '-' in token[1:]:
        lo, hi = [t.strip() for t in token.split('-', 1)]
        if lo.isdigit() and hi.isdigit() and cavity.isdigit():
            return int(lo) <= int(cavity) <= int(hi)
        if len(lo) == 1 and len(hi) == 1 and len(cavity) == 1 and lo.isalpha() and hi.isalpha() and cavity.isalpha():
            return lo.upper() <= cavity.upper() <= hi.upper()
        return False
    return token.upper() == cavity.upper()


def expand_token(token):
    """'1-4' -> ['1','2','3','4']; 'A-C' -> ['A','B','C']; 'X1' -> ['X1'] (for counting blanks)."""
    token = str(token).strip()
    if '-' in token[1:]:
        lo, hi = [t.strip() for t in token.split('-', 1)]
        if lo.isdigit() and hi.isdigit() and int(lo) <= int(hi) and int(hi) - int(lo) < 500:
            return [str(n) for n in range(int(lo), int(hi) + 1)]
        if len(lo) == 1 and len(hi) == 1 and lo.isalpha() and hi.isalpha() and lo.upper() <= hi.upper():
            return [chr(c) for c in range(ord(lo.upper()), ord(hi.upper()) + 1)]
    return [token] if token else []


def parse_cavity_map(raw):
    """Cavity Map text -> [CavityRange]; entries that can't be read carry a problem message."""
    ranges = []
    for entry in re.split(r'[;\n]+', str(raw or '')):
        if not entry.strip():
            continue
        match = re.match(r'^\s*(.+?)\s*[:=]\s*(.+?)\s*$', entry)
        if not match:
            ranges.append(CavityRange([], '', problems=[f"'{entry.strip()}' isn't 'cavities: series'"]))
            continue
        tokens = [t.strip() for t in match.group(1).split(',') if t.strip()]
        parts = [p.strip() for p in match.group(2).split('/')]
        series, options = parts[0], parts[1:]
        rng = CavityRange(tokens, series)
        for option in options:
            low = option.lower()
            if low in SEALINGS:
                rng.sealing = low
            elif _OD_RE.match(option):
                rng.max_od = float(_OD_RE.match(option).group(1).replace(',', '.'))
            elif option:
                rng.problems.append(f"'{option}' isn't individual / mat / none or 'maxod <mm>'")
        ranges.append(rng)
    return ranges


def parse_cavity_groups_as_map(raw):
    """The older Cavity Groups value as ranges: series = the size, no sealing, no OD limit."""
    ranges = []
    for entry in re.split(r'[;\n]+', str(raw or '')):
        match = re.match(r'^\s*(.+?)\s*[:=]\s*(.+?)\s*$', entry)
        if match:
            tokens = [t.strip() for t in match.group(1).split(',') if t.strip()]
            if tokens:
                ranges.append(CavityRange(tokens, match.group(2).strip()))
    return ranges


def range_for_cavity(ranges, cavity):
    """
    The range a cavity belongs to: a range that names it on its own wins
    over a span ("C" over "A-D"); otherwise the first range covering it.
    None if no range covers it.
    """
    single = [r for r in ranges if any(str(t).strip().upper() == str(cavity).strip().upper() for t in r.tokens)]
    if single:
        return single[-1]
    for rng in ranges:
        if rng.covers(cavity):
            return rng
    return None


def mapped_cavities(ranges):
    """Every cavity the map names (ranges expanded), in map order without repeats."""
    seen, result = set(), []
    for rng in ranges:
        for token in rng.tokens:
            for cavity in expand_token(token):
                if cavity.upper() not in seen:
                    seen.add(cavity.upper())
                    result.append(cavity)
    return result
