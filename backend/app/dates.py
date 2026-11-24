"""One place for dates: any accepted source format in, canonical ISO for storage, DD.MM.YYYY for everything shown."""
import re
from datetime import date, datetime

FMT = "%d.%m.%Y"
_MONTHS = {}
for i, names in enumerate([
        "january januar jan", "february februar feb", "march marec marca mar", "april aprila apr", "may maj maja",
        "june junij junija jun", "july julij julija jul", "august avgust avgusta aug avg", "september septembra sep sept",
        "october oktober oktobra oct okt", "november novembra nov", "december decembra dec"], 1):
    _MONTHS.update({n: i for n in names.split()})
_NAMES = "|".join(sorted(_MONTHS, key=len, reverse=True))
_NUM = re.compile(r"(\d{1,2})([./-])(\d{1,2})\2(\d{2}|\d{4})")
_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:[T ].*)?")
_DMY_NAME = re.compile(rf"(\d{{1,2}})\.?\s+({_NAMES})\.?,?\s+(\d{{4}})", re.I)
_MDY_NAME = re.compile(rf"({_NAMES})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})", re.I)


def _mk(y, m, d) -> date | None:
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def parse_date(s) -> date | None:
    """None when `s` is not a whole date. Slash dates are DD/MM unless the month would be > 12 (then MM/DD)."""
    if isinstance(s, datetime):
        return s.date()
    if isinstance(s, date):
        return s
    s = str(s or "").strip()
    if m := _ISO.fullmatch(s):
        return _mk(*m.groups())
    if m := _NUM.fullmatch(s):
        a, sep, b, y = m.groups()
        y = int(y) + 2000 if len(y) == 2 else int(y)
        return _mk(y, b, a) if sep != "/" or int(b) <= 12 else _mk(y, a, b)
    if m := _DMY_NAME.fullmatch(s):
        return _mk(m[3], _MONTHS[m[2].lower()], m[1])
    if m := _MDY_NAME.fullmatch(s):
        return _mk(m[3], _MONTHS[m[1].lower()], m[2])
    return None


def dmy(s) -> str:
    """DD.MM.YYYY of a date-like value; anything unparseable is returned unchanged."""
    d = parse_date(s)
    return d.strftime(FMT) if d else ("" if s is None else str(s))
