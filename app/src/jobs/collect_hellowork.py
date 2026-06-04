"""
collect_hellowork.py — Collecte les offres HelloWork depuis les pages de listing publiques.

Les pages HelloWork sont server-rendered : requests + BeautifulSoup suffisent, pas de Selenium.
Enrichit chaque offre avec la description complète depuis la page détail.

Mode sans --append :
    Remplace uniquement les entrées platform=hellowork dans jobs_raw.json.
    Les offres WTTJ et Indeed sont préservées.

Mode --append :
    Ajoute uniquement les nouvelles URLs HelloWork (déduplication par URL).

Usage :
    python app/src/jobs/collect_hellowork.py
    python app/src/jobs/collect_hellowork.py --append
    python app/src/jobs/collect_hellowork.py --append --limit 5
"""

import argparse
import csv
import json
import logging
import os
import random
import re
import time
import yaml

import requests
from bs4 import BeautifulSoup

CONFIG_PATH   = "app/config/candidate_profile.yaml"
OUTPUT_PATH   = "app/data/jobs/jobs_raw.json"
TRACKING_PATH = "app/data/tracking.csv"
BASE_URL      = "https://www.hellowork.com"

MANUAL_STATUSES = {"ignored", "applied", "already_applied", "rejected_by_company", "manually_rejected"}

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xhtml+xml;q=0.9,*/*;q=0.8",
}

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config / I/O
# ---------------------------------------------------------------------------

def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_excluded_urls():
    """Return URLs that have a manual status in tracking.csv."""
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


def load_existing_jobs():
    try:
        with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_jobs(jobs):
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)


def make_session():
    s = requests.Session()
    s.headers.update(HTTP_HEADERS)
    return s


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _extract_salary(aria_label):
    """Extract salary string from aria-label attribute."""
    m = re.search(r"avec un salaire de (.+?)(?:,\s*en temps|,\s*T[ée]l|$)", aria_label)
    return m.group(1).strip() if m else ""


def _extract_date(card_text):
    """Extract publication date from serpCard text (e.g. 'il y a 2 jours')."""
    m = re.search(r"Voir l.offre\s*(.+)$", card_text.strip())
    return m.group(1).strip() if m else ""


# ---------------------------------------------------------------------------
# Listing page collection
# ---------------------------------------------------------------------------

def collect_listing_page(session, listing_url, page_num, seen_urls, remaining):
    """Collect job cards from one listing page.

    Returns (jobs, n_found) where n_found=0 signals end of pagination
    or a network error.
    """
    params = {"p": str(page_num)} if page_num > 1 else {}
    try:
        r = session.get(listing_url, params=params, timeout=15)
        if r.status_code != 200:
            logger.warning(f"  Page {page_num}: HTTP {r.status_code}")
            return [], 0
    except Exception as e:
        logger.warning(f"  Page {page_num}: erreur réseau: {e}")
        return [], 0

    soup = BeautifulSoup(r.text, "html.parser")
    cards = soup.find_all(attrs={"data-cy": "serpCard"})
    n_found = len(cards)

    if not cards:
        logger.info(f"  Page {page_num}: aucune offre — fin de pagination")
        return [], 0

    jobs = []

    for card in cards:
        if len(jobs) >= remaining:
            break

        link = card.find("a", attrs={"data-cy": "offerTitle"})
        if not link:
            continue

        href = link.get("href", "")
        id_match = re.search(r"/emplois/(\d+)\.html", href)
        if not id_match:
            continue

        job_url = f"{BASE_URL}{href}"

        if job_url in seen_urls:
            continue

        job_id = f"hellowork-{id_match.group(1)}"

        # Title + company from <h3> sub-elements
        title, company = "", ""
        h3 = link.find("h3")
        if h3:
            ps = h3.find_all("p")
            if ps:
                title = ps[0].get_text(strip=True)
            if len(ps) > 1:
                company = ps[1].get_text(strip=True)

        # Location / contract / telework via data-cy
        loc_el      = card.find(attrs={"data-cy": "localisationCard"})
        contract_el = card.find(attrs={"data-cy": "contractCard"})
        remote_el   = card.find(attrs={"data-cy": "contractTag"})

        location = loc_el.get_text(strip=True)      if loc_el      else ""
        contract = contract_el.get_text(strip=True) if contract_el else ""
        remote   = remote_el.get_text(strip=True)   if remote_el   else ""

        salary    = _extract_salary(link.get("aria-label", ""))
        published = _extract_date(card.get_text(" ", strip=True))

        jobs.append({
            "id":          job_id,
            "platform":    "hellowork",
            "title":       title,
            "company":     company,
            "location":    location,
            "url":         job_url,
            "contract":    contract,
            "salary":      salary,
            "remote":      remote,
            "published":   published,
            "description": "",
        })
        seen_urls.add(job_url)

    return jobs, n_found


# ---------------------------------------------------------------------------
# Description enrichment (detail page)
# ---------------------------------------------------------------------------

_DESC_HEADINGS = re.compile(
    r"(?:D[eé]tail du poste|Les missions du poste|Description de l.offre|Description du poste)\s*",
    re.IGNORECASE,
)


def fetch_description(session, url):
    """Fetch full job description from the HelloWork detail page.

    Tries each <section> containing a known description heading.
    Falls back to the largest non-navigation section if none match.
    Returns empty string on failure.
    """
    try:
        r = session.get(url, timeout=15)
        if r.status_code != 200:
            return ""
    except Exception as e:
        logger.warning(f"    Erreur description {url}: {e}")
        return ""

    soup = BeautifulSoup(r.text, "html.parser")

    # Strategy 1: section containing a known description heading
    for section in soup.find_all("section"):
        text = section.get_text(" ", strip=True)
        if _DESC_HEADINGS.search(text) and len(text) >= 100:
            text = _DESC_HEADINGS.sub("", text, count=1).strip()
            return text[:5000]

    # Fallback: largest section with meaningful content (>200 chars, <15000)
    best = ""
    for section in soup.find_all("section"):
        text = section.get_text(" ", strip=True)
        if 200 <= len(text) < 15000 and len(text) > len(best):
            best = text

    return best[:5000] if best else ""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Collecte et enrichit les offres HelloWork."
    )
    parser.add_argument(
        "--append", action="store_true",
        help="Ajoute uniquement les nouvelles URLs (préserve les existantes).",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Limite le nombre total d'offres collectées (test).",
    )
    args = parser.parse_args()

    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        )

    config = load_config()
    hw_cfg = config.get("hellowork", {})

    if not hw_cfg.get("enabled", True):
        logger.info("[HelloWork] Désactivé dans la config (enabled: false). Arrêt.")
        return

    global_max  = hw_cfg.get("max_jobs", 20)
    search_urls = hw_cfg.get("search_urls", [])

    if not search_urls:
        logger.error("[HelloWork] Aucune search_url configurée dans candidate_profile.yaml.")
        return

    excluded_urls = load_excluded_urls()
    if excluded_urls:
        logger.info(f"[HelloWork] URLs exclues (statuts manuels tracking.csv) : {len(excluded_urls)}")

    existing_jobs = load_existing_jobs()

    if args.append:
        base_jobs = existing_jobs
        seen_urls = {j["url"] for j in existing_jobs} | excluded_urls
        logger.info(
            f"[HelloWork] Mode append — {len(existing_jobs)} offres existantes conservées"
        )
    else:
        base_jobs = [j for j in existing_jobs if j.get("platform") != "hellowork"]
        seen_urls = {j["url"] for j in base_jobs} | excluded_urls
        n_removed = len(existing_jobs) - len(base_jobs)
        if n_removed:
            logger.info(f"[HelloWork] {n_removed} ancienne(s) offre(s) hellowork supprimée(s)")
        logger.info(
            f"[HelloWork] Mode normal — {len(base_jobs)} offres non-hellowork préservées"
        )

    session     = make_session()
    collected   = []
    total_limit = args.limit if args.limit is not None else global_max

    for query in search_urls:
        label     = query.get("label", "")
        url       = query.get("url", "")
        query_max = min(query.get("max_jobs", 10), total_limit - len(collected))

        if query_max <= 0:
            logger.info(f"[HelloWork] Limite globale atteinte — skip '{label}'")
            break
        if not url:
            continue

        logger.info(f"[HelloWork] Collecte '{label}' — max {query_max} offres")

        page             = 1
        query_collected  = 0

        while query_collected < query_max:
            remaining = query_max - query_collected
            jobs, n_found = collect_listing_page(session, url, page, seen_urls, remaining)

            if n_found == 0:
                break

            collected.extend(jobs)
            query_collected += len(jobs)

            logger.info(
                f"  Page {page}: {n_found} offres vues, {len(jobs)} retenues"
                f" (query total: {query_collected}/{query_max})"
            )

            # Stop if all cards on the page were duplicates
            if len(jobs) == 0:
                logger.info(f"  Page {page}: toutes déjà connues — arrêt pagination")
                break

            # Less than 20 results = last page
            if n_found < 20:
                break

            page += 1
            time.sleep(random.uniform(1.0, 2.0))

        logger.info(f"[HelloWork] '{label}': {query_collected} offre(s) collectée(s)")

    if not collected:
        logger.info("[HelloWork] Aucune nouvelle offre collectée.")
        save_jobs(base_jobs)
        _print_summary(0, 0, 0, base_jobs)
        return

    # Enrichissement descriptions
    logger.info(f"[HelloWork] Enrichissement descriptions — {len(collected)} offre(s)...")
    enriched = 0
    errors   = 0

    for i, job in enumerate(collected):
        logger.info(f"  [{i + 1}/{len(collected)}] {job['url']}")
        desc = fetch_description(session, job["url"])
        if desc:
            job["description"] = desc
            enriched += 1
            logger.info(f"    {len(desc)} chars | '{desc[:60].strip()}...'")
        else:
            errors += 1
            logger.warning("    Pas de description extraite")
        time.sleep(random.uniform(1.0, 2.5))

    all_jobs = base_jobs + collected
    save_jobs(all_jobs)
    _print_summary(len(collected), enriched, errors, all_jobs)


def _print_summary(n_collected, enriched, errors, all_jobs):
    wttj_count    = sum(1 for j in all_jobs if j.get("platform") == "wttj")
    hw_count      = sum(1 for j in all_jobs if j.get("platform") == "hellowork")
    indeed_count  = sum(1 for j in all_jobs if j.get("platform") not in ("wttj", "hellowork"))

    print()
    print("=" * 55)
    print("  Résumé collecte HelloWork")
    print(f"  Nouvelles offres collectées : {n_collected}")
    print(f"  Descriptions enrichies      : {enriched}")
    print(f"  Erreurs description         : {errors}")
    print(f"  Total jobs_raw.json         : {len(all_jobs)}")
    if wttj_count:
        print(f"    wttj      : {wttj_count}")
    print(f"    hellowork : {hw_count}")
    if indeed_count:
        print(f"    indeed    : {indeed_count}")
    print("=" * 55)


if __name__ == "__main__":
    main()
