"""
cleanup_jobs.py — Nettoie jobs_raw.json, tracking.csv et les fichiers dérivés.

Conserve uniquement les offres avec un statut manuel protégé :
  applied · ignored · manually_rejected · already_applied · rejected_by_company

Les statuts automatiques (selected, review, rejected, skipped, etc.)
ne sont PAS protégés et peuvent être supprimés.
Aucun statut n'est jamais modifié : on supprime des lignes, on ne reclassifie pas.

Usage:
    python app/src/jobs/cleanup_jobs.py          # dry-run (affiche seulement)
    python app/src/jobs/cleanup_jobs.py --apply  # applique + crée sauvegardes
"""

import argparse
import csv
import json
import os
import shutil
from datetime import datetime

JOBS_RAW_PATH      = "app/data/jobs/jobs_raw.json"
TRACKING_PATH      = "app/data/tracking.csv"
SELECTED_JOBS_PATH = "app/data/jobs/selected_jobs.json"
SELECTED_URLS_PATH = "app/data/jobs/selected_urls.txt"
BACKUP_DIR         = "app/data/backups"

MANUAL_STATUSES = {
    "applied", "ignored", "manually_rejected",
    "already_applied", "rejected_by_company",
}

PLATFORM_LABELS = {
    "wttj":      "WTTJ",
    "hellowork": "HelloWork",
    "linkedin":  "LinkedIn",
    "indeed":    "Indeed",
}

TRACKING_HEADERS = [
    "job_id", "platform", "company", "title", "location",
    "url", "score", "status", "applied_at", "notes",
]


