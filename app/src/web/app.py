"""
Interface web locale job-bot — V2.

Routes:
    GET  /               Offres exploitables du run courant
    POST /mark           Enregistre les décisions manuelles
    GET  /history        Historique complet (tracking.csv)
    GET  /reports        Consultation des rapports Markdown générés
    GET  /settings       Paramètres (candidate_profile.yaml)
    POST /settings       Sauvegarde des paramètres
    POST /run/full       Lance run.py --wttj-interactive (background)
    POST /run/matching   Lance run.py --from-step=3 (background)
    POST /run/export     Lance run.py --from-step=4 (background)
    GET  /api/run/status Statut du run en cours (JSON)
"""

import copy
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from datetime import datetime

import yaml
from flask import Flask, jsonify, redirect, render_template, request, url_for

try:
    import markdown as _md_lib
    def _render_md(text):
        return _md_lib.markdown(
            text, extensions=["tables", "fenced_code"], output_format="html"
        )
except ImportError:
    def _render_md(text):
        return None

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# app/src/web/app.py  →  app/src/web  →  app/src  →  app  →  BASE_DIR
BASE_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

SELECTED_JOBS_PATH = os.path.join(BASE_DIR, "app", "data", "jobs", "selected_jobs.json")
JOBS_RAW_PATH      = os.path.join(BASE_DIR, "app", "data", "jobs", "jobs_raw.json")
TRACKING_PATH      = os.path.join(BASE_DIR, "app", "data", "tracking.csv")
PROFILE_PATH       = os.path.join(BASE_DIR, "app", "config", "candidate_profile.yaml")
BACKUP_DIR         = os.path.join(BASE_DIR, "app", "config", "backups")
EXPORT_DIR         = os.path.join(BASE_DIR, "app", "data", "exports")

TRACKING_HEADERS = [
    "job_id", "platform", "company", "title", "location",
    "url", "score", "status", "applied_at", "notes",
]

MANUAL_STATUSES = {
    "applied", "ignored", "manually_rejected",
    "already_applied", "rejected_by_company",
}

PLATFORM_LABELS = {
    "wttj":      "WTTJ",
    "hellowork": "HelloWork",
    "linkedin":  "LinkedIn",
    "indeed":    "Indeed",
}

STATUS_LABELS = {
    "applied":             "Postulé",
    "ignored":             "Ignoré",
    "manually_rejected":   "Pas pertinent",
    "already_applied":     "Déjà postulé",
    "rejected_by_company": "Refus entreprise",
}

REASON_LABELS = {
    "selected":              "",
    "missing_target_title":  "Titre non DevOps/Cloud",
    "mid_score":             "Score partiel",
    "low_must_have":         "1 seul must-have",
    "linkedin_manual_review": "LinkedIn — vérif. manuelle",
}

# ---------------------------------------------------------------------------
# Title cleaning
# ---------------------------------------------------------------------------

def _clean_display_title(raw_title, company=None):
    """Strip WTTJ title pollution (repeated title+company blocks).

    WTTJ raw titles look like "Job Title Company Job Title Company desc..."
    Returns a short, readable display title. Raw title is never modified in data.
    """
    if not raw_title:
        return raw_title
    title = raw_title.strip()

    def _finalize(s):
        """Strip trailing separators left by WTTJ patterns (dash, pipe, space)."""
        return re.sub(r'[\s\-–|_/]+$', '', s).strip()

    # Pattern A: company name appears mid-title — cut before it
    if company:
        co  = company.strip()
        idx = title.find(co)
        if 3 < idx < 120:
            candidate = _finalize(title[:idx])
            if candidate:
                return candidate

    n    = len(title)
    half = n // 2

    # Pattern B: exact half-string repeat ("Foo Bar Foo Bar")
    if half >= 5:
        first = title[:half].rstrip()
        rest  = title[half:].lstrip()
        if rest.startswith(first):
            return _finalize(first)

    # Pattern C: first word repeats mid-string ("Title Co Title Co …")
    words = title.split()
    if len(words) > 4:
        w0  = words[0]
        idx = title.find(w0, len(w0) + 1)
        if 5 < idx <= half + 20:
            candidate = _finalize(title[:idx])
            if len(candidate) >= 5:
                return candidate

    return title


