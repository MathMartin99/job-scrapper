"""
setup_local.py — Initialisation locale apres clone du repo.

Usage :
    python app/src/jobs/setup_local.py

Actions (safe — ne jamais ecraser un fichier existant) :
    - Cree les dossiers de donnees necessaires s'ils n'existent pas
    - Cree jobs_raw.json avec [] s'il est absent
    - Cree selected_jobs.json avec [] s'il est absent
    - Cree selected_urls.txt vide s'il est absent
    - Cree tracking.csv avec l'en-tete s'il est absent
    - Cree run_stats.json avec {} s'il est absent
    - Copie candidate_profile.example.yaml vers candidate_profile.yaml
      SEULEMENT si candidate_profile.yaml n'existe pas encore
    - N'ecrase jamais candidate_profile.yaml existant
    - N'ecrase jamais aucune donnee existante

Apres ce script :
    python web_ui.py
"""

import json
import os
import shutil
import sys

# Force UTF-8 output on Windows
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

TRACKING_HEADER = "job_id,platform,company,title,location,url,score,status,applied_at,notes\n"

DIRS_TO_CREATE = [
    "app/data/jobs",
    "app/data/exports",
    "app/data/logs",
    "app/data/backups",
    "app/data/cv",
    "app/config/backups",
]

DATA_FILES = {
    "app/data/jobs/jobs_raw.json":      lambda f: f.write("[]\n"),
    "app/data/jobs/selected_jobs.json": lambda f: f.write("[]\n"),
    "app/data/jobs/selected_urls.txt":  lambda f: None,
}


def p(rel):
    return os.path.join(BASE_DIR, rel.replace("/", os.sep))


def setup():
    print("=== setup_local.py - Initialisation locale ===\n")
    any_action = False

    # 1. Creer les dossiers
    for d in DIRS_TO_CREATE:
        path = p(d)
        if not os.path.isdir(path):
            os.makedirs(path, exist_ok=True)
            print(f"[CREE] dossier : {d}")
            any_action = True
        else:
            print(f"[OK]   dossier present : {d}")

    print()

    # 2. Creer les fichiers de donnees manquants
    for rel, writer in DATA_FILES.items():
        path = p(rel)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                writer(f)
            print(f"[CREE] {rel}")
            any_action = True
        else:
            print(f"[OK]   present : {rel}")

    # tracking.csv
    tracking = p("app/data/tracking.csv")
    if not os.path.exists(tracking):
        with open(tracking, "w", encoding="utf-8", newline="") as f:
            f.write(TRACKING_HEADER)
        print("[CREE] app/data/tracking.csv")
        any_action = True
    else:
        print("[OK]   present : app/data/tracking.csv")

    # run_stats.json
    stats = p("app/data/run_stats.json")
    if not os.path.exists(stats):
        with open(stats, "w", encoding="utf-8") as f:
            json.dump({}, f, indent=2)
        print("[CREE] app/data/run_stats.json")
        any_action = True
    else:
        print("[OK]   present : app/data/run_stats.json")

    print()

    # 3. Copier le profil exemple si candidate_profile.yaml est absent
    example = p("app/config/candidate_profile.example.yaml")
    profile = p("app/config/candidate_profile.yaml")

    if not os.path.exists(profile):
        if os.path.exists(example):
            shutil.copy2(example, profile)
            print("[CREE] app/config/candidate_profile.yaml (copie depuis .example.yaml)")
            print()
            print("  ETAPE SUIVANTE : ouvrez candidate_profile.yaml et adaptez :")
            print("    - target_titles    : les intitules de postes recherches")
            print("    - must_have_keywords : vos technologies indispensables")
            print("    - locations        : vos villes cibles")
            print("    - URLs de recherche Indeed et HelloWork")
            any_action = True
        else:
            print("[ATTENTION] candidate_profile.example.yaml introuvable.")
            print("  Impossible de creer candidate_profile.yaml automatiquement.")
    else:
        print("[OK]   candidate_profile.yaml deja present - conserve sans modification")

    print()

    if any_action:
        print("=== Initialisation terminee ===")
    else:
        print("=== Tout est deja en place - rien a faire ===")

    print()
    print("Lancez maintenant :")
    print("  python web_ui.py")
    print()
    print("Puis ouvrez : http://127.0.0.1:5000")


if __name__ == "__main__":
    setup()
