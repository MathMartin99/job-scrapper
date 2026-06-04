"""
reset_data.py — Réinitialise les données runtime du projet job-bot.

Usage :
    python app/src/jobs/reset_data.py
    python app/src/jobs/reset_data.py --keep-profile

Actions :
    - Crée les dossiers nécessaires s'ils n'existent pas
    - Remet jobs_raw.json et selected_jobs.json à []
    - Vide selected_urls.txt
    - Recrée tracking.csv avec l'en-tête correct
    - Supprime les rapports Markdown générés (report_*.md)
    - Supprime les backups runtime (app/data/backups/, app/config/backups/)
    - Supprime les logs (app/data/logs/)
    - Réinitialise run_stats.json à {}

Ne supprime pas :
    - candidate_profile.yaml
    - candidate_profile.example.yaml
    - Le code source
"""

import argparse
import glob
import json
import os
import shutil
import sys

# Force UTF-8 output on Windows (avoids UnicodeEncodeError with cp1252)
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

TRACKING_HEADER = "job_id,platform,company,title,location,url,score,status,applied_at,notes\n"

DIRS_TO_ENSURE = [
    "app/data/jobs",
    "app/data/exports",
    "app/data/logs",
    "app/data/backups",
    "app/data/cv",
    "app/config/backups",
]


def p(rel):
    return os.path.join(BASE_DIR, rel.replace("/", os.sep))


def reset(keep_profile=False):
    print("=== reset_data.py — Réinitialisation des données ===\n")

    # Créer les dossiers manquants
    for d in DIRS_TO_ENSURE:
        os.makedirs(p(d), exist_ok=True)
        print(f"[OK] dossier présent : {d}")

    print()

    # Réinitialiser jobs_raw.json
    with open(p("app/data/jobs/jobs_raw.json"), "w", encoding="utf-8") as f:
        f.write("[]\n")
    print("[OK] app/data/jobs/jobs_raw.json : []")

    # Reinitialiser selected_jobs.json
    with open(p("app/data/jobs/selected_jobs.json"), "w", encoding="utf-8") as f:
        f.write("[]\n")
    print("[OK] app/data/jobs/selected_jobs.json : []")

    # Vider selected_urls.txt
    with open(p("app/data/jobs/selected_urls.txt"), "w", encoding="utf-8") as f:
        pass
    print("[OK] app/data/jobs/selected_urls.txt : vide")

    # Recree tracking.csv avec l'en-tete
    with open(p("app/data/tracking.csv"), "w", encoding="utf-8", newline="") as f:
        f.write(TRACKING_HEADER)
    print("[OK] app/data/tracking.csv : en-tete seul")

    # Reinitialiser run_stats.json
    with open(p("app/data/run_stats.json"), "w", encoding="utf-8") as f:
        json.dump({}, f, indent=2)
    print("[OK] app/data/run_stats.json : {}")

    print()

    # Supprimer les rapports Markdown générés
    reports = glob.glob(p("app/data/exports/report_*.md"))
    if reports:
        for r in reports:
            os.remove(r)
        print(f"[OK] {len(reports)} rapport(s) Markdown supprimé(s)")
    else:
        print("[--] Aucun rapport Markdown à supprimer")

    # Supprimer les backups runtime
    for backup_dir in ["app/data/backups", "app/config/backups"]:
        files = [
            f for f in glob.glob(os.path.join(p(backup_dir), "*"))
            if os.path.isfile(f)
        ]
        for f in files:
            os.remove(f)
        if files:
            print(f"[OK] {len(files)} backup(s) supprimé(s) dans {backup_dir}")

    # Supprimer les logs
    logs = [
        f for f in glob.glob(os.path.join(p("app/data/logs"), "*"))
        if os.path.isfile(f)
    ]
    if logs:
        for f in logs:
            os.remove(f)
        print(f"[OK] {len(logs)} log(s) supprimé(s)")
    else:
        print("[--] Aucun log à supprimer")

    print()

    if keep_profile:
        print("[--] --keep-profile : candidate_profile.yaml conservé (comportement par défaut)")
    else:
        profile = p("app/config/candidate_profile.yaml")
        if os.path.exists(profile):
            print("[--] candidate_profile.yaml conserve (non supprime par defaut)")
            print("     Pour le reinitialiser manuellement :")
            print("     copy app\\config\\candidate_profile.example.yaml app\\config\\candidate_profile.yaml")

    print("\n=== Réinitialisation terminée ===")
    print("Lancez maintenant : python web_ui.py")


def main():
    parser = argparse.ArgumentParser(
        description="Réinitialise les données runtime de job-bot."
    )
    parser.add_argument(
        "--keep-profile",
        action="store_true",
        help="Conserve candidate_profile.yaml (comportement par défaut, option acceptée sans effet).",
    )
    args = parser.parse_args()
    reset(keep_profile=args.keep_profile)


if __name__ == "__main__":
    main()
