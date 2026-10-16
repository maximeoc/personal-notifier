"""Contrôle à blanc des durées Bricks : récupère les vrais projets, calcule les durées, AFFICHE le résultat.
N'écrit rien dans le Google Sheet.

    python -m scripts.check_bricks_durations
"""

import requests
from dotenv import load_dotenv

load_dotenv()

from diversification import bricks_diversification as bricks  # noqa: E402
from shared.loan_durations import compute_stats, durations_from_json_items  # noqa: E402


def main() -> None:
    if not bricks.BRICKS_EMAIL or not bricks.BRICKS_PASSWORD:
        raise SystemExit("BRICKS_EMAIL et BRICKS_PASSWORD requis (.env).")
    session = requests.Session()
    session.headers.update({"Origin": "https://app.bricks.co", "Referer": "https://app.bricks.co/"})
    bricks.login(session)

    resp = session.get(bricks.PORTFOLIO_PROPERTIES_URL, timeout=15)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    ongoing = (resp.json() or {}).get("ongoing") or []

    items = []
    for project in ongoing:
        amount = round(float(project.get("brickCount") or 0) * float(project.get("brickPrice") or 0) / 100, 2)
        if amount > 0:
            items.append((project, amount))

    extraction = durations_from_json_items(items)
    print(f"{'Projet':45} {'Statut':18} {'Encours':>9} {'Horizon (mois)':>15} {'Restant (mois)':>15}")
    for (project, amount), loan in zip(items, extraction.loans):
        print(f"{(project.get('propertyName') or '')[:45]:45} {str(project.get('financialStatus')):18} {amount:9.2f} "
              f"{str(loan.term_months):>15} {str(loan.remaining_months):>15}")
    print()
    print("Source :", extraction.source)
    print("Stats  :", compute_stats(extraction.loans))


if __name__ == "__main__":
    main()
