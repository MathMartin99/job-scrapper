"""
collect_linkedin.py — Collecte les offres LinkedIn Jobs en mode interactif.

Mode interactif uniquement (V1) :
    - Ouvre Chrome sur la page de recherche LinkedIn Jobs configurée
    - L'utilisateur se connecte si nécessaire et règle la recherche
    - L'utilisateur appuie sur Entrée
    - Phase 1 : collecte les métadonnées des cards visibles
    - Phase 2 : pour chaque card, clic JS pur → vérification panneau → description.
                Fallback : navigation directe /jobs/view/{id}/

Usage :
    python app/src/jobs/collect_linkedin.py --interactive --append --limit 5
    python app/src/jobs/collect_linkedin.py --interactive --limit 3   (purge LinkedIn, recollecte)
"""

import argparse
import csv
import hashlib
import json
import logging
import os
import random
import re
import sys
import time
import unicodedata
import yaml

from seleniumbase import SB

CONFIG_PATH   = "app/config/candidate_profile.yaml"
OUTPUT_PATH   = "app/data/jobs/jobs_raw.json"
TRACKING_PATH = "app/data/tracking.csv"
PROFILE_DIR   = "app/data/chrome_profile/linkedin"

MANUAL_STATUSES = {"ignored", "applied", "already_applied", "rejected_by_company", "manually_rejected"}

LINKEDIN_JOB_ID_PATTERN = re.compile(r'/jobs/view/(\d+)')

BLOCK_MARKERS = [
    "verify you're a human",
    "déverrouillez votre compte",
    "something went wrong",
    "please complete the security check",
    "veuillez résoudre ce captcha",
    "security verification",
]

# ---------------------------------------------------------------------------
# Text parsing constants
# ---------------------------------------------------------------------------

# Sorted longest-first so replace() matches greedily
_CARD_NOISE_TOKENS = sorted([
    "Le délai d'évaluation des entreprises est généralement de 1 semaine",
    "Le délai d'évaluation des entreprises est généralement de 1 semaine",
    "Évaluation des candidatures en cours",
    "Candidature simplifiée",
    "Candidature simplifiee",
    "with verification",
    "Promu(e)",
    "Promu",
    "Consulté",
    "Consulte",
    "Sponsorisé",
    "Sponsorise",
    "Alumni",
    "Vu",
], key=len, reverse=True)

# Markers at which location text must be TRUNCATED (everything from the marker
# onwards is noise — replacing mid-string would leave trailing garbage).
_LOCATION_CLIP_AT = [
    "Au cours des",
    "il y a",
    "Republié",
    "Republication",
    "Le délai",
    "Évaluation des candidatures",
    "Candidature simplifi",
    "Promu(e)",
    "Promu",
    "Consulté",
    "Consulte",
    "Sponsorisé",
    "Sponsorise",
]

_CARD_NOISE_TIMESTAMP_RE = re.compile(
    r'il\s+y\s+a\s+\d+\s*\w+'
    r'|\d+\s*(?:day|week|month|hour|minute|jour|semaine|heure)s?\s*(?:ago)?'
    r'|\d+\s*(?:applicant|candidat)s?',
    re.IGNORECASE,
)

# String markers: location starts at (or before) the first match.
# Order doesn't matter — we take the minimum position.
_LOCATION_MARKERS = [
    # Work-mode badges (most specific — catch these first)
    "(Hybride)", "(Sur site)", "(Télétravail)", "(Remote)", "(On-site)", "(Hybrid)",
    # French administrative prefix
    "Ville de",
    # Compound suburb names (before their short forms to avoid short-form shadowing)
    "Levallois-Perret", "Boulogne-Billancourt", "Neuilly-sur-Seine",
    "Issy-les-Moulineaux", "Vélizy-Villacoublay",
    # Region names
    "Île-de-France", "Ile-de-France",
    # "périphérie" handles "Paris et périphérie"
    "périphérie",
    # Major cities
    "Paris", "Lyon", "Bordeaux", "Toulouse", "Marseille", "Nantes", "Rennes",
    "Montpellier", "Lille", "Strasbourg", "Grenoble",
    # IDF suburbs
    "Courbevoie", "Levallois", "Boulogne", "Neuilly", "Suresnes",
    "Issy", "Vélizy", "Saclay", "Massy", "Clichy",
    "Saint-Denis", "Châtillon", "Clamart", "Rueil", "Versailles",
    # Country (last resort — least specific)
    "France",
]

# Regex to detect CITY, Île-de-France  or  CITY, France patterns.
# The comma marks the boundary: the word before it is the last word of the city.
_LOCATION_REGION_RE = re.compile(
    r',\s*(?:Île-de-France|Ile-de-France|France)\b',
    re.IGNORECASE,
)

# Description section boundaries
_DESC_START_MARKERS = [
    "À propos de l'offre",
    "À propos du poste",
    "Description du poste",
    "Descriptif du poste",
    "About the job",
    "About the role",
    "Vos missions",
    "Missions",
]

