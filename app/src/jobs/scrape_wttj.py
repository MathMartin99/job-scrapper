"""
scrape_wttj.py — Enrichit les offres wttj-* de jobs_raw.json avec la description complète.

Usage :
    python app/src/jobs/scrape_wttj.py --interactive
    python app/src/jobs/scrape_wttj.py --interactive --limit 5
"""

import argparse
import json
import logging
import os
import random
import sys
import time

from seleniumbase import SB

INPUT_OUTPUT_PATH = "app/data/jobs/jobs_raw.json"
PROFILE_DIR = "app/data/chrome_profile/wttj"

BLOCK_MARKERS = [
    "une erreur s'est produite",
    "page introuvable",
    "access denied",
    "something went wrong",
]

# Selectors tried in order — first non-empty result wins.
# WTTJ uses Next.js/styled-components so class names are generated;
# data-testid and semantic HTML are more stable.
TITLE_SELECTORS = [
    "h1",
    "[data-testid='job-title']",
]

COMPANY_SELECTORS = [
    "[data-testid='organization-name']",
    "[data-testid='company-title']",
    "[data-testid='company-name']",
]

LOCATION_SELECTORS = [
    "[data-testid='job-location']",
    "[data-testid='location']",
    "[data-testid='office-location']",
    "[data-testid='job-metadatas-location']",
    "[data-testid='job-contract-location']",
]

DESCRIPTION_SELECTORS = [
    "[data-testid='job-description']",
    "[data-testid='description']",
    "div[data-testid*='description']",
    "section[data-testid*='description']",
    "article",
]

logger = logging.getLogger(__name__)


def needs_metadata_refresh(job):
    """Return True if title looks polluted or location is missing."""
    title = job.get("title", "")
    return len(title) > 120 or not job.get("location")


