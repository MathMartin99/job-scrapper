import csv
import json
import os
import re
import unicodedata
import yaml
from datetime import datetime

INPUT_PATH    = "app/data/jobs/selected_jobs.json"
OUTPUT_PATH   = "app/data/jobs/selected_urls.txt"
PROFILE_PATH  = "app/config/candidate_profile.yaml"
REPORT_DIR    = "app/data/exports"
RUN_STATS_PATH = "app/data/run_stats.json"
JOBS_RAW_PATH  = "app/data/jobs/jobs_raw.json"
TRACKING_PATH  = "app/data/tracking.csv"
MAX_URLS      = 10

MANUAL_STATUSES = {"ignored", "applied", "already_applied", "rejected_by_company", "manually_rejected"}

PLATFORM_LABELS = {
    "wttj": "WTTJ",
    "hellowork": "HelloWork",
    "linkedin": "LinkedIn",
    "indeed": "Indeed",
}

REVIEW_REASONS = {
    "missing_target_title": (
        "Aucun titre DevOps/Cloud détecté dans le titre ou la description. "
        "Le poste semble orienté sysadmin/Linux avec des outils DevOps partagés. "
        "À vérifier manuellement avant de postuler."
    ),
    "mid_score": (
        "Score entre 40 et 69 avec ≥ 2 must-have. "
        "Correspondance partielle au profil junior DevOps/Cloud."
    ),
    "low_must_have": (
        "Un seul mot-clé must-have détecté (seuil sélection = 2). "
        "Profil DevOps partiellement compatible."
    ),
    "linkedin_manual_review": (
        "Offre LinkedIn passant les filtres de matching. "
        "Validation manuelle requise avant de postuler."
    ),
}

FORM_PREFIXES = (
    "formation", "contrat", "statut", "localisation",
    "diplôme", "branche", "niveau d", "mission qui"
)


def normalize(text):
    return re.sub(r"\s+", " ", str(text).lower()).strip()


def _clean_display_title(raw_title, company=None):
    """Strip WTTJ title pollution (repeated title+company blocks).

    Raw title is never modified in data files — this is display-only.
    """
    if not raw_title:
        return raw_title
    title = raw_title.strip()

    def _finalize(s):
        return re.sub(r'[\s\-–|_/]+$', '', s).strip()

    if company:
        co  = company.strip()
        idx = title.find(co)
        if 3 < idx < 120:
            candidate = _finalize(title[:idx])
            if candidate:
                return candidate
    n    = len(title)
    half = n // 2
    if half >= 5:
        first = title[:half].rstrip()
        rest  = title[half:].lstrip()
        if rest.startswith(first):
            return _finalize(first)
    words = title.split()
    if len(words) > 4:
        w0  = words[0]
        idx = title.find(w0, len(w0) + 1)
        if 5 < idx <= half + 20:
            candidate = _finalize(title[:idx])
            if len(candidate) >= 5:
                return candidate
    return title


def keyword_hits(job, keywords):
    full = normalize(
        job.get("title", "") + " " +
        job.get("location", "") + " " +
        job.get("description", "")
    )
    hits = [k for k in keywords if normalize(k) in full]
    miss = [k for k in keywords if normalize(k) not in full]
    return hits, miss


def make_summary(description, max_chars=300):
    text = re.sub(r"\s+", " ", description).strip()
    sentences = re.split(r"(?<=[.!?])\s+", text)
    for s in sentences:
        s = s.strip()
        if len(s) < 80:
            continue
        if any(normalize(s).startswith(p) for p in FORM_PREFIXES):
            continue
        return s[:max_chars] + ("..." if len(s) > max_chars else "")
    return text[:max_chars] + ("..." if len(text) > max_chars else "")