_DESC_END_MARKERS = [
    "Voir plus",
    "Compétences",
    "Qualifications",
    "À propos de l'entreprise",
    "Alertes Emploi",
    "Offres similaires",
    "Signaler cette offre",
    "Postuler",
    "Candidater",
]

_PANEL_DESC_SELECTORS = [
    "#job-details",
    ".jobs-description__content",
    ".jobs-description-content__text",
    ".jobs-box__html-content",
    ".jobs-description",
    ".show-more-less-html__markup",
    "article.jobs-description__container",
    "div[class*='jobs-description']",
]

# ---------------------------------------------------------------------------
# Title pre-filter — skip obviously non-junior titles before enrichment
# ---------------------------------------------------------------------------

# Keywords are matched after full normalization (lower + strip accents + collapse
# punctuation).  Both title and each keyword are normalized at comparison time,
# so accented / unaccented variants are all caught by a single entry.
_LINKEDIN_TITLE_FILTER_KEYWORDS = [
    # Seniority
    "senior", "sénior",
    "expert", "experte",
    # Leadership
    "lead", "tech lead", "techlead",
    # Role levels
    "staff",
    "principal",
    "architect", "architecte",
    "responsable",
    "manager",
    "head of",
    "director", "directeur",
    # Experience markers
    "confirmé", "confirmée",
    "expérimenté", "expérimentée",
    "experienced",
    # Excluded contract types
    "freelance",
    "indépendant",
    "alternance", "alternant",
    "apprenti", "apprentice", "apprenticeship",
    "stage",
    "intern", "internship",
]


def _normalize_title_for_filter(text):
    """Lowercase, strip accents, replace punctuation with spaces, collapse whitespace."""
    text = text.lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _is_rejected_linkedin_title(title):
    """Return (True, matched_keyword) if title contains a blocking seniority keyword.

    Both title and each keyword are normalized before comparison so accent
    variants are handled transparently.  Word-boundary matching (space padding)
    prevents false positives on substrings like 'upload' matching 'lead'.
    """
    padded = " " + _normalize_title_for_filter(title) + " "
    for kw in _LINKEDIN_TITLE_FILTER_KEYWORDS:
        kw_norm = _normalize_title_for_filter(kw)
        if f" {kw_norm} " in padded:
            return True, kw
    return False, ""


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config / I/O
# ---------------------------------------------------------------------------

def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_excluded_urls():
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


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def human_delay(min_s=2.0, max_s=4.0):
    time.sleep(random.uniform(min_s, max_s))


def wait_for_user(msg):
    print()
    print(msg)
    try:
        input("  → Appuie sur Entrée pour continuer (Ctrl+C pour annuler) : ")
        return True
    except (EOFError, KeyboardInterrupt):
        return False


def _wait_for_linkedin_auto(sb, timeout, interval):
    """Poll until LinkedIn job cards appear in DOM or timeout expires.

    Returns "ok" when cards are detected, "timeout" on expiry, "blocked" on CAPTCHA.
    """
    import time as _time
    deadline = _time.time() + timeout
    attempt = 0
    while _time.time() < deadline:
        attempt += 1
        _time.sleep(interval)
        if detect_block(sb):
            logger.warning("[auto-confirm] LinkedIn : blocage / CAPTCHA détecté.")
            return "blocked"
        if detect_login_wall(sb):
            logger.info(f"[auto-confirm] Tentative {attempt} — login wall, attente connexion...")
            continue
        n_cards = count_job_cards_in_dom(sb)
        logger.info(f"[auto-confirm] Tentative {attempt} — {n_cards} card(s) dans le DOM")
        if n_cards > 0:
            logger.info(f"[auto-confirm] Session LinkedIn détectée ({n_cards} cards). Collecte...")
            return "ok"
    logger.warning(f"[auto-confirm] Timeout {timeout}s dépassé sans détection de session LinkedIn.")
    return "timeout"


def _make_job_id(job_id_numeric, title, company, url):
    if job_id_numeric:
        return f"linkedin-{job_id_numeric}"
    raw = f"{title}|{company}|{url}".lower()
    return f"linkedin-{hashlib.md5(raw.encode()).hexdigest()[:12]}"


def _clean_url(url):
    m = LINKEDIN_JOB_ID_PATTERN.search(url)
    if m:
        return f"https://www.linkedin.com/jobs/view/{m.group(1)}/"
    return url.split("?")[0].rstrip("/") + "/"


# ---------------------------------------------------------------------------
# Card text parsing
# ---------------------------------------------------------------------------

