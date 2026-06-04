import argparse
import csv
import json
import logging
import os
import random
import re
import sys
import time
import yaml

from seleniumbase import SB

CONFIG_PATH   = "app/config/candidate_profile.yaml"
OUTPUT_PATH   = "app/data/jobs/jobs_raw.json"
TRACKING_PATH = "app/data/tracking.csv"
PROFILE_DIR   = "app/data/chrome_profile/wttj"
BASE_URL      = "https://www.welcometothejungle.com"

MANUAL_STATUSES = {"ignored", "applied", "already_applied", "rejected_by_company", "manually_rejected"}

# Matches /fr/companies/[company-slug]/jobs/[job-slug]
WTTJ_JOB_PATTERN = re.compile(r'/fr/companies/([^/]+)/jobs/([^/?#]+)')

BLOCK_MARKERS = [
    "une erreur s'est produite",
    "page introuvable",
    "access denied",
    "something went wrong",
]

# Soft pre-filter at listing stage — obvious senior roles only.
# The real business rejection stays in matcher.py.
SENIOR_TITLE_MARKERS = [
    " senior ", " lead ", "manager", "principal",
    "staff engineer", "head of", "director", "directeur",
    "architect", "architecte",
]

logger = logging.getLogger(__name__)


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_excluded_urls():
    """Return URLs with a manual status in tracking.csv (must not be re-collected)."""
    excluded = set()
    try:
        with open(TRACKING_PATH, "r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("status", "").strip().lower() in MANUAL_STATUSES:
                    url = row.get("url", "").strip()
                    if url:
                        excluded.add(url)
    except FileNotFoundError:
        pass
    return excluded


def human_delay():
    time.sleep(random.uniform(3, 5))


def load_existing_jobs(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_jobs(jobs):
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)


def is_likely_senior(title):
    """Return True if the title clearly signals a senior role."""
    if not title:
        return False
    # Wrap in spaces so " lead " matches "Tech Lead" but not "upload" or "platform".
    t = " " + title.lower() + " "
    return any(m in t for m in SENIOR_TITLE_MARKERS)


def detect_session_expired(sb):
    """Session expirée = WTTJ a redirigé vers la page de login.
    On vérifie l'URL courante, pas le source (qui contient toujours
    'authenticate/signin' dans les bundles JS Next.js)."""
    try:
        return "authenticate/signin" in sb.get_current_url()
    except Exception:
        return False


def detect_block(sb):
    try:
        source = sb.get_page_source().lower()
        return any(m in source for m in BLOCK_MARKERS)
    except Exception:
        return False


def count_job_links_in_dom(sb):
    """Count distinct job links visible in the current DOM."""
    try:
        links = sb.find_elements("a[href*='/fr/companies/'][href*='/jobs/']")
        urls = set()
        for link in links:
            try:
                href = link.get_attribute("href") or ""
                m = WTTJ_JOB_PATTERN.search(href)
                if m:
                    urls.add((m.group(1), m.group(2)))
            except Exception:
                pass
        return len(urls)
    except Exception:
        return 0


def wait_for_user(msg):
    """Print msg and wait for Enter. Returns False on Ctrl+C/EOF."""
    print()
    print(msg)
    try:
        input("  → Appuie sur Entrée pour continuer (Ctrl+C pour annuler) : ")
        return True
    except (EOFError, KeyboardInterrupt):
        return False


def _wait_for_wttj_auto(sb, timeout, interval):
    """Poll until WTTJ job links appear in DOM or timeout expires.

    Returns "ok" when jobs are detected, "timeout" on expiry, "blocked" on CAPTCHA.
    """
    import time as _time
    deadline = _time.time() + timeout
    attempt = 0
    while _time.time() < deadline:
        attempt += 1
        _time.sleep(interval)
        if detect_block(sb):
            logger.warning("[auto-confirm] WTTJ : blocage / CAPTCHA détecté.")
            return "blocked"
        if detect_session_expired(sb):
            logger.info(f"[auto-confirm] Tentative {attempt} — page login, attente session...")
            continue
        job_count = count_job_links_in_dom(sb)
        logger.info(f"[auto-confirm] Tentative {attempt} — {job_count} lien(s) offre(s) dans le DOM")
        if job_count > 0:
            logger.info(f"[auto-confirm] Session WTTJ détectée ({job_count} liens). Collecte...")
            return "ok"
    logger.warning(f"[auto-confirm] Timeout {timeout}s dépassé sans détection de session WTTJ.")
    return "timeout"


def collect_from_page(sb, max_jobs, seen_urls):
    """Collect job listings from the current page.

    Returns (jobs, stats) where stats = {found, skipped_seen, skipped_senior}.
    """
    jobs = []
    stats = {"found": 0, "skipped_seen": 0, "skipped_senior": 0}

    # Wait for Next.js hydration
    time.sleep(4)

    try:
        source = sb.get_page_source()
        all_links = sb.find_elements("a")
        logger.debug(f"Source: {len(source)} chars | <a> tags: {len(all_links)}")
        job_link_count = sum(1 for _ in WTTJ_JOB_PATTERN.finditer(source))
        logger.info(f"Liens offres WTTJ dans source: {job_link_count}")
    except Exception:
        pass

    links = []
    for selector in [
        "a[href*='/fr/companies/'][href*='/jobs/']",
        "a[href*='/jobs/']",
    ]:
        try:
            found = sb.find_elements(selector)
            if found:
                links = found
                logger.info(f"Liens trouvés avec '{selector}': {len(found)}")
                break
        except Exception:
            pass

    if not links:
        logger.warning("Aucun lien offre trouvé sur la page.")
        return jobs, stats

    seen_on_page = set()

    for link in links:
        try:
            href = link.get_attribute("href") or ""
        except Exception:
            continue

        if not href:
            continue

        m = WTTJ_JOB_PATTERN.search(href)
        if not m:
            continue

        company_slug = m.group(1)
        job_slug     = m.group(2)
        clean_url    = f"{BASE_URL}/fr/companies/{company_slug}/jobs/{job_slug}"

        # Intra-page dedup
        if clean_url in seen_on_page:
            continue
        seen_on_page.add(clean_url)
        stats["found"] += 1

        if clean_url in seen_urls:
            stats["skipped_seen"] += 1
            continue

        # Title: try heading sub-element first (cleaner), fall back to card text
        title = ""
        try:
            heading = link.find_element("css selector", "h3, h4, [data-testid='job-title']")
            title = heading.text.strip()
        except Exception:
            pass
        if not title:
            try:
                raw = link.text.strip()
                if raw:
                    lines = [l.strip() for l in raw.split("\n") if l.strip()]
                    title = lines[0] if lines else ""
            except Exception:
                pass

        if is_likely_senior(title):
            stats["skipped_senior"] += 1
            logger.debug(f"Filtré (senior): '{title[:60]}'")
            continue

        company = company_slug.replace("-", " ").title()

        jobs.append({
            "id": f"wttj-{job_slug}",
            "platform": "wttj",
            "company": company,
            "title": title,
            "location": "",
            "url": clean_url,
            "description": "",
        })
        seen_urls.add(clean_url)
        logger.info(f"URL retenue: {clean_url} | '{title[:50]}'")

        if len(jobs) >= max_jobs:
            break

    return jobs, stats


def print_summary(total_stats, all_jobs_count):
    print()
    print("=" * 55)
    print("  Résumé collecte WTTJ")
    print(f"  Liens trouvés (total)   : {total_stats['found']}")
    print(f"  Filtrés senior/lead     : {total_stats['skipped_senior']}")
    print(f"  Doublons/déjà présents  : {total_stats['skipped_seen']}")
    print(f"  Nouveaux ajoutés        : {total_stats['added']}")
    print(f"  Total dans jobs_raw.json: {all_jobs_count}")
    print("=" * 55)


def _accumulate(total_stats, page_stats, new_jobs):
    for k in ("found", "skipped_seen", "skipped_senior"):
        total_stats[k] += page_stats[k]
    total_stats["added"] += new_jobs


def collect_pages(sb, jobs_matches_url, max_pages, max_jobs, seen_urls, all_jobs,
                  start_page=1, first_page_already_open=False):
    """Paginate through jobs-matches and collect up to max_jobs new offers.

    Returns (total_stats, exit_signal) where exit_signal is:
        None         → normal completion
        'expired'    → session expired mid-run
        'blocked'    → WTTJ block detected
    """
    total_stats = {"found": 0, "skipped_seen": 0, "skipped_senior": 0, "added": 0}

    for page_num in range(start_page, max_pages + 1):
        remaining = max_jobs - total_stats["added"]
        if remaining <= 0:
            logger.info("Limite max_jobs atteinte — arrêt de la pagination.")
            break

        if not (first_page_already_open and page_num == start_page):
            sep = "&" if "?" in jobs_matches_url else "?"
            url = f"{jobs_matches_url}{sep}page={page_num}"
            logger.info(f"Collecte WTTJ jobs-matches page {page_num} : {url}")
            sb.open(url)
            human_delay()

            if detect_session_expired(sb):
                logger.error(
                    "Session WTTJ expirée — relancez avec : "
                    "python app/src/jobs/collect_wttj.py --interactive --append"
                )
                return total_stats, "expired"

            if detect_block(sb):
                logger.warning(f"Blocage WTTJ détecté (page {page_num}) — arrêt.")
                return total_stats, "blocked"

            sb.scroll_to_bottom()
            human_delay()
        else:
            # First page already loaded by interactive verify loop — just scroll
            logger.info(f"Collecte WTTJ jobs-matches page {page_num} (déjà chargée)")
            sb.scroll_to_bottom()
            human_delay()

        jobs, page_stats = collect_from_page(sb, remaining, seen_urls)
        all_jobs.extend(jobs)
        _accumulate(total_stats, page_stats, len(jobs))
        logger.info(f"Page {page_num}: {len(jobs)} nouvelles offres ajoutées")

        # Stop early if the page had no job links at all (past end of matches)
        if page_stats["found"] == 0:
            logger.info(f"Page {page_num}: aucun lien job — fin des matches.")
            break

    return total_stats, None


def run_interactive(sb, jobs_matches_url, max_pages, max_jobs, seen_urls, all_jobs,
                    auto_confirm=False, auth_timeout=180, auth_poll_interval=2):
    """Interactive mode: open jobs-matches, wait for login confirmation, then paginate.

    When auto_confirm=True, polls the DOM instead of blocking on input().
    """
    logger.info(f"Ouverture de : {jobs_matches_url}")
    sb.open(jobs_matches_url)
    time.sleep(3)

    if auto_confirm:
        logger.info(f"[auto-confirm] Attente session WTTJ (timeout={auth_timeout}s, interval={auth_poll_interval}s)...")
        status = _wait_for_wttj_auto(sb, auth_timeout, auth_poll_interval)
        if status == "blocked":
            return False
        if status == "timeout":
            logger.warning("[auto-confirm] Timeout WTTJ — aucune session détectée, arrêt.")
            return False
        # status == "ok" → proceed to collection
    else:
        print()
        print("=" * 65)
        print("  Chrome est ouvert sur ta page jobs-matches WTTJ.")
        print()
        print("  Etapes :")
        print("  1. Accepte les cookies si une banniere apparait")
        print("  2. Connecte-toi a ton compte WTTJ si besoin (bouton en haut a droite)")
        print("  3. Verifie que tes offres personnalisees s'affichent")
        print("  4. Reviens ici et appuie sur Entree")
        print()
        print("  Ne ferme PAS Chrome.")
        print("=" * 65)

        ok = wait_for_user("  Pret ? Appuie sur Entree pour lancer la verification")
        if not ok:
            logger.info("Mode interactif annule.")
            return False

        # Verify loop — same Chrome window
        attempt = 0
        while True:
            attempt += 1
            time.sleep(2)

            current_url = sb.get_current_url()
            on_login    = detect_session_expired(sb)
            job_count   = count_job_links_in_dom(sb)

            print()
            print(f"  [Verification {attempt}]")
            print(f"  URL actuelle  : {current_url[:90]}")
            print(f"  Login wall    : {'OUI' if on_login else 'non'}")
            print(f"  Liens offres  : {job_count} lien(s) detecte(s) dans le DOM")

            if on_login:
                print()
                print("  La page de login est affichee.")
                print("  Connecte-toi a WTTJ dans le navigateur, puis reviens ici.")
                ok = wait_for_user("  Connecte ? Appuie sur Entree")
                if not ok:
                    return False
                continue

            if job_count > 0:
                print()
                print(f"  Session valide - {job_count} offre(s) visibles. Lancement de la collecte...")
                logger.info(f"Session WTTJ verifiee ({job_count} liens). Collecte en cours.")
                break

            print()
            print("  Aucune offre detectee. Causes possibles :")
            print("    - Tu n'es pas connecte (connecte-toi dans le navigateur)")
            print("    - La page n'a pas fini de charger (attends quelques secondes)")
            print("  Connecte-toi si besoin, puis verifie que les offres s'affichent.")
            ok = wait_for_user("  Quand des offres sont visibles, appuie sur Entree")
            if not ok:
                return False

    # Page 1 is already open and verified — paginate from there
    total_stats, signal = collect_pages(
        sb, jobs_matches_url, max_pages, max_jobs, seen_urls, all_jobs,
        start_page=1, first_page_already_open=True,
    )

    print_summary(total_stats, len(all_jobs))

    if signal == "expired":
        return None   # caller maps to exit 3
    if signal == "blocked":
        return False  # caller maps to exit 2

    if total_stats["added"] == 0:
        logger.warning("Aucune offre WTTJ collectée en mode interactif.")
    return total_stats["added"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--append", action="store_true",
                        help="Append to existing jobs_raw.json instead of resetting.")
    parser.add_argument("--interactive", action="store_true",
                        help="Open Chrome, wait for manual login, then collect in the same window.")
    parser.add_argument("--pages", type=int, default=None,
                        help="Number of jobs-matches pages to scrape (overrides YAML max_pages).")
    parser.add_argument("--auto-confirm", action="store_true",
                        help="Poll for session readiness instead of blocking on input().")
    parser.add_argument("--auth-timeout", type=int, default=180,
                        help="Seconds to wait for WTTJ session in auto-confirm mode.")
    parser.add_argument("--auth-poll-interval", type=int, default=2,
                        help="Polling interval in seconds for auto-confirm mode.")
    args = parser.parse_args()

    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        )

    config           = load_config()
    wttj_cfg         = config["wttj"]
    jobs_matches_url = wttj_cfg["jobs_matches_url"]
    max_pages        = args.pages if args.pages is not None else wttj_cfg.get("max_pages", 3)
    max_jobs         = wttj_cfg.get("max_jobs", 50)

    excluded_urls = load_excluded_urls()
    if excluded_urls:
        logger.info(f"URLs exclues (tracking.csv, statuts manuels) : {len(excluded_urls)}")

    if args.append:
        existing_jobs = load_existing_jobs(OUTPUT_PATH)
        seen_urls     = {j["url"] for j in existing_jobs} | excluded_urls
        all_jobs      = existing_jobs
    else:
        seen_urls = excluded_urls
        all_jobs  = []

    os.makedirs(PROFILE_DIR, exist_ok=True)

    if args.interactive:
        with SB(uc=True, headless=False, user_data_dir=PROFILE_DIR) as sb:
            result = run_interactive(
                sb, jobs_matches_url, max_pages, max_jobs, seen_urls, all_jobs,
                auto_confirm=args.auto_confirm,
                auth_timeout=args.auth_timeout,
                auth_poll_interval=args.auth_poll_interval,
            )

        save_jobs(all_jobs)
        logger.info(f"Total: {len(all_jobs)} offres sauvegardées dans {OUTPUT_PATH}")

        if result is None:
            logger.error("Session WTTJ expirée en cours de collecte.")
            sys.exit(3)
        if result is False:
            sys.exit(2)
        return

    # Standard (non-interactive) mode
    with SB(uc=True, headless=False, user_data_dir=PROFILE_DIR) as sb:
        total_stats, signal = collect_pages(
            sb, jobs_matches_url, max_pages, max_jobs, seen_urls, all_jobs,
        )

    print_summary(total_stats, len(all_jobs))
    save_jobs(all_jobs)
    logger.info(f"Total: {len(all_jobs)} offres sauvegardées dans {OUTPUT_PATH}")

    if total_stats["added"] == 0 and signal is None:
        logger.warning(
            "Aucune offre WTTJ trouvée. "
            "Si vous n'êtes pas connecté, lancez : "
            "python app/src/jobs/collect_wttj.py --interactive --append"
        )

    if signal == "expired":
        sys.exit(3)
    if signal == "blocked":
        sys.exit(2)


if __name__ == "__main__":
    main()
