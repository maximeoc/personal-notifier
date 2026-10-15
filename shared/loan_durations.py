"""Durée des prêts par plateforme -> onglet « durées prêts » du Google Sheet.

Chaque *_diversification.py qui récupère déjà ses prêts un par un (JSON ou
tableau HTML) transmet ici la liste brute ; ce module en déduit, pour chaque
prêt, la durée initiale et la durée restante en mois, puis écrit sur la ligne
de la plateforme : durée min / moyenne / max (moyenne pondérée par l'encours),
durée restante moyenne, nombre de prêts et la source des champs utilisés.
L'écran « Durée des prêts » de la Web App (Appsscripts/plateformes) lit cet
onglet.

Les noms de champs de durée n'ont jamais été vérifiés en direct sur les
plateformes : la détection est volontairement tolérante (clés JSON courantes,
intitulés de colonnes FR/EN) et prudente (une durée sans unité explicite est
ignorée, bornes 0-600 mois). Quand rien n'est reconnu, rien n'est écrit et les
NOMS des champs disponibles (jamais leurs valeurs) sont journalisés pour
pouvoir ajuster la détection.

Toute erreur est journalisée sans jamais interrompre le rapport appelant
(voir record_platform_loan_durations()).
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable, Iterable

logger = logging.getLogger(__name__)

DURATIONS_SHEET_NAME = "durées prêts"
DURATIONS_HEADERS = [
    "Plateforme", "Secteur", "Durée min (mois)", "Durée moyenne (mois)", "Durée max (mois)",
    "Commentaire", "Mis à jour le", "Durée restante moyenne (mois)", "Nb prêts", "Source",
]
AUTO_SOURCE_PREFIX = "auto"

DAYS_PER_MONTH = 365.25 / 12
MAX_MONTHS = 600
MAX_FIELD_NAMES_LOGGED = 60


@dataclass
class LoanDuration:
    amount: float
    term_months: float | None = None
    remaining_months: float | None = None


@dataclass
class DurationExtraction:
    loans: list[LoanDuration]
    source: str = ""
    available_fields: list[str] | None = None
    # Exemple de valeur des seuls champs dont le NOM évoque une date ou une durée (diagnostic, jamais de montant).
    samples: dict[str, str] | None = None


_SAMPLE_NAME_RE = re.compile(r"date|dur|term|month|mois|day|jour|end|fin|matur|period|year|annee|echeance|expir|remain|rest")


def _samples_from_pairs(pairs: Iterable[tuple[str, object]]) -> dict[str, str]:
    samples = {}
    for name, value in pairs:
        if len(samples) >= 20:
            break
        if isinstance(value, (dict, list)) or value in (None, ""):
            continue
        if _SAMPLE_NAME_RE.search(_key(name)) and not re.search(r"amount|montant|price|prix|balance|solde", _key(name)):
            samples[str(name)] = str(value)[:40]
    return samples


# --------------------------------------------------------------------------
# Valeurs élémentaires
# --------------------------------------------------------------------------

def _norm(text) -> str:
    """Minuscules, sans accents ni ponctuation : 'Date d’échéance' -> 'date d echeance'."""
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def _key(text) -> str:
    """Clé JSON comparable quelle que soit la casse : 'maturity_date' == 'maturityDate'."""
    return re.sub(r"[^a-z0-9]", "", _norm(text))


def _valid_months(value: float | None, allow_zero: bool = False) -> float | None:
    if value is None:
        return None
    if value < 0 or value > MAX_MONTHS or (value == 0 and not allow_zero):
        return None
    return value


_MONTHS_FR_EN = {
    "jan": 1, "janv": 1, "january": 1, "janvier": 1,
    "feb": 2, "fev": 2, "fevr": 2, "february": 2, "fevrier": 2,
    "mar": 3, "mars": 3, "march": 3,
    "apr": 4, "avr": 4, "april": 4, "avril": 4,
    "may": 5, "mai": 5,
    "jun": 6, "juin": 6, "june": 6,
    "jul": 7, "juil": 7, "july": 7, "juillet": 7,
    "aug": 8, "aou": 8, "aout": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9, "septembre": 9,
    "oct": 10, "october": 10, "octobre": 10,
    "nov": 11, "november": 11, "novembre": 11,
    "dec": 12, "december": 12, "decembre": 12,
}


def parse_date(value) -> date | None:
    """Date depuis ISO, 'JJ/MM/AAAA', 'JJ.MM.AAAA', '15 Oct 2026', '15 octobre 2026' ou timestamp Unix."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e11 else value
        if seconds < 1e8:  # trop petit pour une date plausible (≈ 1973) : sans doute une durée
            return None
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            return None

    text = str(value).strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        return _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b", text)
    if m:
        return _safe_date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    m = re.match(r"^(\d{4})/(\d{1,2})/(\d{1,2})\b", text)
    if m:
        return _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r"^(\d{1,2})\s+([^\W\d_]+)\.?\s+(\d{4})\b", _norm_spaces_keep_digits(text))
    if m and m.group(2) in _MONTHS_FR_EN:
        return _safe_date(int(m.group(3)), _MONTHS_FR_EN[m.group(2)], int(m.group(1)))
    return None