def _find_location_split(text):
    """Split 'COMPANY LOCATION' text by finding the earliest location boundary.

    Two strategies run in parallel; we take the minimum (earliest) position:
      A) String markers in _LOCATION_MARKERS
      B) Regex `, Île-de-France` / `, France` — the city word before the comma
         is part of the location, so we scan back from the comma to find its start.

    Returns (company, location). Either may be "" if no split found.
    """
    if not text:
        return "", ""

    low = text.lower()
    best_pos = len(text)

    # A) String markers
    for marker in _LOCATION_MARKERS:
        pos = low.find(marker.lower())
        if 0 < pos < best_pos:
            best_pos = pos

    # B) Regex: CITY, (Île-de-France|France)
    m = _LOCATION_REGION_RE.search(text)
    if m:
        comma_pos = m.start()                    # position of the comma
        city_start = comma_pos
        while city_start > 0 and text[city_start - 1] not in (" ", "\t"):
            city_start -= 1
        if 0 < city_start < best_pos:
            best_pos = city_start

    if best_pos == len(text):
        return "", text

    # Scan back to the word boundary so we don't cut mid-word
    loc_start = best_pos
    while loc_start > 0 and text[loc_start - 1] not in (" ", "\t"):
        loc_start -= 1

    company  = text[:loc_start].strip()
    location = text[loc_start:].strip()
    return company, location


def _strip_card_noise(text):
    """Remove badge/noise tokens and timestamps from card text."""
    for noise in _CARD_NOISE_TOKENS:
        text = text.replace(noise, " ")
    text = _CARD_NOISE_TIMESTAMP_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _clip_location(text):
    """Truncate location at the first noise marker.

    Handles cases where the card crops the noise string mid-sentence
    (e.g. 'Paris (Sur site) Le délai' → 'Paris (Sur site)').
    """
    low = text.lower()
    clip = len(text)
    for marker in _LOCATION_CLIP_AT:
        pos = low.find(marker.lower())
        if 0 < pos < clip:
            clip = pos
    return text[:clip].strip()


def _parse_linkedin_compact_card_text(raw):
    """Parse LinkedIn card innerText when all content is packed into one string.

    LinkedIn card format:
        TITLE TITLE [with verification] COMPANY LOCATION [NOISE]

    Returns (title, company, location) — any field may be "" on failure.
    """
    raw = raw.strip()
    if not raw:
        return "", "", ""

    # Step 1: detect repeated title (prefix + " " + same prefix)
    title = ""
    rest  = raw
    half  = len(raw) // 2

    for n in range(half, 2, -1):
        prefix = raw[:n]
        if raw[n : n + 1] == " " and raw[n + 1 : n + 1 + n] == prefix:
            title = prefix
            rest  = raw[n + 1 + n :].strip()
            break

    if not title:
        return "", "", ""

    # Step 2: strip noise
    rest = _strip_card_noise(rest)

    # Step 3: split company | location
    company, location = _find_location_split(rest)

    if not company:
        company  = rest
        location = ""

    location = _strip_card_noise(location)
    location = _clip_location(location)
    return title, company, location


def _parse_card_text_lines(raw):
    """Fallback: multi-line card text → line 0=title, 1=company, 2=location."""
    lines = [l.strip() for l in raw.split("\n") if l.strip()]
    clean = []
    for line in lines:
        low = line.lower()
        if any(low == t.lower() for t in _CARD_NOISE_TOKENS):
            continue
        if _CARD_NOISE_TIMESTAMP_RE.match(low):
            continue
        if len(line) < 2:
            continue
        clean.append(line)

    title    = clean[0] if len(clean) > 0 else ""
    company  = clean[1] if len(clean) > 1 else ""
    location = clean[2] if len(clean) > 2 else ""
    return title, company, location


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def detect_login_wall(sb):
    try:
        url = sb.get_current_url().lower()
        return any(m in url for m in ["/authwall", "/login", "/checkpoint/"])
    except Exception:
        return False


def detect_block(sb):
    try:
        source = sb.get_page_source().lower()
        return any(m in source for m in BLOCK_MARKERS)
    except Exception:
        return False


def count_job_cards_in_dom(sb):
    for selector in [
        "li[data-occludable-job-id]",
        "li[data-job-id]",
        ".jobs-search__results-list li",
        "div.job-card-container",
    ]:
        try:
            cards = sb.find_elements(selector)
            if cards:
                return len(cards)
        except Exception:
            pass
    return 0


# ---------------------------------------------------------------------------
# Metadata extraction from card element
# ---------------------------------------------------------------------------

