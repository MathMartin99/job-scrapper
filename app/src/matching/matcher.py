import csv
import json
import os
import re
import unicodedata
import yaml

PROFILE_PATH = "app/config/candidate_profile.yaml"
JOBS_PATH = "app/data/jobs/jobs_raw.json"
TRACKING_PATH = "app/data/tracking.csv"
SELECTED_JOBS_PATH = "app/data/jobs/selected_jobs.json"
MIN_SCORE = 40

MANUAL_STATUSES = {"ignored", "applied", "already_applied", "rejected_by_company", "manually_rejected"}

HEADERS = ["job_id", "platform", "company", "title", "location", "url", "score", "status", "applied_at", "notes"]

TITLE_EXCLUDE_KEYWORDS = [
    "freelance",
    "architecte",
    "architect",
    "stage",
    "internship",
    "alternance",
    "alternant",
    "apprenti",
    "formation"
]

# Normalized (accent-free) forms matched via _norm() + word-boundary regex
SENIOR_TITLE_KEYWORDS = [
    "senior",
    "expert",
    "lead",
    "tech lead",
    "techlead",
    "principal",
    "staff",
    "manager",
    "confirme",
    "confirmee",
    "experimente",
    "experienced",
    "head of",
    "responsable",
    "directeur",
    "director",
]

# Range patterns: two numbers (lower, upper). Applied before min patterns.
# Thresholds: lower <= 1 → ok | lower == 2 → borderline | lower >= 3 → too_high
_RANGE_PATTERNS = [
    # French
    r'(\d+)\s+a\s+(\d+)\s+an',                            # "2 à 3 ans" → "2 a 3 ans"
    r'entre\s+(\d+)\s+et\s+(\d+)\s+an',                   # "entre 2 et 3 ans"
    r'(\d+)\s+(\d+)\s+an\w*\b',                            # "2-3 ans" → "2 3 ans"
    # English
    r'(\d+)\s+to\s+(\d+)\s+year',                         # "2 to 3 years"
    r'(\d+)\s+and\s+(\d+)\s+year',                        # "2 and 3 years"
    r'(\d+)\s+(\d+)\s+year\w*\b',                          # "2-3 years" → "2 3 years"
]

# Minimum/explicit patterns: single number = the minimum required.
# Thresholds: year <= 2 → ok | year >= 3 → too_high  (no borderline)
# Leading-digit patterns use (?<!\d ) to avoid capturing the upper bound of a range
# (after _norm() all spaces are collapsed to one space, so "2 3 ans" → digit-space-digit).
_MIN_PATTERNS = [
    # French — keyword-anchored (no ambiguity, no lookbehind needed)
    r'au moins\s+(\d+)\s+an',                              # "au moins 4 ans"
    r'minimum\s+(\d+)\s+an',                               # "minimum 4 ans"
    r'plus\s+de\s+(\d+)\s+an',                             # "plus de 3 ans"
    r'justifi\w*\s+d\s+au\s+moins\s+(\d+)',                # "justifiez d'au moins 4"
    r'd\s+experience\s+(?:de\s+)?(\d+)\s+an',              # "d'expérience de 4 ans"
    r'experience\s+(?:de\s+)?(\d+)\s+an',                  # "expérience de 4 ans"
    # French — leading digit (lookbehind guards against range upper-bound capture)
    r'(?<!\d )(\d+)\s+an\w*\s+minimum',                    # "4 ans minimum"
    r'(?<!\d )(\d+)\s+an\w*\s+d\s+experience',             # "4 ans d'expérience"
    r'(?<!\d )(\d+)\s+ans?\s+(?:en|dans|sur)\b',           # "4 ans en DevOps"
    # English — keyword-anchored
    r'at\s+least\s+(\d+)\s+year',                          # "at least 4 years"
    r'minimum\s+(\d+)\s+year',                             # "minimum 4 years"
    # English — leading digit (lookbehind)
    r'(?<!\d )(\d+)\s+year\w*\s+minimum',                  # "4 years minimum"
    r'(?<!\d )(\d+)\s+year\w*\s+of\s+exp',                 # "4 years of experience"
    r'(?<!\d )(\d+)\s+year\w*\s+experience',               # "4 years experience"
    r'(?<!\d )(\d+)\s+year\w*\s+(?:in|on|with)\b',         # "4 years in DevOps"
]