def _norm_spaces_keep_digits(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return " ".join(text.replace(",", " ").split())


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def months_between(start: date, end: date) -> float:
    return (end - start).days / DAYS_PER_MONTH


_UNIT_PATTERNS = [
    (re.compile(r"^(?:mois|months?|mths?|mo|m)$"), 1.0),
    (re.compile(r"^(?:jours?|days?|j|d)$"), 1 / DAYS_PER_MONTH),
    (re.compile(r"^(?:ans?|annees?|years?|yrs?|y)$"), 12.0),
    (re.compile(r"^(?:semaines?|weeks?|sem|w)$"), 7 / DAYS_PER_MONTH),
]


def _unit_factor(unit_text) -> float | None:
    unit = _norm(unit_text)
    for word in unit.split() or [""]:
        for pattern, factor in _UNIT_PATTERNS:
            if pattern.match(word):
                return factor
    return None


def parse_duration_text(text, default_factor: float | None = None) -> float | None:
    """'36 mois', '12 months', '180 days', '2 ans', '1 an 6 mois' -> mois.
    Un nombre seul n'est accepté que si `default_factor` (unité connue par ailleurs) est fourni."""
    if text is None:
        return None
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return text * default_factor if default_factor else None
    normalized = _norm_spaces_keep_digits(str(text))
    parts = re.findall(r"(\d+(?:[.,]\d+)?)\s*([a-z]+)", normalized)
    total = 0.0
    found = False
    for number, unit in parts:
        factor = _unit_factor(unit)
        if factor is None:
            continue
        total += float(number.replace(",", ".")) * factor
        found = True
    if found:
        return total
    m = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*", normalized)
    if m and default_factor:
        return float(m.group(1).replace(",", ".")) * default_factor
    return None


def strip_html(fragment) -> str:
    """Texte visible d'un fragment HTML (balises retirées, entités décodées, espaces compactés)."""
    import html as html_lib
    text = re.sub(r"<[^>]+>", " ", str(fragment or ""))
    return " ".join(html_lib.unescape(text).split())


def table_headers_from_html(page_html: str) -> list[str]:
    """Intitulés du premier <thead> d'une page (liste vide s'il n'y en a pas)."""
    match = re.search(r"<thead[^>]*>(.*?)</thead>", page_html or "", re.DOTALL | re.IGNORECASE)
    if not match:
        return []
    return [strip_html(h) for h in re.findall(r"<th[^>]*>(.*?)</th>", match.group(1), re.DOTALL | re.IGNORECASE)]


def parse_amount(value) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value or "").replace("\u202f", "").replace("\xa0", "").replace(" ", "").replace("€", "")
    if "," in text and "." in text:
        text = text.replace(",", "") if text.rfind(".") > text.rfind(",") else text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return 0.0