def _extract_from_card(card, job_url):
    """Extract title, company, location from a live card WebElement.

    Priority:
      1. CSS selectors (multiple LinkedIn layout variants)
      2. Compact innerText parser (title-repeated format)
      3. Line-based innerText parser (multiline fallback)
      4. aria-label of the job link
    """
    title, company, location = "", "", ""
    selector_used = "none"

    # --- CSS: title ---
    for sel in [
        ".artdeco-entity-lockup__title",
        ".job-card-list__title--link",
        ".job-card-list__title",
        "a.job-card-list__title",
        ".job-card-container__link",
        "strong",
        "h3",
        "h4",
    ]:
        try:
            el = card.find_element("css selector", sel)
            text = el.text.strip() if el is not None else ""
            if text:
                title = text
                selector_used = f"CSS title '{sel}'"
                break
        except Exception:
            pass

    # --- CSS: company ---
    for sel in [
        ".artdeco-entity-lockup__subtitle",
        ".job-card-container__primary-description",
        ".job-card-container__company-name",
        ".job-card-list__company-name",
        "h4.base-search-card__subtitle",
        "a.job-card-container__company-name",
    ]:
        try:
            el = card.find_element("css selector", sel)
            text = el.text.strip() if el is not None else ""
            if text:
                company = text
                break
        except Exception:
            pass

    # --- CSS: location ---
    for sel in [
        ".artdeco-entity-lockup__metadata li",
        ".job-card-container__metadata-item",
        "li.job-card-container__metadata-item",
        ".base-search-card__metadata",
        "li[class*='metadata']",
    ]:
        try:
            el = card.find_element("css selector", sel)
            text = el.text.strip() if el is not None else ""
            if text:
                location = text
                break
        except Exception:
            pass

    # --- Compact innerText parser (primary fallback) ---
    if not title or not company:
        try:
            raw = card.text or ""
            if raw.strip():
                t, c, l = _parse_linkedin_compact_card_text(raw)
                if not title and t:
                    title = t
                    selector_used = "compact innerText"
                if not company and c:
                    company = c
                if not location and l:
                    location = l
        except Exception:
            pass

    # --- Line-based innerText (secondary fallback) ---
    if not title or not company:
        try:
            raw = card.text or ""
            if raw.strip():
                t, c, l = _parse_card_text_lines(raw)
                if not title and t:
                    title = t
                    selector_used = "line-based innerText"
                if not company and c:
                    company = c
                if not location and l:
                    location = l
        except Exception:
            pass

    # --- aria-label fallback for title ---
    if not title:
        try:
            link = card.find_element("css selector", "a[href*='/jobs/view/']")
            if link is not None:
                aria = link.get_attribute("aria-label") or ""
                if aria:
                    title = aria.split(" at ")[0].strip() if " at " in aria else aria.strip()
                    selector_used = "aria-label"
        except Exception:
            pass

    if not title or not company:
        logger.warning(f"  title/company vides (sélecteur: {selector_used}) — {job_url}")

    return title, company, location


# ---------------------------------------------------------------------------
# Panel click — pure JavaScript, no Python WebElement references
# ---------------------------------------------------------------------------

def _click_job_card_by_id(sb, job_id):
    """Click the job card link for a given LinkedIn job ID using pure JavaScript.

    Never stores or dereferences a Python WebElement — all DOM access happens
    inside the JS execution context.  Returns True on success, False on failure.
    """
    jid = str(job_id)
    js = f"""(() => {{
  const selectors = [
    'li[data-occludable-job-id="{jid}"] a[href*="/jobs/view/"]',
    'li[data-job-id="{jid}"] a[href*="/jobs/view/"]',
    'a[href*="/jobs/view/{jid}/"]',
    'a[href*="/jobs/view/{jid}?"]'
  ];
  for (const sel of selectors) {{
    const el = document.querySelector(sel);
    if (el) {{
      el.scrollIntoView({{block: 'center'}});
      el.click();
      return sel;
    }}
  }}
  return null;
}})()"""
    try:
        result = sb.execute_script(js)
        if result:
            logger.info(f"  [panel-click] OK job_id={job_id} via JS '{result}'")
            return True
        logger.warning(f"  [panel-click] failed job_id={job_id} — aucun sélecteur JS ne matche")
        return False
    except Exception as e:
        logger.warning(f"  [panel-click] Erreur JS pour job_id={job_id} : {e}")
        return False


# ---------------------------------------------------------------------------
# Panel verification — ensure the active panel matches the requested job
# ---------------------------------------------------------------------------

def _verify_panel_matches_job(sb, job_id):
    """Return True if the currently visible panel is for the requested job_id.

    Checks:
      1. currentJobId={job_id} in window.location.href (most reliable)
      2. Presence of a link /jobs/view/{job_id}/ anywhere in the DOM
    """
    try:
        current_url = sb.get_current_url()
        if f"currentJobId={job_id}" in current_url:
            logger.info(f"  [panel-check] OK — currentJobId={job_id} dans URL")
            return True
        if f"/jobs/view/{job_id}/" in current_url:
            logger.info(f"  [panel-check] OK — /jobs/view/{job_id}/ dans URL")
            return True

        # Fallback: check DOM for a link to this job (panel card header, etc.)
        result = sb.execute_script(
            f"return !!document.querySelector('a[href*=\"/jobs/view/{job_id}/\"]');"
        )
        if result:
            logger.info(f"  [panel-check] OK — lien /jobs/view/{job_id}/ dans le DOM")
            return True

        logger.warning(
            f"  [panel-check] FAIL — ni currentJobId={job_id} dans l'URL "
            f"ni lien dans le DOM (URL courante : {current_url[:80]})"
        )
        return False
    except Exception as e:
        logger.warning(f"  [panel-check] Erreur : {e}")
        return False