# ---------------------------------------------------------------------------
# Background runner
# ---------------------------------------------------------------------------
_run_lock  = threading.Lock()
_run_state = {
    "running":   False,
    "command":   "",
    "started":   "",
    "output":    "",
    "exit_code": None,
}


def _background_run(cmd_args, label):
    with _run_lock:
        _run_state.update({
            "running":   True,
            "command":   label,
            "started":   datetime.now().strftime("%H:%M:%S"),
            "output":    "",
            "exit_code": None,
        })
    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["JOB_BOT_WEB_UI"] = "1"
        result = subprocess.run(
            cmd_args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=BASE_DIR,
        )
        output    = (result.stdout or "") + (result.stderr or "")
        exit_code = result.returncode
    except Exception as exc:
        output    = str(exc)
        exit_code = -1
    with _run_lock:
        _run_state.update({
            "running":   False,
            "output":    output[-4000:],
            "exit_code": exit_code,
        })


def _start_run(label, *extra_args):
    """Launch a background run if none is in progress. Returns True if started."""
    with _run_lock:
        if _run_state["running"]:
            return False
    cmd = [sys.executable, os.path.join(BASE_DIR, "run.py")] + list(extra_args)
    t   = threading.Thread(target=_background_run, args=(cmd, label), daemon=True)
    t.start()
    return True


# ---------------------------------------------------------------------------
# Tracking helpers
# ---------------------------------------------------------------------------

