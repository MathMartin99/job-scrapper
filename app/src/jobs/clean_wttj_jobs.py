"""
clean_wttj_jobs.py — Supprime les entrées WTTJ de jobs_raw.json.

Crée un backup avant toute modification.
Les entrées Indeed sont conservées intactes.

Usage :
    python app/src/jobs/clean_wttj_jobs.py
"""

import json
import os
import shutil
import sys

JOBS_PATH  = "app/data/jobs/jobs_raw.json"
BACKUP_PATH = "app/data/jobs/jobs_raw.backup_before_wttj_cleanup.json"


def main():
    # Vérification que le fichier source existe
    if not os.path.exists(JOBS_PATH):
        print(f"Fichier introuvable : {JOBS_PATH}")
        sys.exit(1)

    with open(JOBS_PATH, "r", encoding="utf-8") as f:
        jobs = json.load(f)

    total_before = len(jobs)
    wttj_jobs    = [j for j in jobs if j.get("platform") == "wttj"]
    indeed_jobs  = [j for j in jobs if j.get("platform") != "wttj"]
    n_wttj       = len(wttj_jobs)
    n_indeed     = len(indeed_jobs)

    print()
    print("=" * 55)
    print("  Nettoyage jobs_raw.json — entrées WTTJ")
    print("=" * 55)
    print(f"  Entrées avant nettoyage : {total_before}")
    print(f"  Entrées WTTJ à supprimer: {n_wttj}")
    print(f"  Entrées Indeed conservées: {n_indeed}")

    if n_wttj == 0:
        print()
        print("  Aucune entrée WTTJ trouvée — rien à faire.")
        print("=" * 55)
        return

    # Confirmation manuelle
    print()
    print(f"  Backup : {BACKUP_PATH}")
    print()
    try:
        rep = input("  Confirmer la suppression ? (oui/non) : ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        print("  Annulé.")
        sys.exit(0)

    if rep != "oui":
        print("  Annulé (répondre 'oui' pour confirmer).")
        sys.exit(0)

    # Backup
    shutil.copy2(JOBS_PATH, BACKUP_PATH)
    print(f"  Backup créé : {BACKUP_PATH}")

    # Purge
    with open(JOBS_PATH, "w", encoding="utf-8") as f:
        json.dump(indeed_jobs, f, ensure_ascii=False, indent=2)

    print()
    print(f"  Entrées supprimées      : {n_wttj}")
    print(f"  Entrées conservées      : {n_indeed}")
    print(f"  Entrées après nettoyage : {n_indeed}")
    print("=" * 55)
    print()


if __name__ == "__main__":
    main()