# ---------------------------------------------------------------------------
# Description extraction
# ---------------------------------------------------------------------------

def _extract_description_from_body_text(sb):
    """Extract description from body innerText using section start/end markers.

    Returns description (>100 chars) or "".
    """
    try:
        body = sb.execute_script("return document.body ? document.body.innerText : '';") or ""
        if not body:
            return ""

        body_low = body.lower()
        start_pos = -1

        for marker in _DESC_START_MARKERS:
            pos = body_low.find(marker.lower())
            if pos != -1:
                candidate = pos + len(marker)
                if start_pos == -1 or candidate < start_pos:
                    start_pos = candidate

        if start_pos == -1:
            return ""

        desc = body[start_pos:].strip()
        for end_marker in _DESC_END_MARKERS:
            idx = desc.lower().find(end_marker.lower())
            if idx != -1:
                desc = desc[:idx].strip()

        return desc[:5000] if len(desc) > 100 else ""

    except Exception as e:
        logger.warning(f"  [body] Erreur extraction body text : {e}")
        return ""


def _extract_description_from_panel(sb, job_id, job_url):
    """Click the card for job_id via JS, verify the panel loaded, then extract description.

    Returns description text or "" (caller will use direct-URL fallback).
    """
    # Step 1: click via pure JS — no Python WebElement
    if not _click_job_card_by_id(sb, job_id):
        return ""

    # Step 2: wait for LinkedIn to update the right panel
    human_delay(3.0, 5.0)

    # Step 3: verify the panel is for THIS job — guard against stale panel content
    if not _verify_panel_matches_job(sb, job_id):
        logger.warning(f"  [panel] Panneau non vérifié pour job_id={job_id} — abandon")
        return ""

    # Step 4: CSS selectors on the panel
    for sel in _PANEL_DESC_SELECTORS:
        try:
            el = sb.find_element(sel)
            if el is None:
                continue
            text = el.text.strip()
            if text and len(text) > 50:
                logger.info(f"  [panel] Description via '{sel}' : {len(text)} chars")
                return text[:5000]
        except Exception:
            pass

    # Step 5: JS fallback on panel selectors
    try:
        sels_js = ", ".join(f"'{s}'" for s in _PANEL_DESC_SELECTORS[:5])
        text = sb.execute_script(
            f"const sels = [{sels_js}];"
            "for (const s of sels) {"
            "  const el = document.querySelector(s);"
            "  if (el && el.innerText && el.innerText.trim().length > 50)"
            "    return el.innerText.trim();"
            "}"
            "return '';"
        )
        if text and len(text.strip()) > 50:
            logger.info(f"  [panel] Description via JS : {len(text)} chars")
            return text.strip()[:5000]
    except Exception:
        pass

    # Step 6: body-text marker fallback (safe — panel already verified for this job_id)
    desc = _extract_description_from_body_text(sb)
    if desc:
        logger.info(f"  [panel-body] Description via marqueurs : {len(desc)} chars")
        return desc

    logger.warning(f"  [panel] Tous sélecteurs échoués — description vide pour {job_url}")

    return ""


def _fetch_description_direct(sb, url):
    """Navigate directly to /jobs/view/{id}/ as last-resort fallback.

    The URL itself is the job verification — no further check needed.
    """
    try:
        sb.open(url)
        human_delay(3.0, 5.0)

        if detect_login_wall(sb):
            logger.warning(f"  [direct] Login wall : {url}")
            return ""
        if detect_block(sb):
            logger.warning(f"  [direct] Blocage : {url}")
            return ""

        # CSS selectors
        for sel in _PANEL_DESC_SELECTORS + ["div[class*='description']"]:
            try:
                el = sb.find_element(sel)
                if el is None:
                    continue
                text = el.text.strip()
                if text and len(text) > 50:
                    logger.info(f"  [direct] Description via '{sel}' : {len(text)} chars")
                    return text[:5000]
            except Exception:
                pass

        # JS fallback
        try:
            sels_js = ", ".join(f"'{s}'" for s in _PANEL_DESC_SELECTORS[:5])
            text = sb.execute_script(
                f"const sels = [{sels_js}];"
                "for (const s of sels) {"
                "  const el = document.querySelector(s);"
                "  if (el && el.innerText && el.innerText.trim().length > 50)"
                "    return el.innerText.trim();"
                "}"
                "return '';"
            )
            if text and len(text.strip()) > 50:
                logger.info(f"  [direct] Description via JS : {len(text)} chars")
                return text.strip()[:5000]
        except Exception:
            pass

        # Body-text marker fallback
        desc = _extract_description_from_body_text(sb)
        if desc:
            logger.info(f"  [direct] Description via body fallback : {len(desc)} chars")
            return desc

        logger.warning(f"  [direct] Tous sélecteurs échoués pour {url}")
        return ""

    except Exception as e:
        logger.warning(f"  [direct] Erreur {url} : {e}")
        return ""


