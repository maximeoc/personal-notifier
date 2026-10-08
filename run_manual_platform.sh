#!/usr/bin/env bash
# Runs the LOCAL-ONLY manual-login helpers for Mintos and Lande, one after
# the other. Both open a real browser window and may need you to solve a
# CAPTCHA/Turnstile challenge by hand - just follow the on-screen prompts.
# Each platform still runs even if the other one fails.
#
# By default (nothing passed, or everything left blank at the prompt),
# fetches the CURRENT month. To backfill a single past/future month, or a
# range of several months (mirrors the "Diversification Reports" GitHub
# workflow's start_month/end_month inputs), pass START_MONTH [END_MONTH]
# (MM/AAAA, inclusive range) - Mintos and Lande each log in/open the
# browser only ONCE, then reuse that same session for every month in the
# range (REPORT_DATE set to the LAST day of each month in turn). Passing
# just one of the two treats it as a single month.
#
# Usage: ./run_manual_platform.sh
#        ./run_manual_platform.sh 06/2026
#        ./run_manual_platform.sh 01/2026 06/2026

cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1

if [ -x ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
elif [ -x ".venv/Scripts/python.exe" ]; then
    PYTHON=".venv/Scripts/python.exe"
else
    echo "Environnement virtuel .venv introuvable." >&2
    exit 1
fi

START_MONTH="${1:-}"
END_MONTH="${2:-}"

if [ -z "$START_MONTH" ] && [ -z "$END_MONTH" ]; then
    read -r -p "Mois de début (MM/AAAA), ou laissez vide pour le mois en cours: " START_MONTH
    if [ -n "$START_MONTH" ]; then
        read -r -p "Mois de fin (MM/AAAA, inclus), ou laissez vide pour ne traiter que le mois de début: " END_MONTH
    fi
fi

# A single month passed either way is a valid 1-month range.
[ -n "$START_MONTH" ] && [ -z "$END_MONTH" ] && END_MONTH="$START_MONTH"
[ -n "$END_MONTH" ] && [ -z "$START_MONTH" ] && START_MONTH="$END_MONTH"

CYAN=$'\033[36m'
YELLOW=$'\033[33m'
RED=$'\033[31m'
RESET=$'\033[0m'

mintos_exit=0
lande_exit=0

invoke_platforms() {
    echo "${CYAN}=== Mintos ===${RESET}"
    "$PYTHON" -m diversification.mintos_get_session
    mintos_exit=$?

    echo
    echo "${CYAN}=== Lande ===${RESET}"
    "$PYTHON" -m diversification.lande_get_session
    lande_exit=$?
}

print_summary() {
    echo
    echo "${CYAN}=== Summary ===${RESET}"
    if [ "$mintos_exit" -eq 0 ]; then echo "Mintos: OK"; else echo "Mintos: FAILED (exit $mintos_exit)"; fi
    if [ "$lande_exit" -eq 0 ]; then echo "Lande:  OK"; else echo "Lande:  FAILED (exit $lande_exit)"; fi
}

if [ -z "$START_MONTH" ] && [ -z "$END_MONTH" ]; then
    unset REPORT_DATE
    invoke_platforms
    print_summary
    exit 0
fi

month_pattern='^(0[1-9]|1[0-2])/[0-9]{4}$'
if ! [[ "$START_MONTH" =~ $month_pattern ]]; then
    echo "${RED}StartMonth '$START_MONTH' n'est pas au format MM/AAAA.${RESET}" >&2
    exit 1
fi
if ! [[ "$END_MONTH" =~ $month_pattern ]]; then
    echo "${RED}EndMonth '$END_MONTH' n'est pas au format MM/AAAA.${RESET}" >&2
    exit 1
fi

cur_month=$((10#${START_MONTH%/*}))
cur_year=$((10#${START_MONTH#*/}))
end_month=$((10#${END_MONTH%/*}))
end_year=$((10#${END_MONTH#*/}))

if [ $((cur_year * 12 + cur_month)) -gt $((end_year * 12 + end_month)) ]; then
    echo "${RED}StartMonth ($START_MONTH) est après EndMonth ($END_MONTH).${RESET}" >&2
    exit 1
fi

last_day_of_month() {
    local month=$1 year=$2
    case $month in
        1|3|5|7|8|10|12) echo 31 ;;
        4|6|9|11) echo 30 ;;
        2)
            if [ $((year % 400)) -eq 0 ] || { [ $((year % 4)) -eq 0 ] && [ $((year % 100)) -ne 0 ]; }; then
                echo 29
            else
                echo 28
            fi
            ;;
    esac
}

report_dates=()
while [ $((cur_year * 12 + cur_month)) -le $((end_year * 12 + end_month)) ]; do
    day=$(last_day_of_month "$cur_month" "$cur_year")
    report_dates+=("$(printf '%02d/%02d/%04d' "$day" "$cur_month" "$cur_year")")
    cur_month=$((cur_month + 1))
    if [ "$cur_month" -gt 12 ]; then
        cur_month=1
        cur_year=$((cur_year + 1))
    fi
done

# One login/browser session, reused for every month (see mintos_get_session.py/
# lande_get_session.py's REPORT_DATE_MONTHS handling) instead of relaunching
# the browser and logging in again for each month.
REPORT_DATE_MONTHS=$(IFS=,; echo "${report_dates[*]}")
export REPORT_DATE_MONTHS
echo
echo "${YELLOW}=== ${#report_dates[@]} mois ($START_MONTH -> $END_MONTH), une seule connexion réutilisée ===${RESET}"
invoke_platforms

unset REPORT_DATE_MONTHS REPORT_DATE

print_summary
exit 0