def _load_manual_urls():
    """Return set of URLs with a protected manual status in tracking.csv."""
    manual_urls = set()
    if not os.path.exists(TRACKING_PATH):
        return manual_urls
    with open(TRACKING_PATH, "r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("status", "").strip().lower() in MANUAL_STATUSES:
                url = row.get("url", "").strip()
                if url:
                    manual_urls.add(url)
    return manual_urls


def _count_by_platform(jobs):
    counts = {}
    for job in jobs:
        p = job.get("platform", "unknown")
        counts[p] = counts.get(p, 0) + 1
    return counts


def _count_by_status(rows):
    counts = {}
    for row in rows:
        s = (row.get("status", "") or "unknown").strip().lower()
        counts[s] = counts.get(s, 0) + 1
    return counts


def run_analysis():
    """Return analysis dict without modifying any file.

    Returns:
        {
            "manual_urls": set of URLs with protected manual status,
            "jobs_raw":  {"total": N, "to_keep": [...], "to_remove": [...]},
            "tracking":  {"total": N, "to_keep": [...], "to_remove": [...]},
        }
    """
    manual_urls = _load_manual_urls()

    jr = {"total": 0, "to_keep": [], "to_remove": []}
    if os.path.exists(JOBS_RAW_PATH):
        with open(JOBS_RAW_PATH, "r", encoding="utf-8") as f:
            all_jobs = json.load(f)
        jr["total"]     = len(all_jobs)
        jr["to_keep"]   = [j for j in all_jobs if j.get("url", "").strip() in manual_urls]
        jr["to_remove"] = [j for j in all_jobs if j.get("url", "").strip() not in manual_urls]

    tr = {"total": 0, "to_keep": [], "to_remove": []}
    if os.path.exists(TRACKING_PATH):
        with open(TRACKING_PATH, "r", newline="", encoding="utf-8") as f:
            all_rows = list(csv.DictReader(f))
        tr["total"]     = len(all_rows)
        tr["to_keep"]   = [
            r for r in all_rows
            if r.get("status", "").strip().lower() in MANUAL_STATUSES
        ]
        tr["to_remove"] = [
            r for r in all_rows
            if r.get("status", "").strip().lower() not in MANUAL_STATUSES
        ]

    return {"manual_urls": manual_urls, "jobs_raw": jr, "tracking": tr}


def apply_cleanup(analysis):
    """Apply cleanup described by analysis. Returns list of backup paths created.

    Actions:
    - Backup + clean jobs_raw.json (remove non-manual entries)
    - Backup + clean tracking.csv (remove non-manual rows)
    - Clean selected_jobs.json (keep only manual-URL jobs)
    - Clear selected_urls.txt (regenerated on next run)

    No status is ever modified — only rows are deleted.
    """
    jr          = analysis["jobs_raw"]
    tr          = analysis["tracking"]
    manual_urls = analysis["manual_urls"]
    backups     = []

    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # jobs_raw.json
    if jr["to_remove"] and os.path.exists(JOBS_RAW_PATH):
        backup = os.path.join(BACKUP_DIR, f"jobs_raw_{ts}.json")
        shutil.copy2(JOBS_RAW_PATH, backup)
        backups.append(backup)
        with open(JOBS_RAW_PATH, "w", encoding="utf-8") as f:
            json.dump(jr["to_keep"], f, ensure_ascii=False, indent=2)

    # tracking.csv
    if tr["to_remove"] and os.path.exists(TRACKING_PATH):
        backup = os.path.join(BACKUP_DIR, f"tracking_{ts}.csv")
        shutil.copy2(TRACKING_PATH, backup)
        backups.append(backup)
        with open(TRACKING_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=TRACKING_HEADERS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(tr["to_keep"])

    # selected_jobs.json — keep only jobs with a protected manual URL
    if os.path.exists(SELECTED_JOBS_PATH):
        try:
            with open(SELECTED_JOBS_PATH, "r", encoding="utf-8") as f:
                sel_jobs = json.load(f)
            sel_kept = [j for j in sel_jobs if j.get("url", "").strip() in manual_urls]
        except (FileNotFoundError, json.JSONDecodeError):
            sel_kept = []
        with open(SELECTED_JOBS_PATH, "w", encoding="utf-8") as f:
            json.dump(sel_kept, f, ensure_ascii=False, indent=2)

    # selected_urls.txt — clear (will be regenerated on next run)
    with open(SELECTED_URLS_PATH, "w", encoding="utf-8"):
        pass

    return backups


def _print_platform_breakdown(label, jobs):
    if not jobs:
        return
    counts = _count_by_platform(jobs)
    print(f"\n  Détail par plateforme ({label}) :")
    for p, lbl in PLATFORM_LABELS.items():
        n = counts.get(p, 0)
        if n:
            print(f"    {lbl:<12} : {n}")
    for p, n in sorted(counts.items()):
        if p not in PLATFORM_LABELS:
            print(f"    {p:<12} : {n}")


def _print_status_breakdown(label, rows):
    if not rows:
        return
    counts = _count_by_status(rows)
    print(f"\n  Détail par statut ({label}) :")
    for s, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"    {s:<30} : {n}")


def print_report(analysis):
    jr = analysis["jobs_raw"]
    tr = analysis["tracking"]
    print()
    print(f"jobs_raw.json ({jr['total']} offre(s))")
    print(f"  Conservées   : {len(jr['to_keep'])}  (statut manuel protégé)")
    print(f"  Supprimables : {len(jr['to_remove'])}")
    _print_platform_breakdown("supprimables", jr["to_remove"])
    print()
    print(f"tracking.csv  ({tr['total']} ligne(s))")
    print(f"  Conservées   : {len(tr['to_keep'])}  (statut manuel protégé)")
    print(f"  Supprimables : {len(tr['to_remove'])}")
    _print_status_breakdown("supprimables", tr["to_remove"])
    print()


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Nettoie jobs_raw.json et tracking.csv.\n"
            "Conserve uniquement les offres avec statut manuel protégé :\n"
            "  applied · ignored · manually_rejected · already_applied · rejected_by_company\n"
            "Aucun statut n'est modifié — seules des lignes sont supprimées."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Applique le nettoyage et crée des sauvegardes horodatées.",
    )
    args = parser.parse_args()

    analysis = run_analysis()
    jr = analysis["jobs_raw"]
    tr = analysis["tracking"]

    if not os.path.exists(JOBS_RAW_PATH) and not os.path.exists(TRACKING_PATH):
        print("Aucun fichier trouvé.")
        return

    print_report(analysis)

    if not args.apply:
        print("  Mode dry-run — aucune modification effectuée.")
        print("  Relancer avec --apply pour appliquer le nettoyage.")
        return

    if not jr["to_remove"] and not tr["to_remove"]:
        print("  Rien à supprimer.")
        return

    backups = apply_cleanup(analysis)

    print()
    for b in backups:
        print(f"  Sauvegarde créée      : {b}")
    if jr["to_remove"]:
        print(f"  jobs_raw.json         : {len(jr['to_remove'])} supprimée(s), {len(jr['to_keep'])} conservée(s).")
    if tr["to_remove"]:
        print(f"  tracking.csv          : {len(tr['to_remove'])} ligne(s) supprimée(s), {len(tr['to_keep'])} conservée(s).")
    print(f"  selected_jobs.json    : vidé (offres non traitées supprimées).")
    print(f"  selected_urls.txt     : vidé (sera régénéré au prochain run).")


if __name__ == "__main__":
    main()
