"""
setup_wttj_session.py — Configuration one-shot de la session WTTJ.

Usage :
    python app/src/jobs/setup_wttj_session.py

Ce script ouvre Chrome avec un profil dédié, navigue vers WTTJ,
et attend que tu confirmes manuellement que des offres sont visibles.
"""

import logging
import os
import re
import sys
import time

from seleniumbase import SB

PROFILE_DIR = "app/data/chrome_profile/wttj"
SEARCH_URL = "https://www.welcometothejungle.com/fr/jobs?query=devops&aroundQuery=Paris&page=1"

WTTJ_JOB_PATTERN = re.compile(r'/fr/companies/([^/]+)/jobs/([^/?#]+)')

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)


def count_job_links(sb):
    """Compte les liens /fr/companies/.../jobs/... visibles dans le DOM."""
    try:
        source = sb.get_page_source()
        return len(set(WTTJ_JOB_PATTERN.findall(source)))
    except Exception:
        return 0


def is_on_login_page(sb):
    """Vérifie si Chrome a été redirigé vers la page de login (URL réelle)."""
    try:
        return "authenticate/signin" in sb.get_current_url()
    except Exception:
        return False


def prompt(msg):
    """Affiche un message et attend Entrée. Retourne False si Ctrl+C."""
    print()
    print(msg)
    try:
        input("  → Appuie sur Entrée pour continuer (Ctrl+C pour annuler) : ")
        return True
    except (EOFError, KeyboardInterrupt):
        return False


def verify_loop(sb):
    """Boucle de vérification : attend que des offres soient visibles."""
    attempt = 0
    while True:
        attempt += 1
        time.sleep(2)

        current_url = sb.get_current_url()
        job_count = count_job_links(sb)

        print()
        print(f"  [Vérification {attempt}]")
        print(f"  URL actuelle  : {current_url[:90]}")
        print(f"  Login wall    : {'OUI — tu n’es pas connecté' if is_on_login_page(sb) else 'non'}")
        print(f"  Liens offres  : {job_count} lien(s) /fr/companies/.../jobs/ détecté(s)")

        if is_on_login_page(sb):
            print()
            print("  ⚠  La page de login est affichée.")
            print("  → Connecte-toi à WTTJ dans le navigateur.")
            ok = prompt("  Appuie sur Entrée une fois connecté")
            if not ok:
                return False
            continue

        if job_count > 0:
            print()
            print(f"  ✓  {job_count} offre(s) détectée(s). Session valide.")
            print()
            logger.info("Session WTTJ validée avec succès.")
            logger.info("Lance maintenant :")
            logger.info("  python app/src/jobs/collect_wttj.py --page 0 --append")
            return True

        # 0 offres, pas de login wall
        print()
        print("  ✗  Aucune offre détectée sur cette page.")
        print("  Causes possibles :")
        print("    - Tu n'es pas encore connecté (connecte-toi dans le navigateur)")
        print("    - La page n'a pas fini de charger (attends quelques secondes)")
        print("    - La recherche DevOps Paris est vide (essaie une autre ville)")
        print()
        print("  → Dans Chrome : connecte-toi si besoin, puis navigue vers")
        print("    https://www.welcometothejungle.com/fr/jobs?query=devops&aroundQuery=Paris&page=1")
        print("    et vérifie visuellement que des offres s'affichent.")
        ok = prompt("  Quand des offres sont visibles, appuie sur Entrée")
        if not ok:
            return False


def main():
    os.makedirs(PROFILE_DIR, exist_ok=True)
    abs_profile = os.path.abspath(PROFILE_DIR)
    logger.info(f"Profil Chrome : {abs_profile}")
    logger.info("Ouverture de Chrome avec le profil dédié WTTJ...")

    with SB(uc=True, headless=False, user_data_dir=PROFILE_DIR) as sb:
        logger.info(f"Ouverture de : {SEARCH_URL}")
        sb.open(SEARCH_URL)
        time.sleep(3)

        print()
        print("=" * 65)
        print("  Chrome est ouvert sur la page de recherche WTTJ.")
        print()
        print("  Étapes :")
        print("  1. Accepte les cookies si une bannière apparaît")
        print("  2. Connecte-toi à ton compte WTTJ (bouton en haut à droite)")
        print("  3. Vérifie que des offres DevOps apparaissent dans la liste")
        print("  4. Reviens ici et appuie sur Entrée")
        print()
        print("  Ne ferme PAS Chrome.")
        print("=" * 65)

        ok = prompt("  Prêt ? Appuie sur Entrée pour lancer la vérification")
        if not ok:
            logger.info("Setup annulé.")
            sys.exit(0)

        success = verify_loop(sb)

    if not success:
        logger.warning("Setup WTTJ non validé. Relance le script quand tu es prêt.")
        sys.exit(1)


if __name__ == "__main__":
    main()