def _norm(text):
    """Accent-insensitive normalization: lowercase + strip combining accents + punct→space."""
    text = unicodedata.normalize("NFD", str(text).lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Publication date parsing
# ---------------------------------------------------------------------------

_FRENCH_MONTHS = {
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "aout": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12,
}


def _parse_published_days_ago(text):
    """Parse a publication date/age string → integer days ago, or None.

    Handles French/English relative strings and absolute French dates (WTTJ).
    Returns None if text is empty or unparseable — callers treat that as neutral.
    """
    if not text:
        return None
    t = _norm(text)

    # "au cours des 24 dernières heures" / similar
    if "24" in t and ("heure" in t or "hour" in t):
        return 0

    # Hours → same day
    if re.search(r'\d+\s*(?:heure|hour)', t):
        return 0

    # Days: "il y a X jours", "X days ago", "publié il y a X jours"
    m = re.search(r'(\d+)\s*(?:jour|day)', t)
    if m:
        return int(m.group(1))

    # Weeks
    m = re.search(r'(\d+)\s*(?:semaine|week)', t)
    if m:
        return int(m.group(1)) * 7

    # Months
    m = re.search(r'(\d+)\s*(?:mois|month)', t)
    if m:
        return min(int(m.group(1)) * 30, 365)

    # Absolute French date "7 mai 2026" (accent-stripped by _norm)
    m = re.search(r'(\d{1,2})\s+([a-z]+)\s+(\d{4})', t)
    if m:
        month = _FRENCH_MONTHS.get(m.group(2))
        if month:
            from datetime import date as _date
            try:
                pub = _date(int(m.group(3)), month, int(m.group(1)))
                return max(0, (_date.today() - pub).days)
            except ValueError:
                pass

    return None


def _recency_bucket(days_ago):
    if days_ago is None:
        return "unknown"
    if days_ago <= 2:
        return "fresh"
    if days_ago <= 7:
        return "recent"
    if days_ago <= 14:
        return "normal"
    if days_ago <= 30:
        return "old"
    return "very_old"


def _freshness_bonus(days_ago):
    """Score adjustment based on publication age."""
    if days_ago is None:
        return 0
    if days_ago <= 2:
        return 10
    if days_ago <= 7:
        return 5
    if days_ago <= 14:
        return 2
    if days_ago <= 30:
        return 0
    return -5


def load_profile(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def load_jobs(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_selected_jobs(path, jobs):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)

def normalize(text):
    return re.sub(r"\s+", " ", str(text).lower()).strip()

def contains_any(text, keywords):
    text = normalize(text)
    return any(normalize(k) in text for k in keywords)

def count_hits(text, keywords):
    text = normalize(text)
    return sum(1 for k in keywords if normalize(k) in text)

def is_invalid_job_page(job):
    title = normalize(job.get("title", ""))
    description = normalize(job.get("description", ""))
    invalid_markers = [
        "vérification supplémentaire requise",
        "verification supplementaire requise",
        "additional verification required",
        "security check",
        "captcha",
        "robot",
        "page introuvable",
        "ce site est inaccessible"
    ]
    return any(marker in title or marker in description for marker in invalid_markers)


def has_senior_title(title):
    """True if the job title contains a seniority keyword (word-boundary, accent-insensitive)."""
    t = _norm(title)
    for kw in SENIOR_TITLE_KEYWORDS:
        pattern = r'(?<![a-z])' + re.escape(kw) + r'(?![a-z])'
        if re.search(pattern, t):
            return True
    return False


def analyze_experience(description):
    """
    Detects explicit experience requirement in the description.

    Rules:
      Range patterns (two numbers): lower <= 1 → ok | lower == 2 → borderline | lower >= 3 → too_high
      Min patterns (one number):    year  <= 2 → ok | year  >= 3 → too_high

    Min-pattern matches whose start position falls within a range match's span are skipped:
    this prevents e.g. the "3" in "2 à 3 ans d'expérience" (normalized "2 a 3 ans d experience")
    from being captured by a min pattern as a standalone "3 ans" minimum.

    Returns a dict:
      decision : "ok" | "borderline" | "too_high" | "unknown"
      years    : int or None (the deciding year value)
      matched  : matched text snippet (normalized form)
      reason   : short reason code
    """
    text = _norm(description)
    decisions = []   # list of (priority, decision, years, matched)
    range_spans = [] # list of (start, end) for each range match found

    # 1. Range patterns — two capture groups: (lower, upper)
    for pattern in _RANGE_PATTERNS:
        for match in re.finditer(pattern, text):
            try:
                lower = int(match.group(1))
                upper = int(match.group(2))
                if not (1 <= lower <= 15 and lower < upper):
                    continue
                range_spans.append((match.start(), match.end()))
                if lower <= 1:
                    decisions.append((1, "ok",         lower, match.group(0)))
                elif lower == 2:
                    decisions.append((2, "borderline", lower, match.group(0)))
                else:
                    decisions.append((3, "too_high",   lower, match.group(0)))
            except (ValueError, IndexError):
                pass

    # 2. Min patterns — single capture group: the minimum required years.
    # Skip any match whose start position falls inside an already-found range span.
    for pattern in _MIN_PATTERNS:
        for match in re.finditer(pattern, text):
            try:
                years = int(match.group(1))
                if not (1 <= years <= 15):
                    continue
                if any(rs <= match.start() < re for rs, re in range_spans):
                    continue
                if years <= 2:
                    decisions.append((1, "ok",       years, match.group(0)))
                else:
                    decisions.append((3, "too_high", years, match.group(0)))
            except (ValueError, IndexError):
                pass

    if not decisions:
        return {"decision": "unknown", "years": None, "matched": "", "reason": "no_pattern_matched"}

    # Take the worst (highest priority) decision found
    _, decision, years, matched_text = max(decisions, key=lambda x: x[0])

    reason_map = {"ok": "junior_ok", "borderline": "borderline_range", "too_high": "minimum_years"}
    return {"decision": decision, "years": years, "matched": matched_text, "reason": reason_map[decision]}


def has_title_excluded_keywords(title):
    return contains_any(title, TITLE_EXCLUDE_KEYWORDS)


def score_job(job, profile):
    title = normalize(job.get("title", ""))
    location = normalize(job.get("location", ""))
    description = normalize(job.get("description", ""))
    full_text = f"{title} {location} {description}"

    if is_invalid_job_page(job):
        return -999

    score = 0

    if contains_any(title, profile["target_titles"]):
        score += 40
    elif contains_any(description, profile["target_titles"]):
        score += 20

    if contains_any(location, profile["locations"]):
        score += 20
    elif contains_any(description, profile["locations"]):
        score += 10

    if contains_any(full_text, profile["work_modes"]):
        score += 6

    if contains_any(full_text, profile["experience_level"]):
        score += 18

    must_have_hits = count_hits(full_text, profile["must_have_keywords"])
    score += must_have_hits * 10

    nice_hits = count_hits(full_text, profile["nice_to_have_keywords"])
    score += nice_hits * 4

    if has_title_excluded_keywords(title):
        score -= 35

    if has_senior_title(job.get("title", "")):
        score -= 45

    score += _freshness_bonus(job.get("published_days_ago"))

    return score


def classify_job(job, profile):
    title = normalize(job.get("title", ""))
    description = normalize(job.get("description", ""))
    full_text = f"{title} {normalize(job.get('location', ''))} {description}"

    # 1. Page invalide (captcha, 404, bot detection)
    if is_invalid_job_page(job):
        return "rejected", "invalid_page"

    # 2. Titre exclu : alternance, stage, freelance, architecte
    if has_title_excluded_keywords(title):
        return "rejected", "excluded_title_keyword"

    # 3. Marqueurs senior dans le titre
    if has_senior_title(job.get("title", "")):
        return "rejected", "too_senior"

    # 4. Expérience explicite >= 3 ans → rejet immédiat avant scoring
    exp = analyze_experience(job.get("description", ""))
    job["_exp"] = exp
    if exp["decision"] == "too_high":
        return "rejected", "experience_too_high"

    # 5. Aucun mot-clé must-have détecté
    must_have_hits = count_hits(full_text, profile["must_have_keywords"])
    if must_have_hits == 0:
        return "rejected", "no_must_have"

    # 6. Score global
    score = score_job(job, profile)
    if score < MIN_SCORE:
        return "rejected", "score_too_low"

    # 7. Arbre de décision selected / review
    has_title_match = (
        contains_any(title, profile["target_titles"])
        or contains_any(description, profile["target_titles"])
    )

    if must_have_hits >= 2 and score >= 70:
        if has_title_match:
            if job.get("platform") == "linkedin":
                return "review", "linkedin_manual_review"
            # Expérience borderline (2 ans) → downgrade selected → review
            if exp["decision"] == "borderline":
                return "review", "borderline_experience"
            return "selected", "selected"
        return "review", "missing_target_title"

    if must_have_hits >= 2 and score >= 40:
        return "review", "mid_score"

    if must_have_hits == 1 and score >= 50:
        return "review", "low_must_have"

    return "rejected", "score_too_low"


def load_manual_status_keys(path):
    """Return {(job_id, url): status} for rows with a manual status in tracking.csv."""
    result = {}
    if not os.path.exists(path):
        return result
    with open(path, "r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            status = row.get("status", "").strip().lower()
            if status in MANUAL_STATUSES:
                key = (row.get("job_id", "").strip(), row.get("url", "").strip())
                result[key] = status
    return result


def load_existing_tracking(path):
    if not os.path.exists(path):
        return [], set()

    with open(path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    existing_keys = set()
    for row in rows:
        key = (row.get("job_id", ""), row.get("url", ""))
        existing_keys.add(key)

    return rows, existing_keys


def append_new_jobs_to_tracking(path, jobs):
    existing_rows, existing_keys = load_existing_tracking(path)
    new_rows = []

    for job in jobs:
        key = (job.get("id", ""), job.get("url", ""))
        if key in existing_keys:
            continue

        row = {
            "job_id": job.get("id", ""),
            "platform": job.get("platform", ""),
            "company": job.get("company", ""),
            "title": job.get("title", ""),
            "location": job.get("location", ""),
            "url": job.get("url", ""),
            "score": job.get("score", 0),
            "status": job.get("decision", "rejected"),
            "applied_at": "",
            "notes": job.get("reason", "")
        }
        new_rows.append(row)

    all_rows = existing_rows + new_rows

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(all_rows)

    return len(new_rows), len(all_rows)


def _test_experience_analysis():
    """Smoke test for analyze_experience(). Run: python matcher.py --test"""
    cases = [
        # ── Pas de pattern ──────────────────────────────────────────────────
        ("DevOps 2026",                           "unknown"),   # année calendaire, pas une durée
        ("150/250 K€",                            "unknown"),   # salaire, pas une durée
        ("junior DevOps",                         "unknown"),   # pas de mention explicite
        # ── ok : <= 2 ans explicites ou <= 1 an dans un range ───────────────
        ("1 an d'experience",                     "ok"),
        ("2 ans d'expérience",                    "ok"),        # minimum = 2 ans → ok
        ("2 years minimum",                       "ok"),        # minimum = 2 ans → ok
        # ── borderline : range dont la borne basse est 2 ────────────────────
        ("2 à 3 ans d'expérience",                "borderline"),
        ("2-3 ans",                               "borderline"),
        ("entre 2 et 3 ans",                      "borderline"),
        ("2 to 3 years",                          "borderline"),
        # ── too_high : minimum >= 3 ou range dont la borne basse >= 3 ────────
        ("minimum 3 ans d'expérience",            "too_high"),
        ("3 ans minimum",                         "too_high"),
        ("4 ans d'expérience minimum",            "too_high"),
        ("au moins 5 ans",                        "too_high"),
        ("3 à 5 ans d'expérience",                "too_high"),
        ("5+ years of experience",                "too_high"),
        ("at least 3 years of experience",        "too_high"),
        ("expérience de 4 ans",                   "too_high"),
        ("Vous justifiez d'au moins 3 ans",       "too_high"),
    ]
    passed = 0
    failed = 0
    for text, expected in cases:
        result = analyze_experience(text)
        ok = result["decision"] == expected
        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
        print(f"  [{status}] {text!r:<46} => {result['decision']:<12} (years={result['years']}, matched={result['matched']!r})")
        if not ok:
            print(f"         expected: {expected}")
    print(f"\n  {passed}/{passed + failed} passed")


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        print("=== analyze_experience() smoke test ===\n")
        _test_experience_analysis()
        sys.exit(0)

    profile = load_profile(PROFILE_PATH)
    jobs = load_jobs(JOBS_PATH)

    manual_keys = load_manual_status_keys(TRACKING_PATH)

    selected_jobs = []
    count_selected = 0
    count_review = 0
    count_rejected = 0
    count_skipped_manual = 0

    for job in jobs:
        key = (job.get("id", ""), job.get("url", ""))
        if key in manual_keys:
            manual_status = manual_keys[key]
            job["score"] = 0
            job["decision"] = "skipped"
            job["reason"] = f"manual_status={manual_status}"
            count_skipped_manual += 1
            print(f"Job: {job['id']} | skipped   | manual_status={manual_status:<20s} | {job['title'][:60]}")
            continue

        # Parse and store publication date fields
        pub_text = (job.get("published") or "").strip()
        days_ago = _parse_published_days_ago(pub_text)
        job["published_at_text"]        = pub_text
        job["published_days_ago"]       = days_ago
        job["published_recency_bucket"] = _recency_bucket(days_ago)

        score = score_job(job, profile)
        decision, reason = classify_job(job, profile)
        exp_info = job.pop("_exp", {})

        job["score"] = score
        job["decision"] = decision
        job["reason"] = reason

        freshness = _freshness_bonus(days_ago)
        freshness_str = f"freshness={freshness:+d} days_ago={days_ago}" if days_ago is not None else "freshness=0"
        if reason == "experience_too_high":
            print(f"Job: {job.get('id','')} | rejected  | experience_too_high | matched={exp_info.get('matched','')!r} | {job['title'][:60]}")
        else:
            print(f"Job: {job.get('id','')} | score={score:4d} | {freshness_str} | {decision:8s} | {reason:25s} | {job['title'][:60]}")

        if decision == "selected":
            count_selected += 1
            selected_jobs.append(job)
        elif decision == "review":
            count_review += 1
            selected_jobs.append(job)
        else:
            count_rejected += 1

    selected_jobs.sort(key=lambda x: x["score"], reverse=True)

    added_count, total_count = append_new_jobs_to_tracking(TRACKING_PATH, jobs)
    save_selected_jobs(SELECTED_JOBS_PATH, selected_jobs)

    print(f"\nOffres analysees   : {len(jobs)}")
    print(f"  selected         : {count_selected}")
    print(f"  review           : {count_review}")
    print(f"  rejected         : {count_rejected}")
    print(f"  statut manuel    : {count_skipped_manual}")
    print(f"Nouvelles en tracking : {added_count} (total {total_count})")
    print(f"Fichier selected_jobs : {SELECTED_JOBS_PATH}")
    print()

    for job in selected_jobs:
        tag = "[SELECTED]" if job["decision"] == "selected" else "[REVIEW]  "
        print(f"{tag} [{job['score']:3d}] {job['title']} - {job['company']} - {job['location']}")
        print(f"           {job['url']}")
        print()