# --------------------------------------------------------------------------
# Extraction depuis des objets JSON
# --------------------------------------------------------------------------

_TERM_MONTH_KEYS = {"terminmonths", "termmonths", "loanterminmonths", "loantermmonths", "durationinmonths",
                    "durationmonths", "periodinmonths", "periodmonths", "initialterminmonths", "originaltermmonths"}
_TERM_DAY_KEYS = {"termindays", "termdays", "loantermindays", "loantermdays", "durationindays", "durationdays",
                  "periodindays", "perioddays"}
_TERM_GENERIC_KEYS = {"term", "loanterm", "duration", "loanduration", "period", "loanperiod", "initialterm",
                      "originalterm", "investmentterm", "projectduration"}
_REMAINING_MONTH_KEYS = {"remainingterminmonths", "remainingtermmonths", "monthsleft", "remainingmonths"}
_REMAINING_DAY_KEYS = {"remainingtermindays", "remainingtermdays", "daysleft", "remainingdays", "daystomaturity",
                       "daysuntilmaturity"}
_REMAINING_GENERIC_KEYS = {"remainingterm", "remainingduration", "remainingperiod", "termleft"}
_UNIT_KEYS = {"termtype", "termunit", "termperiod", "durationunit", "durationtype", "periodtype", "periodunit", "unit",
              "termtypetitle"}
_MATURITY_KEYS = {"maturitydate", "maturity", "finalpaymentdate", "lastpaymentdate", "finalrepaymentdate",
                  "enddate", "loanenddate", "expectedenddate", "expectedrepaymentdate", "plannedenddate",
                  "maturityat", "dateend", "datefin", "dateecheance", "echeance", "lastinstallmentdate"}
_START_KEYS = {"issuedate", "issueddate", "issuedat", "startdate", "loanstartdate", "disbursementdate",
               "disbursedat", "fundeddate", "fundingdate", "listingdate", "investeddate", "investmentdate",
               "investedat", "purchasedate", "purchasedat", "agreementdate", "contractdate", "datedebut",
               "datedemission"}


def _walk(obj, path: str = "", depth: int = 0):
    """(chemin, clé normalisée, valeur) de chaque champ, en largeur d'abord jusqu'à 3 niveaux."""
    queue = [(obj, path, depth)]
    while queue:
        current, current_path, current_depth = queue.pop(0)
        if not isinstance(current, dict):
            continue
        for k, v in current.items():
            child_path = f"{current_path}.{k}" if current_path else str(k)
            yield child_path, _key(k), v
            if isinstance(v, dict) and current_depth < 3:
                queue.append((v, child_path, current_depth + 1))


def field_names(obj) -> list[str]:
    return sorted({path for path, _, v in _walk(obj) if not isinstance(v, dict)})[:MAX_FIELD_NAMES_LOGGED]


def _months_from_value_with_unit(value, siblings: dict) -> float | None:
    """Durée générique ('term': 12) : unité tirée de la valeur elle-même ou d'un champ voisin (termType…)."""
    if isinstance(value, dict):
        amount = next((value[k] for k in value if _key(k) in {"value", "amount", "count", "length", "number"}), None)
        unit = next((value[k] for k in value if _key(k) in {"unit", "type", "period", "termtype", "unittype"}), None)
        factor = _unit_factor(unit) if unit is not None else None
        if amount is not None and factor:
            return parse_duration_text(amount, factor)
        return None
    from_text = parse_duration_text(value) if isinstance(value, str) else None
    if from_text is not None:
        return from_text
    for unit_key in _UNIT_KEYS:
        if unit_key in siblings:
            factor = _unit_factor(siblings[unit_key])
            if factor:
                return parse_duration_text(value, factor)
    return None


