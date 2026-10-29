"""Crée les onglets « durée » et « taux » du Google Sheet s'ils n'existent pas encore.

Les *_diversification.py remplissent ces onglets (shared.google_sheet.fill_remaining_duration_amounts,
shared.rate_sheet.fill_rate_amounts) mais ne les créent pas : ils cherchent, en colonne A, une ligne par secteur
(« Crowdlending », « Crowdlending savings »...) suivie des lignes de ses plateformes. Ce script pose ce squelette,
dans l'ordre de l'onglet « Répartition géographique » ; tout le reste (colonnes, en-têtes, mois, sociétés de
prêt) est ajouté par les scripts à leur prochain passage. Un onglet déjà présent n'est jamais modifié.

    python -m scripts.create_duration_rate_sheets            # affiche le squelette, n'écrit rien
    python -m scripts.create_duration_rate_sheets --apply    # crée les onglets manquants
"""

import sys

import gspread
from dotenv import load_dotenv

load_dotenv()

from shared.google_sheet import (  # noqa: E402
    DURATION_SHEET_NAME, GEO_SECTION_BOUNDARY_LABELS, SPREADSHEET_ID, _DURATION_SECTION_LABELS, _call_with_retry,
    get_google_credentials,
)
from shared.rate_sheet import RATE_SHEET_NAME  # noqa: E402

GEO_SHEET_NAME = "Répartition géographique"


def skeleton_from_geo(grid: list) -> list:
    """Secteurs et plateformes de l'onglet géographique, dans son ordre (premier texte non vide de chaque ligne)."""
    sections = {label: label for label in _DURATION_SECTION_LABELS}
    platforms = {label.casefold(): label for label in GEO_SECTION_BOUNDARY_LABELS}
    rows, seen = [], set()
    for row in grid:
        text = next((value.strip() for value in row if value.strip()), "")
        key = text.casefold()
        if key in seen:
            continue
        if key in sections:
            label = next(s for s in ("Crowdlending", "Crowdlending savings", "Crowdlending agricole", "Crowdfunding immobilier") if s.casefold() == key)
        elif key in platforms and key not in sections and key != "bourse":
            label = platforms[key]
        else:
            continue
        seen.add(key)
        rows.append(label)
    if rows and rows[0].casefold() not in sections:
        rows.insert(0, "Crowdlending")
    return rows


def main() -> None:
    apply = "--apply" in sys.argv
    spreadsheet = _call_with_retry(gspread.authorize(get_google_credentials()).open_by_key, SPREADSHEET_ID)
    titles = {ws.title.strip().casefold(): ws for ws in _call_with_retry(spreadsheet.worksheets)}
    geo = titles.get(GEO_SHEET_NAME.casefold())
    if geo is None:
        raise SystemExit(f"Onglet '{GEO_SHEET_NAME}' introuvable : impossible de déduire les plateformes.")
    rows = skeleton_from_geo(_call_with_retry(geo.get_all_values))
    print("Squelette (colonne A) :")
    for label in rows:
        print(("  " if label.casefold() not in _DURATION_SECTION_LABELS else "") + label)

    for name in (DURATION_SHEET_NAME, RATE_SHEET_NAME):
        if name.casefold() in titles:
            print(f"Onglet '{name}' déjà présent - inchangé.")
            continue
        if not apply:
            print(f"Onglet '{name}' absent - serait créé (relancer avec --apply).")
            continue
        worksheet = _call_with_retry(spreadsheet.add_worksheet, title=name, rows=max(len(rows), 100), cols=26)
        _call_with_retry(worksheet.update, [[label] for label in rows], "A1", value_input_option="RAW")
        print(f"Onglet '{name}' créé ({len(rows)} lignes).")


if __name__ == "__main__":
    main()
