import argparse
import csv
import json
import logging
import random
import sys
import time
import yaml
from urllib.parse import urljoin, urlparse, parse_qs

from seleniumbase import SB

CONFIG_PATH = "app/config/candidate_profile.yaml"
OUTPUT_PATH = "app/data/jobs/jobs_raw.json"
TRACKING_PATH = "app/data/tracking.csv"
BASE_URL = "https://fr.indeed.com"

MANUAL_STATUSES = {"ignored", "applied", "already_applied", "rejected_by_company", "manually_rejected"}

logger = logging.getLogger(__name__)


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def human_delay():
    time.sleep(random.uniform(2, 4))


def load_existing_jobs(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_jobs(jobs):
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)


def load_excluded_urls(path):
    """Return URLs that have a manual status in tracking.csv (should not be re-collected)."""
    excluded = set()
    try:
        with open(path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("status", "").strip().lower() in MANUAL_STATUSES:
                    url = row.get("url", "").strip()
                    if url:
                        excluded.add(url)
    except FileNotFoundError:
        pass
    return excluded


def extract_jk(url):
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    values = qs.get("jk", [])
    return values[0].strip() if values else ""


def is_valid_jk(jk):
    if not jk:
        return False
    if jk in ("123456789abcdef0", "abcdef0123456789"):
        return False
    return len(jk) >= 10


LOGIN_WALL_MARKERS = [
    "pour voir plusieurs pages d'offres d'emploi",
    "créez un compte ou connectez-vous",
    "create an account or sign in",
]


def detect_login_wall(sb):
    try:
        page_text = sb.get_page_source().lower()
        return any(marker in page_text for marker in LOGIN_WALL_MARKERS)
    except Exception:
        return False


def collect_from_cards(sb, max_jobs, seen_urls):
    jobs = []

    card_selectors = [
        "[data-jk]",
        "[data-testid='slider_item']",
        ".job_seen_beacon",
        ".tapItem",
    ]

    cards = []
    for selector in card_selectors:
        try:
            found = sb.find_elements(selector)
            if found:
                cards = found
                logger.info(f"Cartes trouvées avec: {selector} -> {len(found)}")
                break
        except Exception:
            pass

    for card in cards:
        href = ""
        jk = ""

        try:
            jk = card.get_attribute("data-jk") or ""
        except Exception:
            pass

        if not jk:
            try:
                links = card.find_elements("css selector", "a")
                for link in links:
                    tmp = link.get_attribute("href")
                    if tmp and "/viewjob" in tmp:
                        href = tmp
                        break
            except Exception:
                pass
            if href:
                href = urljoin(BASE_URL, href)
                jk = extract_jk(href)

        if not is_valid_jk(jk):
            continue

        clean_url = f"{BASE_URL}/viewjob?jk={jk}"
        if clean_url in seen_urls:
            continue

        seen_urls.add(clean_url)
        jobs.append({
            "id": f"indeed-{jk}",
            "platform": "indeed",
            "company": "",
            "title": "",
            "location": "",
            "url": clean_url,
            "description": "",
        })

        logger.info(f"URL retenue: {clean_url}")

        if len(jobs) >= max_jobs:
            break

    return jobs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--page", type=int, default=0,
                        help="Page offset (0-based, adds &start=N*10 to query URLs)")
    parser.add_argument("--append", action="store_true",
                        help="Append to existing jobs_raw.json instead of resetting")
    args = parser.parse_args()

    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        )

    config = load_config()
    queries = config["indeed"]["search_queries"]

    excluded_urls = load_excluded_urls(TRACKING_PATH)
    if excluded_urls:
        logger.info(f"URLs exclues (statuts manuels) : {len(excluded_urls)}")

    if args.append:
        existing_jobs = load_existing_jobs(OUTPUT_PATH)
        seen_urls = {j["url"] for j in existing_jobs} | excluded_urls
        all_jobs = existing_jobs
    else:
        seen_urls = excluded_urls
        all_jobs = []

    page_offset = args.page * 10
    login_wall_hit = False

    with SB(uc=True, headless=False) as sb:
        for query in queries:
            base_url = query["url"]
            max_jobs = query["max_jobs"]
            label = query.get("label", base_url)

            url = base_url if page_offset == 0 else f"{base_url}&start={page_offset}"

            logger.info(f"Collecte: {label} page={args.page} (max {max_jobs}) — {url}")
            sb.open(url)
            human_delay()

            if detect_login_wall(sb):
                logger.warning(f"Indeed login wall detected ({label}) — arrêt de la pagination.")
                login_wall_hit = True
                break

            sb.scroll_to_bottom()
            human_delay()

            jobs = collect_from_cards(sb, max_jobs, seen_urls)

            all_jobs.extend(jobs)
            logger.info(f"{len(jobs)} nouvelles offres ({label})")

    save_jobs(all_jobs)
    logger.info(f"Total: {len(all_jobs)} offres sauvegardées dans {OUTPUT_PATH}")

    if login_wall_hit:
        sys.exit(2)


if __name__ == "__main__":
    main()