def extract_from_mapping(item: dict, today: date) -> tuple[float | None, float | None, str]:
    """(durée initiale, durée restante, description des champs utilisés) d'un prêt JSON."""
    term = remaining = None
    term_source = remaining_source = ""
    maturity = start = None
    maturity_path = start_path = ""

    by_parent: dict[str, dict] = {}
    entries = list(_walk(item))
    for path, key, value in entries:
        parent = path.rsplit(".", 1)[0] if "." in path else ""
        by_parent.setdefault(parent, {})[key] = value

    for path, key, value in entries:
        parent = path.rsplit(".", 1)[0] if "." in path else ""
        siblings = by_parent.get(parent, {})
        if term is None:
            if key in _TERM_MONTH_KEYS:
                term = _valid_months(parse_duration_text(value, 1.0))
            elif key in _TERM_DAY_KEYS:
                term = _valid_months(parse_duration_text(value, 1 / DAYS_PER_MONTH))
            elif key in _TERM_GENERIC_KEYS:
                term = _valid_months(_months_from_value_with_unit(value, siblings))
            if term is not None:
                term_source = path
        if remaining is None:
            if key in _REMAINING_MONTH_KEYS:
                remaining = _valid_months(parse_duration_text(value, 1.0), allow_zero=True)
            elif key in _REMAINING_DAY_KEYS:
                remaining = _valid_months(parse_duration_text(value, 1 / DAYS_PER_MONTH), allow_zero=True)
            elif key in _REMAINING_GENERIC_KEYS:
                remaining = _valid_months(_months_from_value_with_unit(value, siblings), allow_zero=True)
            if remaining is not None:
                remaining_source = path
        if maturity is None and key in _MATURITY_KEYS:
            maturity = parse_date(value)
            maturity_path = path if maturity else ""
        if start is None and key in _START_KEYS:
            start = parse_date(value)
            start_path = path if start else ""

    if term is None and maturity and start:
        term = _valid_months(months_between(start, maturity))
        if term is not None:
            term_source = f"{start_path} → {maturity_path}"
    if remaining is None and maturity:
        remaining = _valid_months(max(0.0, months_between(today, maturity)), allow_zero=True)
        if remaining is not None:
            remaining_source = maturity_path

    sources = []
    if term_source:
        sources.append(f"durée={term_source}")
    if remaining_source:
        sources.append(f"restant={remaining_source}")
    return term, remaining, ", ".join(sources)


def durations_from_json_items(items: Iterable[tuple[dict, float]], today: date | None = None) -> DurationExtraction:
    """`items` : (objet JSON du prêt, encours en EUR)."""
    today = today or date.today()
    loans: list[LoanDuration] = []
    sources: dict[str, int] = {}
    first = None
    for item, amount in items:
        if first is None and isinstance(item, dict):
            first = item
        if not isinstance(item, dict) or not amount or amount <= 0:
            continue
        term, remaining, source = extract_from_mapping(item, today)
        loans.append(LoanDuration(amount=float(amount), term_months=term, remaining_months=remaining))
        if source:
            sources[source] = sources.get(source, 0) + 1
    return DurationExtraction(
        loans=loans,
        source=_describe_sources(sources),
        available_fields=field_names(first) if first is not None else [],
        samples=_samples_from_pairs((path, v) for path, _, v in _walk(first)) if first is not None else {},
    )


# --------------------------------------------------------------------------
# Extraction depuis un tableau HTML (intitulés de colonnes)
# --------------------------------------------------------------------------

_REMAINING_WORDS = ("restant", "restante", "restants", "restantes", "remaining", "left", "reste")
# Mots entiers uniquement : « périodicité » (fréquence des remboursements) ne doit pas passer pour une durée.
_DURATION_WORDS = ("term", "terms", "duree", "durees", "duration", "period", "periode", "terme")
_MATURITY_LABELS = ("maturity", "echeance", "date de fin", "end date", "final payment", "due date", "fin du pret",
                    "date de remboursement", "repayment date", "last payment", "date d echeance", "maturite")