# ---------------------------------------------------------------------------
# Phase 1 + Phase 2 collection
# ---------------------------------------------------------------------------

def collect_job_cards(sb, seen_urls, limit):
    """Collect LinkedIn job listings from the current search page.

    Phase 1: extract metadata (title, company, location, url) from visible cards.
             Jobs with empty title OR company are skipped immediately.
    Phase 2: for each retained job, click via pure JS → verify panel → extract
             description. Fallback: direct /jobs/view/{id}/ navigation.

    Returns (jobs, stats).
    """
    jobs  = []
    stats = {"found": 0, "skipped_seen": 0, "skipped_no_url": 0, "skipped_incomplete": 0, "skipped_title_filter": 0}

    # Wait for SPA rendering + lazy-load
    time.sleep(5)
    sb.scroll_to_bottom()
    time.sleep(3)
    sb.scroll_to_top()
    time.sleep(1)

    search_page_url = sb.get_current_url()

    # --- Find card elements ---
    cards = []
    for selector in [
        "li[data-occludable-job-id]",
        "li[data-job-id]",
        ".jobs-search__results-list li",
        "div.job-card-container",
    ]:
        try:
            found = sb.find_elements(selector)
            if found:
                cards = found
                logger.info(f"Cards trouvées avec '{selector}' : {len(found)}")
                break
        except Exception:
            pass

    if not cards:
        logger.warning("Aucune card offre trouvée sur la page.")
        return jobs, stats

    # -----------------------------------------------------------------------
    # Phase 1: metadata
    # Store (job_id_numeric_str, job_dict) — NOT the WebElement (becomes stale).
    # -----------------------------------------------------------------------
    candidates = []
    seen_on_page = set()

    for card in cards:
        if len(candidates) >= limit:
            break

        # Job ID from data attribute
        job_id_numeric = ""
        for attr in ("data-occludable-job-id", "data-job-id"):
            try:
                val = (card.get_attribute(attr) or "").strip()
                if val.isdigit():
                    job_id_numeric = val
                    break
            except Exception:
                pass

        # URL from title link
        url = ""
        try:
            link = card.find_element("css selector", "a[href*='/jobs/view/']")
            if link is not None:
                url = link.get_attribute("href") or ""
        except Exception:
            pass

        if not url:
            try:
                outer = card.get_attribute("outerHTML") or ""
                m = re.search(r'href="([^"]*?/jobs/view/\d+[^"]*?)"', outer)
                if m:
                    url = m.group(1)
            except Exception:
                pass

        if not url:
            stats["skipped_no_url"] += 1
            continue

        if not job_id_numeric:
            m = LINKEDIN_JOB_ID_PATTERN.search(url)
            if m:
                job_id_numeric = m.group(1)

        clean = _clean_url(url)

        if clean in seen_on_page:
            continue
        seen_on_page.add(clean)
        stats["found"] += 1

        if clean in seen_urls:
            stats["skipped_seen"] += 1
            continue

        title, company, location = _extract_from_card(card, clean)

        if not title or not company:
            stats["skipped_incomplete"] += 1
            logger.warning(f"  Skipped (title ou company vide) : {clean}")
            continue

        rejected, kw = _is_rejected_linkedin_title(title)
        if rejected:
            stats["skipped_title_filter"] += 1
            logger.info(f"  Skipped title filter LinkedIn : {kw} | {title} | {clean}")
            continue

        job_id = _make_job_id(job_id_numeric, title, company, clean)
        job = {
            "id":          job_id,
            "platform":    "linkedin",
            "title":       title,
            "company":     company,
            "location":    location,
            "url":         clean,
            "description": "",
        }
        seen_urls.add(clean)
        logger.info(
            f"URL retenue : {clean} | "
            f"title='{title[:50]}' | company='{company[:30]}' | location='{location[:40]}'"
        )
        candidates.append((job_id_numeric, job))

    if not candidates:
        return jobs, stats

    # -----------------------------------------------------------------------
    # Phase 2: description enrichment
    # Pure-JS click → panel verification → extract.
    # No Python WebElement reuse — all DOM access via execute_script.
    # -----------------------------------------------------------------------
    logger.info(f"Enrichissement descriptions — {len(candidates)} offre(s)...")
    navigated_away = False

    for i, (job_id_numeric, job) in enumerate(candidates):
        logger.info(f"  [{i + 1}/{len(candidates)}] {job['url']}")

        # Re-navigate to search page if a previous direct-URL fallback navigated away
        if navigated_away:
            logger.info("  Re-navigation vers la page de recherche...")
            sb.open(search_page_url)
            time.sleep(3)
            navigated_away = False

        # A) Panel approach: click via JS + verify + extract
        desc = _extract_description_from_panel(sb, job_id_numeric, job["url"])

        # B) Direct URL fallback if panel failed or was unverified
        if not desc:
            logger.info(f"  [fallback] Navigation directe vers {job['url']}")
            desc = _fetch_description_direct(sb, job["url"])
            navigated_away = True

        if desc:
            job["description"] = desc
            logger.info(f"    {len(desc)} chars | '{desc[:60].strip()}...'")
        else:
            logger.warning(f"  description_empty : {job['url']}")

        jobs.append(job)
        human_delay(2.0, 4.0)

    return jobs, stats


