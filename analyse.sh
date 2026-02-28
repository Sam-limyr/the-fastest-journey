#!/usr/bin/env bash
# analyse.sh — Singapore MRT commute analysis
#
# Computes commute scores for MRT stations and opens an interactive HTML map.
#
# Usage:
#   ./analyse.sh [--display <mode>] [--scope <scope>]
#   ./analyse.sh --help
#
# Options:
#   --display  minutes   Show raw expected commute time (colour-coded but no 1-10 scale)
#              linear    1-10 score: z-score of raw minutes
#              log       1-10 score: z-score of log(minutes)  [default]
#
#   --scope    hdb         HDB estate stations only
#              residential All residential MRT stations        [default]
#              all         Every station in the travel-time dataset
#
# Examples:
#   ./analyse.sh
#   ./analyse.sh --display log --scope residential
#   ./analyse.sh --display minutes --scope all
#   ./analyse.sh --display linear --scope hdb

set -euo pipefail

DISPLAY_MODE="log"
SCOPE="residential"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_HTML="${SCRIPT_DIR}/mrt_commute_scores.html"

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

usage() {
    cat <<EOF
Usage: $(basename "$0") [--display <mode>] [--scope <scope>]

Generate an interactive HTML map of Singapore MRT commute scores.

Options:
  --display   How to present commute performance on the map:
                minutes     Colour by raw expected commute time (no 1-10 scale)
                linear      1-10 score using z-scores of raw minutes
                log         1-10 score using z-scores of log(minutes)  [default]

  --scope     Which stations to include:
                hdb         HDB estate stations only
                residential All residential MRT stations  [default]
                all         Every station in the travel-time dataset

  -h, --help  Show this help message and exit

Examples:
  $(basename "$0")
  $(basename "$0") --display log --scope residential
  $(basename "$0") --display minutes --scope all
  $(basename "$0") --display linear --scope hdb

All 9 combinations of --display and --scope are valid.
After analysis, the HTML map is opened automatically.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --display)
            if [[ -z "${2:-}" ]]; then
                echo "Error: --display requires a value (minutes|linear|log)" >&2
                exit 1
            fi
            case "$2" in
                minutes|linear|log) DISPLAY_MODE="$2" ;;
                *)
                    echo "Error: --display must be one of: minutes, linear, log" >&2
                    exit 1
                    ;;
            esac
            shift 2
            ;;
        --scope)
            if [[ -z "${2:-}" ]]; then
                echo "Error: --scope requires a value (hdb|residential|all)" >&2
                exit 1
            fi
            case "$2" in
                hdb|residential|all) SCOPE="$2" ;;
                *)
                    echo "Error: --scope must be one of: hdb, residential, all" >&2
                    exit 1
                    ;;
            esac
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Error: Unknown option: $1" >&2
            echo "Run '$(basename "$0") --help' for usage." >&2
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Run analysis
# ---------------------------------------------------------------------------

echo "==> display=${DISPLAY_MODE}  scope=${SCOPE}"
echo ""

cd "${SCRIPT_DIR}"
python visualize_scores.py --display "${DISPLAY_MODE}" --scope "${SCOPE}"

# ---------------------------------------------------------------------------
# Open the HTML map
# ---------------------------------------------------------------------------

if [[ ! -f "${OUTPUT_HTML}" ]]; then
    echo "Error: Expected output file not found: ${OUTPUT_HTML}" >&2
    exit 1
fi

echo ""
echo "Opening map: ${OUTPUT_HTML}"

if command -v xdg-open &>/dev/null; then
    xdg-open "${OUTPUT_HTML}"
elif command -v open &>/dev/null; then
    open "${OUTPUT_HTML}"
elif command -v start &>/dev/null; then
    start "${OUTPUT_HTML}"
else
    # Windows fallback via PowerShell (works in Git Bash / WSL)
    powershell.exe -NoProfile -Command "Start-Process '${OUTPUT_HTML}'"
fi