_START_LABELS = ("issue date", "issued", "date d emission", "start date", "date de debut", "invested on",
                 "investment date", "date d investissement", "date de l investissement", "purchase date",
                 "date d achat", "date de souscription", "subscription date", "date du pret", "loan date",
                 "date de financement", "funding date")


def classify_headers(headers: list[str]) -> dict[str, int]:
    """Indices des colonnes 'term', 'remaining', 'maturity', 'start' reconnues par leur intitulé."""
    columns: dict[str, int] = {}
    for index, header in enumerate(headers):
        label = _norm(header)
        if not label:
            continue
        words = label.split()
        has_duration_word = any(w in _DURATION_WORDS for w in words)
        if any(lbl in label for lbl in _MATURITY_LABELS):
            columns.setdefault("maturity", index)
        elif any(lbl in label for lbl in _START_LABELS):
            columns.setdefault("start", index)
        elif has_duration_word and any(w in words for w in _REMAINING_WORDS):
            columns.setdefault("remaining", index)
        elif has_duration_word or label in ("days left", "jours restants", "mois restants", "months left"):
            if any(w in words for w in _REMAINING_WORDS):
                columns.setdefault("remaining", index)
            else:
                columns.setdefault("term", index)
    return columns


def _header_unit_factor(header: str) -> float | None:
    m = re.search(r"\(([^)]*)\)", str(header or ""))
    return _unit_factor(m.group(1)) if m else None


def durations_from_table(headers: list[str], rows: Iterable[tuple[list[str], float]],
                         today: date | None = None) -> DurationExtraction:
    """`rows` : (textes des cellules d'une ligne prêt, encours en EUR). Si la ligne a plus de cellules que
    d'intitulés, les cellules en trop sont supposées en tête (ex. case à cocher sans intitulé)."""
    today = today or date.today()
    headers = [str(h or "") for h in headers]
    columns = classify_headers(headers)
    loans: list[LoanDuration] = []
    used: dict[str, int] = {}
    samples: dict[str, str] | None = None

    for cells, amount in rows:
        if not amount or amount <= 0:
            continue
        offset = max(0, len(cells) - len(headers))
        if samples is None:
            samples = _samples_from_pairs(
                (header, cells[i + offset]) for i, header in enumerate(headers) if header and i + offset < len(cells))

        def cell(kind):
            index = columns.get(kind)
            if index is None or index + offset >= len(cells):
                return None
            return cells[index + offset]

        term = _valid_months(parse_duration_text(cell("term"), _header_unit_factor(headers[columns["term"]])
                                                 if "term" in columns else None))
        remaining = _valid_months(parse_duration_text(cell("remaining"), _header_unit_factor(headers[columns["remaining"]])
                                                      if "remaining" in columns else None), allow_zero=True)
        maturity = parse_date(cell("maturity"))
        start = parse_date(cell("start"))
        source_parts = []
        if term is not None:
            source_parts.append(f"durée=« {headers[columns['term']]} »")
        elif maturity and start:
            term = _valid_months(months_between(start, maturity))
            if term is not None:
                source_parts.append(f"durée=« {headers[columns['start']]} » → « {headers[columns['maturity']]} »")
        if remaining is not None:
            source_parts.append(f"restant=« {headers[columns['remaining']]} »")
        elif maturity:
            remaining = _valid_months(max(0.0, months_between(today, maturity)), allow_zero=True)
            if remaining is not None:
                source_parts.append(f"restant=« {headers[columns['maturity']]} »")
        loans.append(LoanDuration(amount=float(amount), term_months=term, remaining_months=remaining))
        if source_parts:
            key = ", ".join(source_parts)
            used[key] = used.get(key, 0) + 1

    return DurationExtraction(loans=loans, source=_describe_sources(used), available_fields=[h for h in headers if h],
                              samples=samples or {})


