"""
mark_job.py — Marque manuellement le statut d'une offre dans tracking.csv.

L'offre doit déjà exister dans tracking.csv (après un run matching).
Les prochaines collectes ignoreront automatiquement les offres marquées.

Usage :
    python app/src/jobs/mark_job.py --url "https://..." --status applied
    python app/src/jobs/mark_job.py --id "wttj-devops-engineer_paris" --status ignored

Statuts acceptés :
    applied             — j'ai postulé
    ignored             — je passe, ne plus proposer
    rejected_by_company — j'ai été refusé par l'entreprise
    manually_rejected   — je rejette manuellement (poste non adapté)
"""

import argparse
import csv
import os
import sys
from datetime import datetime

TRACKING_PATH = "app/data/tracking.csv"
HEADERS = [
    "job_id", "platform", "company", "title", "location",
    "url", "score", "status", "applied_at", "notes",
]
ALLOWED_STATUSES = {"applied", "ignored", "rejected_by_company", "manually_rejected"}


def load_tracking():
    if not os.path.exists(TRACKING_PATH):
        print(f"Fichier introuvable : {TRACKING_PATH}")
        sys.exit(1)
    with open(TRACKING_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return rows


def save_tracking(rows):
    with open(TRACKING_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def find_row(rows, url=None, job_id=None):
    for i, row in enumerate(rows):
        if url and row.get("url", "").strip() == url.strip():
            return i, row
        if job_id and row.get("job_id", "").strip() == job_id.strip():
            return i, row
    return None, None


def main():
    parser = argparse.ArgumentParser(
        description="Marque manuellement le statut d'une offre dans tracking.csv."
    )
    parser.add_argument("--url",    help="URL complète de l'offre")
    parser.add_argument("--id",     dest="job_id", help="job_id de l'offre (ex: wttj-devops-engineer_paris)")
    parser.add_argument("--status", required=True, choices=sorted(ALLOWED_STATUSES),
                        help="Statut à appliquer")
    args = parser.parse_args()

    if not args.url and not args.job_id:
        print("Erreur : fournir --url ou --id.")
        print("Exemple : python app/src/jobs/mark_job.py --id 'wttj-devops-engineer_paris' --status applied")
        sys.exit(1)

    rows = load_tracking()
    idx, row = find_row(rows, url=args.url, job_id=args.job_id)

    if row is None:
        lookup = f"url={args.url!r}" if args.url else f"id={args.job_id!r}"
        print()
        print(f"  Offre introuvable dans tracking.csv ({lookup})")
        print()
        print("  Causes possibles :")
        print("    - L'offre n'a pas encore été traitée par le matching")
        print("    - L'id ou l'url fourni contient une coquille")
        print()
        print("  Solution :")
        print("    python run.py --from-step=3   # relancer le matching")
        print("    puis relancer mark_job.py")
        sys.exit(1)

    old_status = row.get("status", "")
    now        = datetime.now().strftime("%Y-%m-%d %H:%M")

    print()
    print("=" * 60)
    print("  Offre trouvée")
    print("=" * 60)
    print(f"  id      : {row.get('job_id', '')}")
    print(f"  titre   : {(row.get('title') or '(vide)')[:70]}")
    print(f"  société : {row.get('company') or '(vide)'}")
    print(f"  url     : {row.get('url', '')}")
    print(f"  statut  : {old_status!r}  ->  {args.status!r}")
    print(f"  horodatage : {now}")
    print("=" * 60)

    rows[idx]["status"]     = args.status
    rows[idx]["applied_at"] = now

    save_tracking(rows)

    print()
    print(f"  tracking.csv mis à jour ({TRACKING_PATH})")
    print(f"  Cette offre sera exclue des prochaines collectes.")
    print()


if __name__ == "__main__":
    main()