def load_jobs():
    try:
        with open(INPUT_OUTPUT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error(f"Impossible de lire {INPUT_OUTPUT_PATH}: {e}")
        sys.exit(1)


def save_jobs(jobs):
    with open(INPUT_OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)


def human_delay():
    time.sleep(random.uniform(3, 6))


def detect_session_expired(sb):
    try:
        return "authenticate/signin" in sb.get_current_url()
    except Exception:
        return False


def detect_block(sb):
    try:
        src = sb.get_page_source().lower()
        return any(m in src for m in BLOCK_MARKERS)
    except Exception:
        return False


def wait_for_user(msg):
    print()
    print(msg)
    try:
        input("  → Appuie sur Entrée pour continuer (Ctrl+C pour annuler) : ")
        return True
    except (EOFError, KeyboardInterrupt):
        return False


def try_get_text(sb, selectors, min_len=2):
    """Try each selector in order; return text of first match with enough content."""
    for sel in selectors:
        try:
            elements = sb.find_elements(sel)
            for el in elements:
                text = el.text.strip()
                if len(text) >= min_len:
                    return text
        except Exception:
            continue
    return ""


def extract_location(sb):
    """Try data-testid selectors then JS wildcard fallback for job location."""
    loc = try_get_text(sb, LOCATION_SELECTORS, min_len=2)
    if loc:
        return loc
    try:
        result = sb.execute_script("""
            var els = document.querySelectorAll(
                '[data-testid*="location"], [data-testid*="office"], [data-testid*="contract"]'
            );
            for (var i = 0; i < els.length; i++) {
                var t = (els[i].innerText || '').trim();
                if (t.length > 1 && t.length < 100) return t;
            }
            return '';
        """) or ""
        return result.strip()
    except Exception:
        return ""


def extract_description(sb):
    """Extract job description from the current page.

    Strategy:
    1. Try data-testid / semantic selectors.
    2. JS fallback: collect all <p> and <li> text from main content.
    3. JS fallback: largest text block under <main>.
    """
    text = try_get_text(sb, DESCRIPTION_SELECTORS, min_len=100)
    if text:
        return text

    # Fallback 1 — join all paragraphs/list items inside <main>
    try:
        result = sb.execute_script("""
            var root = document.querySelector('main') || document.body;
            var nodes = root.querySelectorAll('p, li');
            var parts = [];
            nodes.forEach(function(el) {
                var t = (el.innerText || '').trim();
                if (t.length > 20) parts.push(t);
            });
            return parts.join('\\n');
        """) or ""
        if len(result) >= 100:
            return result
    except Exception:
        pass

    # Fallback 2 — largest single element under <main>
    try:
        result = sb.execute_script("""
            var candidates = document.querySelectorAll(
                'main section, main article, main div, article, section'
            );
            var best = '';
            candidates.forEach(function(el) {
                var t = (el.innerText || '').trim();
                if (t.length > best.length && t.length < 40000) {
                    best = t;
                }
            });
            return best;
        """) or ""
        if len(result) >= 100:
            return result
    except Exception:
        pass

    return ""


def scrape_job(sb, job, refresh_metadata=False):
    """Visit the job URL, extract enrichment fields, return updated dict.

    Return values:
        "session_expired"  — redirect to login page
        "blocked"          — block/error page detected
        "error"            — navigation or unexpected failure
        dict               — job with enriched fields

    refresh_metadata=True: update title/company/location only, skip description
    re-extraction if description already present (faster, for metadata cleanup runs).
    """
    url = job["url"]
    logger.info(f"Scraping : {url}")

    try:
        sb.open(url)
    except Exception as e:
        logger.warning(f"Erreur ouverture {url}: {e}")
        return "error"

    # Wait for Next.js hydration
    time.sleep(4)
    sb.scroll_to_bottom()
    time.sleep(1)

    if detect_session_expired(sb):
        return "session_expired"

    if detect_block(sb):
        logger.warning(f"Blocage détecté : {url}")
        return "blocked"

    updated = dict(job)

    # Title — always prefer detail page <h1> (listing card text is polluted)
    title = try_get_text(sb, TITLE_SELECTORS)
    if title and len(title) < 200:
        updated["title"] = title

    # Company — update unconditionally (slug-derived value is a placeholder)
    company = try_get_text(sb, COMPANY_SELECTORS)
    if company and len(company) < 120:
        updated["company"] = company

    # Location
    location = extract_location(sb)
    if location and len(location) < 120:
        updated["location"] = location

    # Description — skip re-extraction in refresh_metadata mode if already present
    if refresh_metadata and updated.get("description"):
        logger.info(f"  metadata updated | title={updated['title'][:50]!r}")
    else:
        description = extract_description(sb)
        if description:
            updated["description"] = description
            logger.info(f"  {len(description)} chars | '{description[:70].strip()}...'")
        else:
            logger.warning(f"  Pas de description extraite")

    return updated


def run_interactive_check(sb, first_url):
    """Open the first job URL and wait for the user to confirm login."""
    logger.info(f"Ouverture de : {first_url}")
    sb.open(first_url)
    time.sleep(3)

    print()
    print("=" * 65)
    print("  Chrome est ouvert sur une offre WTTJ.")
    print()
    print("  Étapes :")
    print("  1. Si une page de login s'affiche : connecte-toi")
    print("  2. Si l'offre est visible : tu peux continuer directement")
    print("  3. Reviens ici et appuie sur Entrée")
    print()
    print("  Ne ferme PAS Chrome.")
    print("=" * 65)

    ok = wait_for_user("  Prêt ? Appuie sur Entrée pour lancer le scraping")
    if not ok:
        logger.info("Scraping annulé.")
        return False

    if detect_session_expired(sb):
        print()
        print("  Session expirée — connecte-toi dans Chrome puis relance le script.")
        return False

    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interactive", action="store_true",
                        help="Ouvre Chrome et attend confirmation login avant de scraper.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Scraper au maximum N offres ce run (utile pour tester).")
    parser.add_argument("--refresh-metadata", action="store_true",
                        help="Revisiter les offres avec titre pollué (>120 chars) ou location manquante "
                             "pour mettre à jour title/company/location sans re-scraper la description.")
    args = parser.parse_args()

    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        )

    os.makedirs(PROFILE_DIR, exist_ok=True)

    jobs      = load_jobs()
    wttj_jobs = [j for j in jobs if j.get("platform") == "wttj"]

    if args.refresh_metadata:
        to_scrape = [j for j in wttj_jobs if needs_metadata_refresh(j)]
        already   = len(wttj_jobs) - len(to_scrape)
        logger.info(f"Mode refresh-metadata")
        logger.info(f"Jobs WTTJ en base      : {len(wttj_jobs)}")
        logger.info(f"Métadonnées OK (skip)  : {already}")
        logger.info(f"À revisiter ce run     : {len(to_scrape)}")
    else:
        to_scrape = [j for j in wttj_jobs if not j.get("description")]
        already   = len(wttj_jobs) - len(to_scrape)
        logger.info(f"Jobs WTTJ en base      : {len(wttj_jobs)}")
        logger.info(f"Déjà enrichis (skippés): {already}")
        logger.info(f"À scraper ce run       : {len(to_scrape)}")

    if not to_scrape:
        if args.refresh_metadata:
            logger.info("Rien à revisiter — tous les titres sont propres et locations présentes.")
        else:
            logger.info("Rien à scraper — tous les jobs WTTJ ont déjà une description.")
        return

    if args.limit:
        to_scrape = to_scrape[:args.limit]
        logger.info(f"Limite appliquée       : {args.limit} offres")

    # Build URL→index map for in-place updates
    url_to_idx = {j["url"]: i for i, j in enumerate(jobs)}

    enriched = 0
    errors   = 0

    with SB(uc=True, headless=False, user_data_dir=PROFILE_DIR) as sb:
        if args.interactive:
            ok = run_interactive_check(sb, to_scrape[0]["url"])
            if not ok:
                sys.exit(1)

        for job in to_scrape:
            result = scrape_job(sb, job, refresh_metadata=args.refresh_metadata)

            if result == "session_expired":
                logger.error(
                    "Session WTTJ expirée — sauvegarde de la progression et arrêt. "
                    "Relancez avec --interactive."
                )
                save_jobs(jobs)
                sys.exit(3)

            if result in ("blocked", "error"):
                errors += 1
            else:
                idx = url_to_idx[job["url"]]
                jobs[idx] = result
                enriched += 1
                save_jobs(jobs)  # persist after each success — crash-safe

            human_delay()

    # Final save covers any edge case
    save_jobs(jobs)

    mode_label = "Résumé refresh-metadata WTTJ" if args.refresh_metadata else "Résumé scraping WTTJ"
    updated_label = "Métadonnées mises à jour" if args.refresh_metadata else "Enrichis ce run"

    print()
    print("=" * 55)
    print(f"  {mode_label}")
    print(f"  Jobs WTTJ en base      : {len(wttj_jobs)}")
    print(f"  Skippés                : {already}")
    print(f"  {updated_label:<23}: {enriched}")
    print(f"  Erreurs/blocages       : {errors}")
    print("=" * 55)

    logger.info(f"Mis à jour : {enriched} | Erreurs : {errors}")


if __name__ == "__main__":
    main()