def durations_from_labelled_pages(pages: Iterable[tuple[dict[str, str], float]],
                                  today: date | None = None) -> DurationExtraction:
    """`pages` : ({intitulé: valeur} d'une fiche projet, encours). Intitulés reconnus fiche par fiche
    (deux fiches peuvent écrire « Durée » et « Durée du prêt »)."""
    loans: list[LoanDuration] = []
    sources: dict[str, int] = {}
    fields: list[str] = []
    samples: dict[str, str] | None = None
    for labels, amount in pages:
        headers = list(labels)
        for header in headers:
            if header not in fields:
                fields.append(header)
        if samples is None and labels:
            samples = _samples_from_pairs(labels.items())
        result = durations_from_table(headers, [([labels[h] for h in headers], amount)], today)
        loans.extend(result.loans)
        if result.source:
            key = re.sub(r" \(\d+ prêts?\)$", "", result.source)
            sources[key] = sources.get(key, 0) + 1
    return DurationExtraction(loans=loans, source=_describe_sources(sources),
                              available_fields=fields[:MAX_FIELD_NAMES_LOGGED], samples=samples or {})


def _describe_sources(sources: dict[str, int]) -> str:
    return " ; ".join(f"{source} ({count} prêt{'s' if count > 1 else ''})"
                      for source, count in sorted(sources.items(), key=lambda kv: -kv[1]))


# --------------------------------------------------------------------------
# Statistiques et écriture dans le Sheet
# --------------------------------------------------------------------------

def compute_stats(loans: list[LoanDuration]) -> dict:
    with_term = [l for l in loans if l.term_months is not None]
    with_remaining = [l for l in loans if l.remaining_months is not None]
    # Tous les prêts « échus » à la fois : la date lue n'est sûrement pas la fin du prêt (ex. fin de collecte).
    # Exception : épargne à vue, dont la durée initiale vaut elle aussi 0.
    a_vue = bool(with_term) and all(l.term_months == 0 for l in with_term)
    if with_remaining and not a_vue and all(l.remaining_months == 0 for l in with_remaining):
        logger.warning("Durées : durée restante nulle pour tous les prêts - ignorée (date de fin non fiable).")
        with_remaining = []
    total = sum(l.amount for l in loans)

    def weighted(subset, attr):
        weight = sum(l.amount for l in subset)
        return round(sum(getattr(l, attr) * l.amount for l in subset) / weight, 1) if weight > 0 else None

    return {
        "min": round(min(l.term_months for l in with_term), 1) if with_term else None,
        "moy": weighted(with_term, "term_months"),
        "max": round(max(l.term_months for l in with_term), 1) if with_term else None,
        "restant": weighted(with_remaining, "remaining_months"),
        "nb": len(loans),
        "nb_term": len(with_term),
        "nb_remaining": len(with_remaining),
        "couverture": round(100 * sum(l.amount for l in with_term) / total) if total > 0 else 0,
    }


def _platform_key(name) -> str:
    """Même rapprochement que normalizePlatformLabel() côté Apps Script (+ insensible aux accents)."""
    text = re.sub(r"\s*\(.*?\)\s*$", "", " ".join(str(name or "").replace("*", "").split()))
    return _norm(text)


def _cell(value):
    return "" if value is None else value