def _load_tracking():
    if not os.path.exists(TRACKING_PATH):
        return []
    with open(TRACKING_PATH, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _save_tracking(rows):
    with open(TRACKING_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TRACKING_HEADERS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _apply_decisions(decisions):
    """Apply {job_id: status} dict to tracking.csv. Returns count updated."""
    rows    = _load_tracking()
    idx_map = {row.get("job_id", "").strip(): i for i, row in enumerate(rows)}
    now     = datetime.now().strftime("%Y-%m-%d %H:%M")
    updated = 0
    for job_id, status in decisions.items():
        if not status or status not in MANUAL_STATUSES:
            continue
        i = idx_map.get(job_id.strip())
        if i is None:
            continue
        rows[i]["status"]     = status
        rows[i]["applied_at"] = now
        updated += 1
    if updated:
        _save_tracking(rows)
    return updated


# ---------------------------------------------------------------------------
# Jobs data helpers
# ---------------------------------------------------------------------------

def _count_pending():
    """Count selected/review jobs in selected_jobs.json not yet manually treated."""
    try:
        with open(SELECTED_JOBS_PATH, encoding="utf-8") as f:
            jobs = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return 0
    manual_urls = {
        row.get("url", "").strip()
        for row in _load_tracking()
        if row.get("status", "").strip().lower() in MANUAL_STATUSES
        and row.get("url", "").strip()
    }
    return sum(
        1 for j in jobs
        if j.get("decision") in ("selected", "review")
        and j.get("url", "").strip() not in manual_urls
    )


def _manual_job_ids():
    return {
        row.get("job_id", "").strip()
        for row in _load_tracking()
        if row.get("status", "").strip().lower() in MANUAL_STATUSES
        and row.get("job_id", "").strip()
    }


def _current_jobs():
    """Selected/review jobs from selected_jobs.json, excluding manually-processed ones."""
    try:
        with open(SELECTED_JOBS_PATH, encoding="utf-8") as f:
            jobs = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    manual = _manual_job_ids()
    result = []
    for j in jobs:
        if j.get("decision") not in ("selected", "review"):
            continue
        if j.get("id", "").strip() in manual:
            continue
        j = dict(j)
        j["display_title"] = _clean_display_title(j.get("title", ""), j.get("company", ""))
        result.append(j)
    return result


def _history_rows():
    """All tracking.csv rows, most recently updated first."""
    rows = _load_tracking()
    rows.sort(key=lambda r: r.get("applied_at", "") or "", reverse=True)
    for row in rows:
        row["display_title"] = _clean_display_title(row.get("title", ""), row.get("company", ""))
    return rows


# ---------------------------------------------------------------------------
# Reports helpers
# ---------------------------------------------------------------------------

def _list_reports():
    """Return list of report filenames (report_*.md) sorted newest first."""
    if not os.path.isdir(EXPORT_DIR):
        return []
    files = [
        fn for fn in os.listdir(EXPORT_DIR)
        if fn.startswith("report_") and fn.endswith(".md")
    ]
    files.sort(reverse=True)
    return files


def _load_report(filename):
    """Load and render a report file. Returns (raw_text, html_or_None)."""
    path = os.path.join(EXPORT_DIR, filename)
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (FileNotFoundError, OSError):
        return None, None
    return text, _render_md(text)


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

def _load_profile():
    with open(PROFILE_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _parse_settings_form(form, current_profile):
    """Apply controlled form data onto a deep copy of current_profile."""
    profile = copy.deepcopy(current_profile)

    def get_int(key, default, lo=1, hi=200):
        try:
            return max(lo, min(hi, int(form.get(key, default))))
        except (ValueError, TypeError):
            return default

    def get_bool(key):
        return form.get(key) == "on"

    def get_list(key):
        return [ln.strip() for ln in form.get(key, "").splitlines() if ln.strip()]

    # --- Export ---
    exp = profile.setdefault("export", {})
    exp["balanced_by_source"]    = get_bool("export_balanced")
    exp["max_per_source"]        = get_int("export_max_per_source", 3, 1, 20)
    exp["target_per_source"]     = get_int("export_target_per_source", 3, 1, 20)
    exp["global_target"]         = get_int("export_global_target", 12, 1, 50)
    exp["allow_source_fallback"] = get_bool("export_allow_fallback")
    include = []
    if form.get("export_inc_selected"):
        include.append("selected")
    if form.get("export_inc_review"):
        include.append("review")
    if include:
        exp["include_statuses"] = include

    # --- WTTJ (no enabled flag, always active via CLI) ---
    wttj = profile.setdefault("wttj", {})
    wttj["max_jobs"]  = get_int("wttj_max_jobs",  50, 1, 500)
    wttj["max_pages"] = get_int("wttj_max_pages",  3, 1,  10)

    # --- HelloWork ---
    hw = profile.setdefault("hellowork", {})
    hw["enabled"]  = get_bool("hw_enabled")
    hw["max_jobs"] = get_int("hw_max_jobs", 20, 1, 200)

    # --- LinkedIn ---
    li = profile.setdefault("linkedin", {})
    li["enabled"]       = get_bool("li_enabled")
    li["interactive"]   = get_bool("li_interactive")
    li["limit_per_run"] = get_int("li_limit", 3, 1, 20)

    # --- Indeed ---
    ind = profile.setdefault("indeed", {})
    ind["enabled"]           = get_bool("in_enabled")
    ind["max_pages_per_run"] = get_int("in_max_pages", 1, 1, 5)

    # --- Pipeline ---
    pipe = profile.setdefault("pipeline", {})
    pipe["pending_limit"] = get_int("pipeline_pending_limit", 10, 1, 100)

    # --- Ciblage (mandatory lists) ---
    titles = get_list("target_titles")
    if titles:
        profile["target_titles"] = titles

    locs = get_list("locations")
    if locs:
        profile["locations"] = locs

    must = get_list("must_have")
    if must:
        profile["must_have_keywords"] = must

    nice = get_list("nice_to_have")
    if nice:
        profile["nice_to_have_keywords"] = nice

    # exclude_keywords may be empty
    profile["exclude_keywords"] = get_list("exclude_kw")

    return profile


def _validate_settings(profile):
    """Return list of error strings, empty if valid."""
    errors = []
    if not profile.get("target_titles"):
        errors.append("Les titres cibles (target_titles) ne peuvent pas être vides.")
    if not profile.get("must_have_keywords"):
        errors.append("Les mots-clés must-have ne peuvent pas être vides.")
    if not profile.get("locations"):
        errors.append("Les localisations ne peuvent pas être vides.")
    exp = profile.get("export", {})
    if exp.get("max_per_source", 1) < 1:
        errors.append("max_per_source doit être ≥ 1.")
    return errors


def _save_profile(profile):
    """Backup current profile, write updated one. Returns backup path."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts          = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"candidate_profile_{ts}.yaml")
    shutil.copy2(PROFILE_PATH, backup_path)

    yaml_text = yaml.dump(profile, allow_unicode=True, default_flow_style=False, sort_keys=False)
    # Validate round-trip
    yaml.safe_load(yaml_text)

    with open(PROFILE_PATH, "w", encoding="utf-8") as f:
        f.write(yaml_text)
    return backup_path


def _list_backups():
    """Return last 5 backup filenames, newest first."""
    if not os.path.isdir(BACKUP_DIR):
        return []
    files = [fn for fn in os.listdir(BACKUP_DIR) if fn.endswith(".yaml")]
    files.sort(reverse=True)
    return files[:5]


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__, template_folder="templates")
app.secret_key = "job-bot-local-v2"


# ── Run courant ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    jobs   = _current_jobs()
    marked = request.args.get("marked")
    # per-platform counts for filter tabs
    counts = {}
    for j in jobs:
        p = j.get("platform", "unknown")
        counts[p] = counts.get(p, 0) + 1
    pending_count = _count_pending()
    try:
        pending_limit = _load_profile().get("pipeline", {}).get("pending_limit", 10)
    except Exception:
        pending_limit = 10
    return render_template(
        "index.html",
        jobs=jobs,
        counts=counts,
        marked=int(marked) if marked and marked.isdigit() else None,
        platform_labels=PLATFORM_LABELS,
        status_labels=STATUS_LABELS,
        reason_labels=REASON_LABELS,
        run_state=dict(_run_state),
        pending_count=pending_count,
        pending_limit=pending_limit,
    )


@app.route("/mark", methods=["POST"])
def mark():
    decisions = {
        key[len("decision_"):]: val
        for key, val in request.form.items()
        if key.startswith("decision_") and val
    }
    updated = _apply_decisions(decisions)
    return redirect(url_for("index", marked=updated))


# ── Run buttons ──────────────────────────────────────────────────────────────

@app.route("/run/full", methods=["POST"])
def run_full():
    _start_run("Collecter + matcher + exporter", "--wttj-interactive")
    return redirect(url_for("index"))


@app.route("/run/matching", methods=["POST"])
def run_matching():
    _start_run("Recalculer scoring + rapport", "--from-step=3")
    return redirect(url_for("index"))


@app.route("/run/export", methods=["POST"])
def run_export():
    _start_run("Régénérer rapport seul", "--from-step=4")
    return redirect(url_for("index"))


@app.route("/api/run/status")
def api_run_status():
    with _run_lock:
        state = dict(_run_state)
    return jsonify(state)


@app.route("/api/cleanup/analyze")
def api_cleanup_analyze():
    """Return cleanup analysis (dry-run) without modifying anything."""
    try:
        from app.src.jobs.cleanup_jobs import run_analysis
        analysis = run_analysis()
        jr = analysis["jobs_raw"]
        tr = analysis["tracking"]
        return jsonify({
            "jobs_raw": {
                "total":     jr["total"],
                "to_keep":   len(jr["to_keep"]),
                "to_remove": len(jr["to_remove"]),
            },
            "tracking": {
                "total":     tr["total"],
                "to_keep":   len(tr["to_keep"]),
                "to_remove": len(tr["to_remove"]),
            },
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/cleanup/apply", methods=["POST"])
def api_cleanup_apply():
    """Apply cleanup: backup + remove non-manual entries from jobs_raw, tracking, and derived files."""
    with _run_lock:
        if _run_state["running"]:
            return jsonify({"error": "Un run est en cours — réessayez après."}), 409
    try:
        from app.src.jobs.cleanup_jobs import run_analysis, apply_cleanup
        analysis = run_analysis()
        jr = analysis["jobs_raw"]
        tr = analysis["tracking"]
        if not jr["to_remove"] and not tr["to_remove"]:
            return jsonify({
                "jobs_raw_removed": 0, "jobs_raw_kept": jr["total"],
                "tracking_removed": 0, "tracking_kept": tr["total"],
                "backups": [],
            })
        backups = apply_cleanup(analysis)
        return jsonify({
            "jobs_raw_removed": len(jr["to_remove"]),
            "jobs_raw_kept":    len(jr["to_keep"]),
            "tracking_removed": len(tr["to_remove"]),
            "tracking_kept":    len(tr["to_keep"]),
            "backups":          [os.path.basename(b) for b in backups],
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Maintenance ───────────────────────────────────────────────────────────────

@app.route("/maintenance")
def maintenance():
    pending_count = _count_pending()
    try:
        pending_limit = _load_profile().get("pipeline", {}).get("pending_limit", 10)
    except Exception:
        pending_limit = 10
    return render_template(
        "maintenance.html",
        pending_count=pending_count,
        pending_limit=pending_limit,
        run_state=dict(_run_state),
    )


# ── Historique ───────────────────────────────────────────────────────────────

@app.route("/history")
def history():
    return render_template(
        "history.html",
        rows=_history_rows(),
        platform_labels=PLATFORM_LABELS,
        status_labels=STATUS_LABELS,
        run_state=dict(_run_state),
    )


# ── Rapports ─────────────────────────────────────────────────────────────────

@app.route("/reports")
def reports():
    files    = _list_reports()
    selected = request.args.get("file")
    if not selected or selected not in files:
        selected = files[0] if files else None

    raw, html = (None, None)
    if selected:
        raw, html = _load_report(selected)

    return render_template(
        "reports.html",
        files=files,
        selected=selected,
        raw=raw,
        html=html,
        export_dir=EXPORT_DIR,
        run_state=dict(_run_state),
    )


# ── Paramètres ───────────────────────────────────────────────────────────────

@app.route("/settings", methods=["GET", "POST"])
def settings():
    error_msg   = None
    saved_ok    = False
    backup_path = None

    if request.method == "POST":
        with _run_lock:
            if _run_state["running"]:
                error_msg = "Un run est en cours. Veuillez attendre la fin avant de modifier les paramètres."

        if not error_msg:
            try:
                current  = _load_profile()
                updated  = _parse_settings_form(request.form, current)
                errors   = _validate_settings(updated)
                if errors:
                    error_msg = " | ".join(errors)
                else:
                    backup_path = _save_profile(updated)
                    saved_ok    = True
            except Exception as exc:
                error_msg = f"Erreur lors de la sauvegarde : {exc}"

    profile  = _load_profile()
    backups  = _list_backups()
    return render_template(
        "settings.html",
        profile=profile,
        backups=backups,
        error_msg=error_msg,
        saved_ok=saved_ok,
        backup_path=backup_path,
        run_state=dict(_run_state),
    )


# ── Aide ─────────────────────────────────────────────────────────────────────

@app.route("/aide")
def aide():
    return render_template("aide.html", run_state=dict(_run_state))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print()
    print("  job-bot UI  →  http://127.0.0.1:5000")
    print("  Ctrl+C pour arrêter.")
    print()
    app.run(debug=False, host="127.0.0.1", port=5000, use_reloader=False)