# ---------------------------------------------------------------------------
# Interactive flow
# ---------------------------------------------------------------------------

def run_interactive(sb, search_urls, limit, seen_urls, all_jobs,
                    auto_confirm=False, auth_timeout=180, auth_poll_interval=2):
    """Open LinkedIn Jobs, wait for manual login, collect and enrich jobs.

    Returns:
        int   → number of jobs added
        False → cancelled by user (Ctrl+C) or auto-confirm timeout
        None  → CAPTCHA/block detected → caller exits with code 2
    """
    start_url = search_urls[0]["url"] if search_urls else "https://www.linkedin.com/jobs/"

    logger.info(f"Ouverture de : {start_url}")
    sb.open(start_url)
    time.sleep(3)

    if auto_confirm:
        logger.info(f"[auto-confirm] Attente session LinkedIn (timeout={auth_timeout}s, interval={auth_poll_interval}s)...")
        status = _wait_for_linkedin_auto(sb, auth_timeout, auth_poll_interval)
        if status == "blocked":
            return None
        if status == "timeout":
            logger.warning("[auto-confirm] Timeout LinkedIn — aucune session détectée, arrêt.")
            return False
        # status == "ok" → proceed to collection
    else:
        print()
        print("=" * 65)
        print("  Chrome est ouvert sur LinkedIn Jobs.")
        print()
        print("  Etapes :")
        print("  1. Accepte les cookies si une banniere apparait")
        print("  2. Connecte-toi a ton compte LinkedIn si necessaire")
        print("  3. Verifie que des resultats de recherche s'affichent")
        print("  4. Ajuste la recherche si besoin (mots-cles, localisation)")
        print("  5. Reviens ici et appuie sur Entree")
        print()
        print("  Ne ferme PAS Chrome.")
        print("=" * 65)

        ok = wait_for_user("  Pret ? Appuie sur Entree pour lancer la verification")
        if not ok:
            logger.info("Mode interactif annule.")
            return False

        attempt = 0
        while True:
            attempt += 1
            time.sleep(2)

            current_url = sb.get_current_url()
            on_login    = detect_login_wall(sb)
            on_block    = detect_block(sb)
            n_cards     = count_job_cards_in_dom(sb)

            print()
            print(f"  [Verification {attempt}]")
            print(f"  URL actuelle      : {current_url[:90]}")
            print(f"  Login wall        : {'OUI' if on_login else 'non'}")
            print(f"  Blocage / CAPTCHA : {'OUI' if on_block else 'non'}")
            print(f"  Cards offres      : {n_cards} detectee(s) dans le DOM")

            if on_block:
                print()
                print("  ARRET : LinkedIn affiche une page de verification ou CAPTCHA.")
                print("  Resoudre le CAPTCHA dans le navigateur, puis relancer le script.")
                logger.error("Blocage LinkedIn detecte — arret.")
                return None

            if on_login:
                print()
                print("  La page de connexion est affichee.")
                print("  Connecte-toi a LinkedIn dans le navigateur, puis reviens ici.")
                ok = wait_for_user("  Connecte ? Appuie sur Entree")
                if not ok:
                    return False
                continue

            if n_cards > 0:
                print()
                print(f"  Session valide - {n_cards} offre(s) visible(s). Lancement de la collecte...")
                logger.info(f"LinkedIn verifie ({n_cards} cards). Collecte en cours.")
                break

            print()
            print("  Aucune card detectee. Causes possibles :")
            print("    - La page n'a pas fini de charger")
            print("    - Tu n'es pas sur une page de resultats LinkedIn Jobs")
            print("  Navigue vers une recherche LinkedIn Jobs avec des resultats visibles.")
            ok = wait_for_user("  Quand des offres sont visibles, appuie sur Entree")
            if not ok:
                return False

    jobs, stats = collect_job_cards(sb, seen_urls, limit)
    logger.info(
        f"Collecte terminée : {stats['found']} vues, "
        f"{stats['skipped_seen']} déjà connues, "
        f"{stats['skipped_no_url']} sans URL, "
        f"{stats['skipped_incomplete']} incomplètes, "
        f"{stats.get('skipped_title_filter', 0)} filtrées titre, "
        f"{len(jobs)} retenues"
    )

    enriched = sum(1 for j in jobs if j.get("description"))
    errors   = len(jobs) - enriched
    all_jobs.extend(jobs)
    _print_summary(stats, enriched, errors, all_jobs)
    return len(jobs)