def write_platform_durations(platform: str, section: str, stats: dict, source: str, now: datetime | None = None) -> None:
    """Crée ou met à jour la ligne de `platform` (colonnes min/moy/max, restant, nb, source, date).
    Le commentaire saisi à la main est conservé."""
    import gspread
    from gspread.utils import rowcol_to_a1
    from shared.google_sheet import SPREADSHEET_ID, _call_with_retry, get_google_credentials

    client = gspread.authorize(get_google_credentials())
    spreadsheet = _call_with_retry(client.open_by_key, SPREADSHEET_ID)
    target = DURATIONS_SHEET_NAME.strip().casefold()
    worksheet = next(
        (ws for ws in _call_with_retry(spreadsheet.worksheets) if ws.title.strip().casefold() == target), None,
    )
    if worksheet is None:
        logger.info("Onglet '%s' absent : création.", DURATIONS_SHEET_NAME)
        worksheet = _call_with_retry(spreadsheet.add_worksheet, title=DURATIONS_SHEET_NAME, rows=100,
                                     cols=len(DURATIONS_HEADERS))

    grid = _call_with_retry(worksheet.get_all_values)
    header_row = grid[0] if grid else []
    if [h.strip() for h in header_row[:len(DURATIONS_HEADERS)]] != DURATIONS_HEADERS:
        # Onglet créé par la Web App (7 colonnes) ou vide : on complète les intitulés sans toucher aux données.
        _call_with_retry(worksheet.update, range_name="A1", values=[DURATIONS_HEADERS], value_input_option="RAW")

    target_key = _platform_key(platform)
    row_number = None
    for index, row in enumerate(grid[1:], start=2):
        if row and _platform_key(row[0]) == target_key:
            row_number = index
            break
    existing = grid[row_number - 1] if row_number else []
    if row_number is None:
        row_number = max(len(grid), 1) + 1

    def existing_value(col):
        return existing[col] if len(existing) > col else ""

    now = now or datetime.now()
    values = [
        platform,
        existing_value(1) or section,
        _cell(stats["min"]),
        _cell(stats["moy"]),
        _cell(stats["max"]),
        existing_value(5),
        now.strftime("%d/%m/%Y %H:%M"),
        _cell(stats["restant"]),
        stats["nb"],
        f"{AUTO_SOURCE_PREFIX} : {source}"[:500],
    ]
    if row_number > worksheet.row_count:
        _call_with_retry(worksheet.add_rows, row_number - worksheet.row_count)
    address = f"{rowcol_to_a1(row_number, 1)}:{rowcol_to_a1(row_number, len(values))}"
    _call_with_retry(worksheet.update, range_name=address, values=[values], value_input_option="RAW")
    logger.info("Durées %s écrites dans '%s'!%s : %s", platform, DURATIONS_SHEET_NAME, address, values[2:5] + values[7:9])


def record_platform_loan_durations(platform: str, section: str, extract: Callable[[], DurationExtraction]) -> dict | None:
    """Extrait puis écrit les durées d'une plateforme. N'interrompt JAMAIS le rapport appelant :
    toute erreur est journalisée et la fonction renvoie None."""
    try:
        extraction = extract()
        loans = extraction.loans
        if not loans:
            logger.info("Durées %s : aucun prêt en cours, rien à écrire.", platform)
            return None
        stats = compute_stats(loans)
        if stats["nb_term"] == 0 and stats["nb_remaining"] == 0:
            logger.warning(
                "Durées %s : aucun champ de durée reconnu sur %d prêt(s) - onglet '%s' non modifié. "
                "Champs disponibles : %s | Exemples (champs date/durée) : %s",
                platform, len(loans), DURATIONS_SHEET_NAME, ", ".join(extraction.available_fields or []) or "(aucun)",
                ", ".join(f"{k}={v}" for k, v in (extraction.samples or {}).items()) or "(aucun)",
            )
            return None
        source = f"{extraction.source} ; durée connue pour {stats['nb_term']}/{stats['nb']} prêts ({stats['couverture']} % de l'encours)"
        logger.info(
            "Durées %s : min=%s moy=%s max=%s mois, restant moyen=%s mois (%s).",
            platform, stats["min"], stats["moy"], stats["max"], stats["restant"], source,
        )
        write_platform_durations(platform, section, stats, source)
        return stats
    except Exception:
        logger.exception("Durées %s : échec de la récupération/écriture (rapport non interrompu).", platform)
        return None


def record_constant_duration(platform: str, section: str, months: float, amount: float, reason: str) -> dict | None:
    """Plateformes sans prêts individuels (épargne à vue) : durée fixe connue."""
    return record_platform_loan_durations(
        platform, section,
        lambda: DurationExtraction(
            loans=[LoanDuration(amount=amount or 1.0, term_months=months, remaining_months=months)] if months >= 0 else [],
            source=reason,
        ),
    )