def count_raw_jobs():
    try:
        with open(JOBS_RAW_PATH, "r", encoding="utf-8") as f:
            return len(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def load_run_stats():
    """Load run_stats.json. Returns None if missing, a dict with _stale=True if outdated."""
    try:
        with open(RUN_STATS_PATH, "r", encoding="utf-8") as f:
            stats = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

    actual = count_raw_jobs()
    if actual is not None and stats.get("total_collected") != actual:
        return {"_stale": True, "total_collected": actual}
    return stats


def _load_raw_platform_counts():
    """Return {platform: count} from jobs_raw.json."""
    try:
        with open(JOBS_RAW_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        counts = {}
        for j in raw:
            p = j.get("platform", "unknown")
            counts[p] = counts.get(p, 0) + 1
        return counts
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _count_skipped_manual_per_source():
    """Return {platform: count} of jobs in current batch that have a manual status.

    Cross-references tracking.csv (manual statuses) with jobs_raw.json (URL→platform).
    Matches what matcher.py counts as count_skipped_manual.
    """
    try:
        with open(JOBS_RAW_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        url_to_platform = {j.get("url", ""): j.get("platform", "unknown") for j in raw}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

    skipped = {}
    try:
        with open(TRACKING_PATH, "r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("status", "").strip().lower() in MANUAL_STATUSES:
                    url = row.get("url", "").strip()
                    if url in url_to_platform:
                        p = url_to_platform[url]
                        skipped[p] = skipped.get(p, 0) + 1
    except FileNotFoundError:
        pass
    return skipped


def _compute_source_stats(all_jobs):
    """Return {platform: {selected: N, review: N}} from full exploitable list."""
    stats = {}
    for job in all_jobs:
        p = job.get("platform", "unknown")
        d = job.get("decision", "")
        if d not in ("selected", "review"):
            continue
        if p not in stats:
            stats[p] = {"selected": 0, "review": 0}
        stats[p][d] += 1
    return stats


def _normalize_duplicate_key(text):
    """Normalize text for duplicate detection: lowercase, strip accents, remove punctuation."""
    text = str(text).lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _split_main_and_potential_duplicates(jobs, max_urls):
    """Split sorted exploitable jobs into main list and potential duplicates.

    Detection rule: exact match on (normalized_title, normalized_company).
    Conservative: requires both title and company to be non-empty after normalization.

    Returns:
        main_jobs: up to max_urls unique jobs (by title+company key)
        potential_duplicates: jobs whose key was already seen in main_jobs,
            each enriched with _similar_to pointing to the matching main job
    """
    seen_keys            = {}
    main_jobs            = []
    potential_duplicates = []

    for job in jobs:
        title_key   = _normalize_duplicate_key(job.get("title", ""))
        company_key = _normalize_duplicate_key(job.get("company", ""))
        dedup_key   = (title_key, company_key)

        if title_key and company_key and dedup_key in seen_keys:
            dup = dict(job)
            dup["_similar_to"] = seen_keys[dedup_key]
            potential_duplicates.append(dup)
        elif len(main_jobs) < max_urls:
            main_jobs.append(job)
            if title_key and company_key:
                seen_keys[dedup_key] = job
        # else: non-duplicate overflow beyond max_urls, discard

    return main_jobs, potential_duplicates


def _build_balanced_jobs(all_jobs, export_cfg):
    """Group exploitable jobs by source, up to max_per_source per source.

    Returns (jobs_by_source dict, flat_list) where flat_list follows source_order.
    """
    max_per          = export_cfg.get("max_per_source", 3)
    include_statuses = set(export_cfg.get("include_statuses", ["selected", "review"]))
    source_order     = export_cfg.get("source_order", ["wttj", "hellowork", "linkedin", "indeed"])

    by_source = {}
    for job in all_jobs:  # already sorted by score desc
        p = job.get("platform", "unknown")
        d = job.get("decision", "")
        if d not in include_statuses:
            continue
        if p not in by_source:
            by_source[p] = []
        if len(by_source[p]) < max_per:
            by_source[p].append(job)

    flat = []
    for src in source_order:
        flat.extend(by_source.get(src, []))
    for src in by_source:
        if src not in source_order:
            flat.extend(by_source[src])

    return by_source, flat


def _build_job_card(job, profile):
    """Return Markdown lines for a single job card."""
    decision = job.get("decision", "")
    reason   = job.get("reason", "")
    score    = job.get("score", 0)
    badge    = "⭐ SELECTED" if decision == "selected" else "🔍 REVIEW"

    display_title = _clean_display_title(job.get("title", ""), job.get("company", ""))

    # Publication date display
    days_ago = job.get("published_days_ago")
    pub_text = job.get("published_at_text", "")
    if days_ago is not None:
        if days_ago == 0:
            pub_display = "aujourd'hui"
        elif days_ago == 1:
            pub_display = "il y a 1 jour"
        else:
            pub_display = f"il y a {days_ago} jours"
    elif pub_text:
        pub_display = pub_text
    else:
        pub_display = "inconnue"

    lines = []
    lines.append("")
    lines.append(f"## {badge} — {display_title}")
    lines.append("")
    lines.append("| Champ | Valeur |")
    lines.append("|---|---|")
    lines.append(f"| **Entreprise** | {job.get('company', '—')} |")
    lines.append(f"| **Localisation** | {job.get('location', '—')} |")
    lines.append(f"| **Publication** | {pub_display} |")
    lines.append(f"| **Score** | {score} |")
    lines.append(f"| **Statut** | {decision.upper()} |")
    if decision == "review":
        lines.append(f"| **Raison** | `{reason}` |")
    lines.append(f"| **URL** | {job.get('url', '')} |")
    lines.append("")

    mh_hits, mh_miss = keyword_hits(job, profile["must_have_keywords"])
    nh_hits, _       = keyword_hits(job, profile["nice_to_have_keywords"])

    mh_hit_str  = " · ".join(f"`{k}`" for k in mh_hits) if mh_hits else "aucun"
    mh_miss_str = " · ".join(f"`{k}`" for k in mh_miss) if mh_miss else ""
    nh_hit_str  = " · ".join(f"`{k}`" for k in nh_hits) if nh_hits else "aucun"
    n_mh = len(profile["must_have_keywords"])
    n_nh = len(profile["nice_to_have_keywords"])

    lines.append("**Mots-clés détectés**")
    lines.append(f"- ✅ Must-have ({len(mh_hits)}/{n_mh}) : {mh_hit_str}")
    if mh_miss:
        lines.append(f"- ❌ Must-have absents : {mh_miss_str}")
    lines.append(f"- 🟡 Nice-to-have ({len(nh_hits)}/{n_nh}) : {nh_hit_str}")
    lines.append("")

    summary = make_summary(job.get("description", ""))
    if summary:
        lines.append("**Résumé**")
        lines.append(f"> {summary}")
        lines.append("")

    if decision == "review":
        explanation = REVIEW_REASONS.get(reason, f"Raison : `{reason}`")
        lines.append("**Pourquoi en review ?**")
        lines.append(explanation)
        lines.append("")

    job_id = job.get("id", "")
    if job_id:
        lines.append("**Actions rapides**")
        lines.append("```")
        lines.append(f'python app/src/jobs/mark_job.py --id "{job_id}" --status applied')
        lines.append(f'python app/src/jobs/mark_job.py --id "{job_id}" --status ignored')
        lines.append(f'python app/src/jobs/mark_job.py --id "{job_id}" --status manually_rejected')
        lines.append(f'python app/src/jobs/mark_job.py --id "{job_id}" --status rejected_by_company')
        lines.append("```")
        lines.append("")

    lines.append("---")
    return lines


def _build_source_section(source_key, source_jobs, profile):
    """Return Markdown lines for a single source section (header + job cards)."""
    label = PLATFORM_LABELS.get(source_key, source_key.upper())
    lines = []
    lines.append("")
    lines.append(f"## Top {label}")
    lines.append("")
    if not source_jobs:
        lines.append("Aucune offre exploitable pour cette source sur ce run.")
        lines.append("")
    else:
        for job in source_jobs:
            lines.extend(_build_job_card(job, profile))
    return lines


def _build_duplicates_section(potential_duplicates):
    """Return Markdown lines for the potential duplicates section."""
    if not potential_duplicates:
        return []

    lines = []
    lines.append("")
    lines.append("## ⚠️ Doublons potentiels")
    lines.append("")
    lines.append(
        f"Les {len(potential_duplicates)} offre(s) suivante(s) partagent le même titre "
        f"et la même entreprise qu'une offre déjà retenue dans la liste principale. "
        f"Vérifier manuellement avant de postuler."
    )

    for dup in potential_duplicates:
        title    = dup.get("title", "")
        company  = dup.get("company", "—")
        score    = dup.get("score", 0)
        url      = dup.get("url", "")
        decision = dup.get("decision", "").upper()
        platform = PLATFORM_LABELS.get(dup.get("platform", ""), dup.get("platform", "?"))

        similar_to       = dup.get("_similar_to", {})
        similar_url      = similar_to.get("url", "—")
        similar_platform = PLATFORM_LABELS.get(
            similar_to.get("platform", ""), similar_to.get("platform", "?")
        )

        lines.append("")
        lines.append(f"### ⚠️ Doublon potentiel — {title}")
        lines.append("")
        lines.append("| Champ | Valeur |")
        lines.append("|---|---|")
        lines.append(f"| **Entreprise** | {company} |")
        lines.append(f"| **Source** | {platform} |")
        lines.append(f"| **Score** | {score} |")
        lines.append(f"| **Statut** | {decision} |")
        lines.append(f"| **URL doublon** | {url} |")
        lines.append(f"| **Offre principale similaire** | {similar_platform} — {similar_url} |")

        job_id = dup.get("id", "")
        if job_id:
            lines.append("")
            lines.append("**Actions rapides**")
            lines.append("```")
            lines.append(f'python app/src/jobs/mark_job.py --id "{job_id}" --status ignored')
            lines.append(f'python app/src/jobs/mark_job.py --id "{job_id}" --status manually_rejected')
            lines.append("```")

    lines.append("")
    lines.append("---")
    return lines


def build_report(
    jobs, profile,
    n_exploitable=None, source_stats=None, potential_duplicates=None,
    balanced=False, jobs_by_source=None, export_cfg=None,
):
    now   = datetime.now().strftime("%Y-%m-%d %H:%M")
    stats = load_run_stats()

    total_exploitable = n_exploitable if n_exploitable is not None else len(jobs)
    n_dup = len(potential_duplicates) if potential_duplicates else 0

    if source_stats:
        n_sel = sum(v.get("selected", 0) for v in source_stats.values())
        n_rev = sum(v.get("review", 0) for v in source_stats.values())
    elif stats and not stats.get("_stale"):
        n_sel = stats.get("n_selected", 0)
        n_rev = stats.get("n_review", 0)
    else:
        n_sel = sum(1 for j in jobs if j.get("decision") == "selected")
        n_rev = sum(1 for j in jobs if j.get("decision") == "review")

    raw_counts         = _load_raw_platform_counts()
    skipped_per_source = _count_skipped_manual_per_source()
    total_skipped      = sum(skipped_per_source.values())

    lines = []
    lines.append(f"# Rapport offres DevOps — {now}")
    lines.append("")

    # Offres analysées
    if stats and not stats.get("_stale"):
        lines.append(f"**Offres analysées :** {stats.get('total_collected', '?')}")
    elif raw_counts:
        lines.append(f"**Offres analysées :** {sum(raw_counts.values())}")

    # Exploitables trouvées
    lines.append(
        f"**Exploitables trouvées :** {total_exploitable}"
        f" — {n_sel} selected | {n_rev} review"
    )

    # Offres exportées / principales
    label_export = "Offres principales exportées" if n_dup > 0 else "Offres exportées"
    if total_exploitable > len(jobs):
        balance_hint = "max 3 par source" if balanced else f"top {len(jobs)} par score"
        lines.append(
            f"**{label_export} :** {len(jobs)} / {total_exploitable}"
            f" — {balance_hint}"
        )
    else:
        lines.append(f"**{label_export} :** {len(jobs)}")

    # Doublons potentiels
    if n_dup > 0:
        lines.append(f"**Doublons potentiels :** {n_dup} (voir section en bas du rapport)")

    # Sources status line
    source_parts    = []
    show_indeed_row = False
    indeed_label    = ""

    if raw_counts.get("wttj", 0) > 0:
        source_parts.append("WTTJ ✓")
    if raw_counts.get("hellowork", 0) > 0:
        source_parts.append("HelloWork ✓")
    if raw_counts.get("linkedin", 0) > 0:
        source_parts.append("LinkedIn ✓")

    if stats and not stats.get("_stale"):
        iters      = stats.get("iterations", 0)
        login_wall = stats.get("login_wall", False)
        if login_wall:
            indeed_label    = "Indeed tenté mais bloqué"
            show_indeed_row = True
        elif iters > 0:
            indeed_label    = "Indeed ✓"
            show_indeed_row = True

    if raw_counts.get("indeed", 0) > 0:
        indeed_label    = indeed_label or "Indeed ✓"
        show_indeed_row = True

    if indeed_label:
        source_parts.append(indeed_label)

    if source_parts:
        lines.append(f"**Sources :** {' | '.join(source_parts)}")

    # Statuts manuels ignorés
    if total_skipped > 0:
        lines.append(f"**Statuts manuels ignorés :** {total_skipped}")

    # Per-source breakdown table
    table_sources = []
    if raw_counts.get("wttj", 0) > 0:
        table_sources.append("wttj")
    if raw_counts.get("hellowork", 0) > 0:
        table_sources.append("hellowork")
    if raw_counts.get("linkedin", 0) > 0:
        table_sources.append("linkedin")
    if show_indeed_row:
        table_sources.append("indeed")

    run_source_stats = (stats or {}).get("sources", {}) if stats and not stats.get("_stale") else {}

    _status_display = {
        "ok":         "✓ ok",
        "partial":    "⚠ partiel",
        "empty":      "∅ vide",
        "blocked":    "🔒 bloqué",
        "login_wall": "🔒 bloqué",
        "failed":     "✗ erreur",
        "skipped":    "— ignoré",
        "disabled":   "off",
    }
    _tgt_default = (export_cfg or {}).get("target_per_source", "?")

    if table_sources:
        lines.append("")
        lines.append("| Source | Statut | Exploitables | Selected | Review | Rejected | Skipped |")
        lines.append("|---|---|---:|---:|---:|---:|---:|")
        for p in table_sources:
            label     = PLATFORM_LABELS.get(p, p)
            p_sel     = (source_stats or {}).get(p, {}).get("selected", 0)
            p_rev     = (source_stats or {}).get(p, {}).get("review", 0)
            p_raw     = raw_counts.get(p, 0)
            p_skipped = skipped_per_source.get(p, 0)
            p_rej     = max(0, p_raw - p_sel - p_rev - p_skipped)
            rs        = run_source_stats.get(p, {})
            run_status = rs.get("status", "")
            target    = rs.get("target", _tgt_default)
            exploitable = p_sel + p_rev
            msg       = rs.get("message", "")
            status_str = _status_display.get(run_status, run_status or "?")
            if msg and run_status in ("partial", "blocked", "failed"):
                status_str += f" ({msg})"
            lines.append(f"| {label} | {status_str} | {exploitable}/{target} | {p_sel} | {p_rev} | {p_rej} | {p_skipped} |")

    lines.append("")
    lines.append("---")

    # Job cards — flat or balanced by source
    if balanced and jobs_by_source is not None:
        source_order = (export_cfg or {}).get(
            "source_order", ["wttj", "hellowork", "linkedin", "indeed"]
        )

        lines.append("")
        lines.append("## Répartition par source")
        lines.append("")
        lines.append("| Source | Offres retenues |")
        lines.append("|---|---:|")
        for src in source_order:
            src_jobs = jobs_by_source.get(src, [])
            if src_jobs:
                label = PLATFORM_LABELS.get(src, src.upper())
                lines.append(f"| {label} | {len(src_jobs)} |")
        lines.append("")
        lines.append("---")

        for src in source_order:
            lines.extend(_build_source_section(src, jobs_by_source.get(src, []), profile))
    else:
        for job in jobs:
            lines.extend(_build_job_card(job, profile))

    lines.extend(_build_duplicates_section(potential_duplicates or []))

    return "\n".join(lines)


def export_markdown(
    jobs, profile,
    n_exploitable=None, source_stats=None, potential_duplicates=None,
    balanced=False, jobs_by_source=None, export_cfg=None,
):
    os.makedirs(REPORT_DIR, exist_ok=True)
    filename = f"report_{datetime.now().strftime('%Y-%m-%d')}.md"
    path = os.path.join(REPORT_DIR, filename)
    content = build_report(
        jobs, profile,
        n_exploitable=n_exploitable,
        source_stats=source_stats,
        potential_duplicates=potential_duplicates,
        balanced=balanced,
        jobs_by_source=jobs_by_source,
        export_cfg=export_cfg,
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Rapport Markdown genere : {path}")
    return path


def main():
    with open(INPUT_PATH, "r", encoding="utf-8") as f:
        all_jobs = json.load(f)

    with open(PROFILE_PATH, "r", encoding="utf-8") as f:
        profile = yaml.safe_load(f)

    all_jobs = sorted(all_jobs, key=lambda x: x.get("score", 0), reverse=True)

    source_stats  = _compute_source_stats(all_jobs)
    n_exploitable = len(all_jobs)

    export_cfg = profile.get("export", {})
    balanced   = export_cfg.get("balanced_by_source", False)

    if balanced:
        jobs_by_source, flat_jobs = _build_balanced_jobs(all_jobs, export_cfg)
        main_jobs, potential_duplicates = _split_main_and_potential_duplicates(
            flat_jobs, len(flat_jobs)
        )
    else:
        jobs_by_source = None
        main_jobs, potential_duplicates = _split_main_and_potential_duplicates(all_jobs, MAX_URLS)

    # Export URL list
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        for job in main_jobs:
            f.write(job.get("url", "").strip() + "\n")
    print(f"{len(main_jobs)} URLs exportees dans {OUTPUT_PATH}")

    # Export Markdown report
    export_markdown(
        main_jobs, profile,
        n_exploitable=n_exploitable,
        source_stats=source_stats,
        potential_duplicates=potential_duplicates,
        balanced=balanced,
        jobs_by_source=jobs_by_source,
        export_cfg=export_cfg,
    )


if __name__ == "__main__":
    main()