def _print_summary(stats, enriched, errors, all_jobs):
    li_count   = sum(1 for j in all_jobs if j.get("platform") == "linkedin")
    wttj_count = sum(1 for j in all_jobs if j.get("platform") == "wttj")
    hw_count   = sum(1 for j in all_jobs if j.get("platform") == "hellowork")
    in_count   = sum(1 for j in all_jobs if j.get("platform") == "indeed")

    print()
    print("=" * 55)
    print("  Résumé collecte LinkedIn")
    print(f"  Cards vues              : {stats.get('found', 0)}")
    print(f"  Déjà connues            : {stats.get('skipped_seen', 0)}")
    print(f"  Sans URL                : {stats.get('skipped_no_url', 0)}")
    print(f"  Incomplètes (ignorées)  : {stats.get('skipped_incomplete', 0)}")
    print(f"  Titres filtrés          : {stats.get('skipped_title_filter', 0)}")
    print(f"  Descriptions ok         : {enriched}")
    print(f"  Erreurs description     : {errors}")
    print(f"  Total jobs_raw.json     : {len(all_jobs)}")
    if wttj_count:
        print(f"    wttj      : {wttj_count}")
    if hw_count:
        print(f"    hellowork : {hw_count}")
    if in_count:
        print(f"    indeed    : {in_count}")
    print(f"    linkedin  : {li_count}")
    print("=" * 55)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Collecte les offres LinkedIn Jobs en mode interactif (V1)."
    )
    parser.add_argument("--interactive", action="store_true",
                        help="Ouvre Chrome, attend la connexion manuelle, puis collecte.")
    parser.add_argument("--append", action="store_true",
                        help="Ajoute uniquement les nouvelles URLs (préserve les existantes).")
    parser.add_argument("--limit", type=int, default=10,
                        help="Nombre maximum d'offres à collecter (défaut: 10).")
    parser.add_argument("--auto-confirm", action="store_true",
                        help="Poll for session readiness instead of blocking on input().")
    parser.add_argument("--auth-timeout", type=int, default=180,
                        help="Seconds to wait for LinkedIn session in auto-confirm mode.")
    parser.add_argument("--auth-poll-interval", type=int, default=2,
                        help="Polling interval in seconds for auto-confirm mode.")
    args = parser.parse_args()

    if not args.interactive:
        print("Erreur : --interactive requis pour LinkedIn V1.")
        print("Usage : python app/src/jobs/collect_linkedin.py --interactive --append --limit 5")
        sys.exit(1)

    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        )

    config      = load_config()
    li_cfg      = config.get("linkedin", {})
    search_urls = li_cfg.get("search_urls", [])

    if not search_urls:
        logger.warning(
            "Aucune search_url LinkedIn configurée dans candidate_profile.yaml. "
            "Chrome s'ouvrira sur linkedin.com/jobs — navigue manuellement."
        )
        search_urls = [{"label": "LinkedIn Jobs", "url": "https://www.linkedin.com/jobs/"}]

    excluded_urls = load_excluded_urls()
    if excluded_urls:
        logger.info(f"URLs exclues (tracking.csv, statuts manuels) : {len(excluded_urls)}")

    if args.append:
        existing_jobs = load_existing_jobs()
        seen_urls     = {j["url"] for j in existing_jobs} | excluded_urls
        all_jobs      = existing_jobs
        logger.info(f"Mode append — {len(existing_jobs)} offres existantes conservées")
    else:
        existing_jobs = load_existing_jobs()
        base_jobs     = [j for j in existing_jobs if j.get("platform") != "linkedin"]
        seen_urls     = {j["url"] for j in base_jobs} | excluded_urls
        all_jobs      = base_jobs
        n_removed     = len(existing_jobs) - len(base_jobs)
        if n_removed:
            logger.info(f"{n_removed} ancienne(s) offre(s) linkedin supprimée(s)")
        logger.info(f"Mode normal — {len(base_jobs)} offres non-linkedin préservées")

    os.makedirs(PROFILE_DIR, exist_ok=True)

    with SB(uc=True, headless=False, user_data_dir=PROFILE_DIR) as sb:
        result = run_interactive(
            sb, search_urls, args.limit, seen_urls, all_jobs,
            auto_confirm=args.auto_confirm,
            auth_timeout=args.auth_timeout,
            auth_poll_interval=args.auth_poll_interval,
        )

    save_jobs(all_jobs)
    logger.info(f"Total : {len(all_jobs)} offres sauvegardées dans {OUTPUT_PATH}")

    if result is None:
        logger.error("Blocage LinkedIn (CAPTCHA ou page de vérification) — arrêt.")
        sys.exit(2)
    if result is False:
        logger.info("Collecte LinkedIn annulée par l'utilisateur.")
        sys.exit(0)


if __name__ == "__main__":
    main()
