"""
run.py — Point d'entrée unique du pipeline job-bot.

Usage:
    python run.py                   # Smart loop : WTTJ + boucle Indeed → matching → export
    python run.py --skip-wttj      # Smart loop Indeed uniquement (ignore WTTJ)
    python run.py --from-step=2    # Commence à l'étape 2 (scraping Indeed)
    python run.py --from-step=3    # Commence à l'étape 3 (matching)
    python run.py --from-step=4    # Commence à l'étape 4 (export)
    python run.py --force-run      # Bypass la limite pending_limit

Note : --from-step ne déclenche jamais WTTJ. Pour WTTJ standalone :
    python app/src/jobs/collect_wttj.py --interactive --append
    python app/src/jobs/scrape_wttj.py --interactive
    python run.py --from-step=3
"""

import csv
import json
import os
import sys
import subprocess
import yaml

from app.src.logger import setup_logging

CONFIG_PATH        = "app/config/candidate_profile.yaml"
JOBS_RAW_PATH      = "app/data/jobs/jobs_raw.json"
SELECTED_JOBS_PATH = "app/data/jobs/selected_jobs.json"
TRACKING_PATH      = "app/data/tracking.csv"
RUN_STATS_PATH     = "app/data/run_stats.json"

MANUAL_STATUSES = {
    "applied", "ignored", "manually_rejected",
    "already_applied", "rejected_by_company",
}

STEPS = [
    (1, "Collecte des URLs Indeed",       "app/src/jobs/collect_indeed_urls.py"),
    (2, "Scraping des détails",           "app/src/jobs/scrape_indeed.py"),
    (3, "Matching et scoring",            "app/src/matching/matcher.py"),
    (4, "Export des URLs sélectionnées",  "app/src/jobs/export_selected_urls.py"),
]


def parse_skip_wttj():
    return "--skip-wttj" in sys.argv


def parse_wttj_interactive():
    return "--wttj-interactive" in sys.argv


def parse_force_run():
    return "--force-run" in sys.argv


def parse_from_step():
    for arg in sys.argv[1:]:
        if arg.startswith("--from-step"):
            try:
                if "=" in arg:
                    return int(arg.split("=")[1])
                idx = sys.argv.index(arg)
                return int(sys.argv[idx + 1])
            except (ValueError, IndexError):
                print("Usage: python run.py [--from-step=N]  (N entre 1 et 4)")
                sys.exit(1)
    return None


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def count_raw_jobs():
    try:
        with open(JOBS_RAW_PATH, "r", encoding="utf-8") as f:
            return len(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return 0


def count_results():
    """Return (n_selected, n_review) from selected_jobs.json."""
    try:
        with open(SELECTED_JOBS_PATH, "r", encoding="utf-8") as f:
            jobs = json.load(f)
        n_selected = sum(1 for j in jobs if j.get("decision") == "selected")
        n_review   = sum(1 for j in jobs if j.get("decision") == "review")
        return n_selected, n_review
    except (FileNotFoundError, json.JSONDecodeError):
        return 0, 0


def count_results_by_source():
    """Return {platform: {selected: N, review: N}} from selected_jobs.json."""
    try:
        with open(SELECTED_JOBS_PATH, "r", encoding="utf-8") as f:
            jobs = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    result = {}
    for j in jobs:
        p = j.get("platform", "unknown")
        d = j.get("decision", "")
        if d not in ("selected", "review"):
            continue
        if p not in result:
            result[p] = {"selected": 0, "review": 0}
        result[p][d] = result[p].get(d, 0) + 1
    return result


def _count_pending_jobs():
    """Count selected/review jobs in selected_jobs.json not yet manually treated."""
    try:
        with open(SELECTED_JOBS_PATH, "r", encoding="utf-8") as f:
            jobs = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return 0

    manual_urls = set()
    if os.path.exists(TRACKING_PATH):
        with open(TRACKING_PATH, "r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("status", "").strip().lower() in MANUAL_STATUSES:
                    url = row.get("url", "").strip()
                    if url:
                        manual_urls.add(url)

    return sum(
        1 for j in jobs
        if j.get("decision") in ("selected", "review")
        and j.get("url", "").strip() not in manual_urls
    )


def write_run_stats(stats):
    os.makedirs(os.path.dirname(RUN_STATS_PATH), exist_ok=True)
    with open(RUN_STATS_PATH, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)


LOGIN_WALL_EXIT_CODE  = 2
WTTJ_SESSION_EXPIRED  = 3


def run_wttj_steps(logger, interactive=False):
    """Run WTTJ collect + scrape, standard or interactive mode.

    Both steps are non-fatal: session expired or block → log + return False.
    The pipeline continues with Indeed regardless.
    """
    mode = "mode interactif" if interactive else "mode standard"
    logger.info(f"[WTTJ] Collecte jobs-matches ({mode})...")
    collect_cmd = [sys.executable, "app/src/jobs/collect_wttj.py", "--append"]
    if interactive:
        collect_cmd.append("--interactive")
    if os.environ.get("JOB_BOT_WEB_UI"):
        collect_cmd += ["--auto-confirm", "--auth-timeout", "180"]
    collect = subprocess.run(collect_cmd, env=_subprocess_env())
    if collect.returncode == WTTJ_SESSION_EXPIRED:
        logger.warning(
            "[WTTJ] Session expirée — collecte ignorée.\n"
            "  Pour relancer WTTJ manuellement :\n"
            "    python app/src/jobs/collect_wttj.py --interactive --append\n"
            "    python app/src/jobs/scrape_wttj.py --interactive\n"
            "    python run.py --from-step=3"
        )
        return False
    if collect.returncode != 0:
        logger.warning(f"[WTTJ] Collecte interrompue (code {collect.returncode}) — poursuite sans WTTJ.")
        return False

    logger.info("[WTTJ] Enrichissement des descriptions...")
    scrape = subprocess.run(
        [sys.executable, "app/src/jobs/scrape_wttj.py"],
        env=_subprocess_env(),
    )
    if scrape.returncode == WTTJ_SESSION_EXPIRED:
        logger.warning("[WTTJ] Session expirée pendant l'enrichissement — offres WTTJ sans description complète.")
    elif scrape.returncode != 0:
        logger.warning(f"[WTTJ] Enrichissement interrompu (code {scrape.returncode}) — offres WTTJ partiellement enrichies.")

    logger.info("[WTTJ] Étapes WTTJ terminées.")
    return True


def _purge_indeed_jobs():
    """Remove stale Indeed entries from jobs_raw.json, keep only WTTJ jobs.

    Called before the Indeed loop so Indeed starts fresh without overwriting WTTJ data.
    Returns the number of WTTJ entries kept.
    """
    try:
        with open(JOBS_RAW_PATH, "r", encoding="utf-8") as f:
            jobs = json.load(f)
        wttj_jobs = [j for j in jobs if j.get("platform") == "wttj"]
        with open(JOBS_RAW_PATH, "w", encoding="utf-8") as f:
            json.dump(wttj_jobs, f, ensure_ascii=False, indent=2)
        return len(wttj_jobs)
    except (FileNotFoundError, json.JSONDecodeError):
        return 0


def _subprocess_env():
    """Force UTF-8 stdout/stderr for all child processes (needed on Windows)."""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_step(step_num, name, script, logger, extra_args=None):
    logger.info(f"[Étape {step_num}/4] {name}")
    cmd = [sys.executable, script] + (extra_args or [])
    result = subprocess.run(cmd, env=_subprocess_env())
    if result.returncode == LOGIN_WALL_EXIT_CODE:
        logger.warning(f"[Étape {step_num}/4] Login wall Indeed — poursuite avec les offres déjà collectées.")
        return LOGIN_WALL_EXIT_CODE
    if result.returncode != 0:
        logger.error(f"[Étape {step_num}/4] ÉCHEC (code retour {result.returncode})")
        return False
    logger.info(f"[Étape {step_num}/4] OK")
    return True


def run_hellowork_step(logger, hw_limit):
    """Run HelloWork collection in complementary mode. Non-fatal.

    Returns (added, blocked): added = count of new jobs, blocked = True on error.
    """
    logger.info(f"[HelloWork] Collecte complémentaire (limit={hw_limit})...")
    before = count_raw_jobs()
    result = subprocess.run(
        [sys.executable, "app/src/jobs/collect_hellowork.py",
         "--append", "--limit", str(hw_limit)],
        env=_subprocess_env(),
    )
    if result.returncode != 0:
        logger.warning(
            f"[HelloWork] Collecte interrompue (code {result.returncode}) "
            f"— poursuite sans HelloWork."
        )
        return 0, True
    added = count_raw_jobs() - before
    logger.info(f"[HelloWork] {added} nouvelle(s) offre(s) ajoutée(s).")
    return added, False


def run_linkedin_step(logger, limit):
    """Run LinkedIn collection in interactive mode. Non-fatal.

    Returns (added, blocked): added = count of new jobs, blocked = True on auth/error.
    """
    before = count_raw_jobs()
    linkedin_cmd = [
        sys.executable, "app/src/jobs/collect_linkedin.py",
        "--interactive", "--append", "--limit", str(limit),
    ]
    if os.environ.get("JOB_BOT_WEB_UI"):
        linkedin_cmd += ["--auto-confirm", "--auth-timeout", "180"]
    result = subprocess.run(linkedin_cmd, env=_subprocess_env())
    if result.returncode != 0:
        logger.warning(
            f"[LinkedIn] Collecte interrompue ou bloquée (code {result.returncode})"
            f" — poursuite sans LinkedIn."
        )
        return 0, True
    added = count_raw_jobs() - before
    logger.info(f"[LinkedIn] {added} nouvelle(s) offre(s) ajoutée(s).")
    return added, False


def run_direct(from_step, logger):
    """Run the pipeline from a given step without the smart loop."""
    for step_num, name, script in STEPS:
        if step_num < from_step:
            logger.info(f"[Étape {step_num}/4] Ignorée (--from-step={from_step})")
            continue
        if not run_step(step_num, name, script, logger):
            logger.error("Pipeline interrompu.")
            sys.exit(1)
    logger.info("=== Pipeline job-bot terminé avec succès ===")


def _source_stat(*, enabled, attempted, collected, target, blocked=False, failed=False, message=""):
    """Build a source_stats entry; status is provisional (updated at end of loop)."""
    if not enabled:
        status = "disabled"
    elif not attempted:
        status = "skipped"
    elif failed:
        status = "failed"
    elif blocked:
        status = "blocked"
    else:
        status = "?"      # resolved after final count_results_by_source()
    return {
        "enabled":    enabled,
        "attempted":  attempted,
        "collected":  collected,
        "exploitable": 0,  # filled at end
        "target":     target,
        "status":     status,
        "message":    message,
    }


def run_smart_loop(config, logger, skip_wttj=False, wttj_interactive=False):
    """Collect from all active sources (WTTJ, HelloWork, LinkedIn, Indeed) then match and export.

    Phase 1 — passe primaire : chaque source activée tente target_per_source exploitables.
    Phase 2 — fallback : si allow_source_fallback et total < global_target, les sources
               saines (HW, Indeed) collectent plus pour compenser les sources bloquées.
    """
    pipeline_cfg   = config.get("pipeline", {})
    target_results = pipeline_cfg.get("target_results", 5)
    max_total_jobs = pipeline_cfg.get("max_total_jobs", 60)

    export_cfg        = config.get("export", {})
    target_per_source = export_cfg.get("target_per_source", 3)
    global_target     = export_cfg.get("global_target", 12)
    allow_fallback    = export_cfg.get("allow_source_fallback", True)

    # HelloWork — source primaire indépendante (plus de dépendance à WTTJ)
    hellowork_cfg     = config.get("hellowork", {})
    hellowork_enabled = hellowork_cfg.get(
        "enabled", pipeline_cfg.get("enable_hellowork_complementary", False)
    )
    hw_limit = hellowork_cfg.get(
        "limit_per_run", pipeline_cfg.get("hellowork_limit_complementary", 10)
    )

    indeed_cfg       = config.get("indeed", {})
    indeed_enabled   = indeed_cfg.get("enabled", True)
    indeed_max_pages = indeed_cfg.get("max_pages_per_run", 1)

    logger.info(
        f"Mode boucle intelligente — cible: {target_per_source}/source, "
        f"global: {global_target}, max collecte: {max_total_jobs}"
    )

    # --- Phase WTTJ ---
    source_stats = {}
    n_selected, n_review = 0, 0
    wttj_kept = 0

    if skip_wttj:
        logger.info("[WTTJ] Ignoré (--skip-wttj).")
        source_stats["wttj"] = _source_stat(
            enabled=True, attempted=False, collected=0,
            target=target_per_source, message="--skip-wttj",
        )
    else:
        wttj_ok = run_wttj_steps(logger, interactive=wttj_interactive)
        wttj_kept = _purge_indeed_jobs()
        logger.info(f"[WTTJ] {wttj_kept} offre(s) conservée(s) dans jobs_raw.json.")

        if not wttj_ok:
            source_stats["wttj"] = _source_stat(
                enabled=True, attempted=True, collected=wttj_kept,
                target=target_per_source, failed=True, message="Collecte WTTJ échouée",
            )
        elif wttj_kept > 0:
            logger.info("[WTTJ] Matching après enrichissement...")
            if not run_step(3, "Matching et scoring", STEPS[2][2], logger):
                logger.error("Arrêt sur erreur matching.")
                sys.exit(1)
            n_selected, n_review = count_results()
            exploitable = n_selected + n_review
            logger.info(f"[WTTJ] Résultats exploitables : {exploitable}/{target_per_source}")
            source_stats["wttj"] = _source_stat(
                enabled=True, attempted=True, collected=wttj_kept, target=target_per_source,
            )
        else:
            source_stats["wttj"] = _source_stat(
                enabled=True, attempted=True, collected=0, target=target_per_source,
            )

    # --- Phase HelloWork (source primaire indépendante) ---
    hw_added = 0
    if hellowork_enabled:
        hw_added, hw_blocked = run_hellowork_step(logger, hw_limit)
        if hw_added > 0:
            logger.info("[HelloWork] Matching après collecte HelloWork...")
            if not run_step(3, "Matching et scoring", STEPS[2][2], logger):
                logger.error("Arrêt sur erreur matching.")
                sys.exit(1)
            n_selected, n_review = count_results()
            logger.info(f"[HelloWork] Résultats exploitables : {n_selected + n_review}/{target_per_source}")
        source_stats["hellowork"] = _source_stat(
            enabled=True, attempted=True, collected=hw_added,
            target=target_per_source, blocked=hw_blocked,
            message="Collecte interrompue" if hw_blocked else "",
        )
    else:
        source_stats["hellowork"] = _source_stat(
            enabled=False, attempted=False, collected=0, target=target_per_source,
        )

    # --- Phase LinkedIn ---
    linkedin_cfg         = config.get("linkedin", {})
    linkedin_enabled     = linkedin_cfg.get("enabled", False)
    linkedin_interactive = linkedin_cfg.get("interactive", True)
    linkedin_limit       = linkedin_cfg.get("limit_per_run", 3)

    linkedin_added = 0
    if linkedin_enabled:
        if linkedin_interactive:
            logger.info(f"[LinkedIn] Collecte interactive (limit={linkedin_limit})...")
            linkedin_added, linkedin_blocked = run_linkedin_step(logger, linkedin_limit)
            if linkedin_added > 0:
                logger.info("[LinkedIn] Matching après collecte LinkedIn...")
                if not run_step(3, "Matching et scoring", STEPS[2][2], logger):
                    logger.error("Arrêt sur erreur matching.")
                    sys.exit(1)
                n_selected, n_review = count_results()
                logger.info(f"[LinkedIn] Résultats exploitables : {n_selected + n_review}/{target_per_source}")
            else:
                logger.info("[LinkedIn] Aucune nouvelle offre — matching LinkedIn ignoré.")
            source_stats["linkedin"] = _source_stat(
                enabled=True, attempted=True, collected=linkedin_added,
                target=target_per_source, blocked=linkedin_blocked,
                message="Auth LinkedIn requise ou blocage" if linkedin_blocked else "",
            )
        else:
            logger.warning("[LinkedIn] Mode non-interactif non supporté — LinkedIn ignoré.")
            source_stats["linkedin"] = _source_stat(
                enabled=True, attempted=False, collected=0,
                target=target_per_source, message="mode non-interactif",
            )
    else:
        logger.info("[LinkedIn] Source désactivée.")
        source_stats["linkedin"] = _source_stat(
            enabled=False, attempted=False, collected=0, target=target_per_source,
        )

    # --- Phase Indeed ---
    total_collected   = count_raw_jobs()
    indeed_before     = total_collected
    login_wall        = False
    indeed_iterations = 0

    if not indeed_enabled:
        logger.info("[Indeed] Désactivé (indeed.enabled=false).")
        source_stats["indeed"] = _source_stat(
            enabled=False, attempted=False, collected=0, target=target_per_source,
        )
        source_stats["indeed"]["pages"] = 0
    else:
        logger.info(f"[Indeed] Source active — max_pages_per_run={indeed_max_pages}")

        for page_idx in range(indeed_max_pages):
            indeed_iterations += 1
            logger.info(f"[Indeed] Page {page_idx + 1}/{indeed_max_pages}")

            if total_collected >= max_total_jobs:
                logger.warning(
                    f"[Indeed] Limite max_total_jobs atteinte "
                    f"({total_collected}/{max_total_jobs}) — arrêt Indeed."
                )
                break

            collect_result = run_step(
                1, "Collecte des URLs Indeed", STEPS[0][2], logger,
                ["--page", str(page_idx), "--append"],
            )
            login_wall = (collect_result == LOGIN_WALL_EXIT_CODE)
            if collect_result is False:
                logger.error("Arrêt sur erreur collecte Indeed.")
                sys.exit(1)

            total_collected = count_raw_jobs()

            if login_wall:
                logger.warning("[Indeed] Login wall — arrêt collecte Indeed, poursuite avec offres existantes.")
                break

            if not run_step(2, "Scraping des détails", STEPS[1][2], logger):
                logger.error("Arrêt sur erreur scraping Indeed.")
                sys.exit(1)

            if not run_step(3, "Matching et scoring", STEPS[2][2], logger):
                logger.error("Arrêt sur erreur matching Indeed.")
                sys.exit(1)

            n_selected, n_review = count_results()
            total_collected = count_raw_jobs()
            logger.info(
                f"[Indeed] Page {page_idx + 1} terminée — "
                f"selected={n_selected}, review={n_review}, total={total_collected}"
            )
        else:
            logger.info(f"[Indeed] Limite pages par run atteinte ({indeed_max_pages}) — arrêt Indeed.")

        source_stats["indeed"] = _source_stat(
            enabled=True, attempted=True,
            collected=total_collected - indeed_before,
            target=target_per_source,
            blocked=login_wall,
            message="Login wall Indeed" if login_wall else "",
        )
        source_stats["indeed"]["pages"] = indeed_iterations

    # --- Reconcile per-source exploitable counts ---
    # After all sources + matching, get final per-platform exploitable counts and
    # resolve provisional "?" statuses. Terminal statuses (blocked/failed/skipped/disabled)
    # are preserved as-is.
    _TERMINAL = {"disabled", "skipped", "blocked", "failed"}
    final_by_src = count_results_by_source()
    for _p, _data in source_stats.items():
        if _data["status"] not in _TERMINAL:
            _ps  = final_by_src.get(_p, {})
            _exp = _ps.get("selected", 0) + _ps.get("review", 0)
            _tgt = _data.get("target", target_per_source)
            _data["exploitable"] = _exp
            if _exp >= _tgt:
                _data["status"] = "ok"
            elif _exp > 0:
                _data["status"]  = "partial"
                _data["message"] = f"{_exp}/{_tgt} exploitables"
            else:
                _data["status"] = "empty"

    # Log source summary (passe primaire)
    for _p, _data in source_stats.items():
        _label = _p.upper().ljust(10)
        _exp   = _data.get("exploitable", 0)
        _tgt   = _data.get("target", target_per_source)
        _st    = _data.get("status", "?")
        _msg   = _data.get("message", "")
        logger.info(f"[Source] {_label}: {_exp}/{_tgt} exploitables — {_st}" + (f" ({_msg})" if _msg else ""))

    # --- Phase fallback (allow_source_fallback) ---
    # Si le global_target n'est pas atteint après la passe primaire, les sources saines
    # (HelloWork, Indeed) collectent en supplément pour compenser les sources bloquées.
    if allow_fallback:
        total_exploitable = sum(d.get("exploitable", 0) for d in source_stats.values())
        shortfall = global_target - total_exploitable

        if shortfall > 0:
            logger.info(
                f"[Fallback] {total_exploitable}/{global_target} exploitables — "
                f"shortfall={shortfall}, tentative sur sources disponibles..."
            )
            ran_fallback = False

            # HelloWork fallback : si HW est sain (pas disabled/failed/blocked)
            hw_st      = source_stats.get("hellowork", {}).get("status", "disabled")
            hw_healthy = hellowork_enabled and hw_st not in ("disabled", "failed", "blocked")
            if hw_healthy:
                extra_hw = min(shortfall * 5, 30)
                logger.info(f"[Fallback/HelloWork] Collecte complémentaire ({extra_hw} offres)...")
                fb_hw_added, _ = run_hellowork_step(logger, extra_hw)
                if fb_hw_added > 0:
                    if not run_step(3, "Matching et scoring", STEPS[2][2], logger):
                        logger.error("Arrêt sur erreur matching (fallback).")
                        sys.exit(1)
                    ran_fallback = True
                prev = source_stats["hellowork"].get("message", "")
                source_stats["hellowork"]["message"] = (
                    (prev + "; " if prev else "") + f"fallback +{fb_hw_added}"
                )

            # Indeed fallback : si Indeed est sain et non login-wallé
            indeed_st      = source_stats.get("indeed", {}).get("status", "disabled")
            indeed_healthy = (
                indeed_enabled
                and not login_wall
                and indeed_st not in ("disabled", "failed", "blocked")
            )
            if indeed_healthy:
                fb_pages = pipeline_cfg.get("max_indeed_pages_complementary", 1)
                for fp in range(fb_pages):
                    logger.info(f"[Fallback/Indeed] Page supplémentaire {fp + 1}/{fb_pages}...")
                    cr = run_step(
                        1, "Collecte URLs Indeed", STEPS[0][2], logger,
                        ["--page", str(indeed_iterations + fp), "--append"],
                    )
                    if cr is False:
                        break
                    if cr == LOGIN_WALL_EXIT_CODE:
                        login_wall = True
                        source_stats["indeed"]["message"] = (
                            (source_stats["indeed"].get("message", "") + "; " if source_stats["indeed"].get("message") else "")
                            + "Login wall (fallback)"
                        )
                        break
                    if not run_step(2, "Scraping des détails", STEPS[1][2], logger):
                        break
                    if not run_step(3, "Matching et scoring", STEPS[2][2], logger):
                        break
                    ran_fallback = True

            if ran_fallback:
                n_selected, n_review = count_results()
                total_collected = count_raw_jobs()
                # Re-réconciliation post-fallback
                final_by_src = count_results_by_source()
                for _p, _data in source_stats.items():
                    if _data["status"] not in _TERMINAL:
                        _ps  = final_by_src.get(_p, {})
                        _exp = _ps.get("selected", 0) + _ps.get("review", 0)
                        _tgt = _data.get("target", target_per_source)
                        _data["exploitable"] = _exp
                        if _exp >= _tgt:
                            _data["status"] = "ok"
                        elif _exp > 0:
                            _data["status"] = "partial"
                            if "fallback" not in _data.get("message", ""):
                                _data["message"] = f"{_exp}/{_tgt} exploitables"
                        else:
                            _data["status"] = "empty"
                # Log post-fallback
                for _p, _data in source_stats.items():
                    _exp = _data.get("exploitable", 0)
                    _tgt = _data.get("target", target_per_source)
                    _st  = _data.get("status", "?")
                    _msg = _data.get("message", "")
                    logger.info(
                        f"[Source post-fallback] {_p.upper().ljust(10)}: "
                        f"{_exp}/{_tgt} exploitables — {_st}"
                        + (f" ({_msg})" if _msg else "")
                    )
        else:
            logger.info(
                f"[Fallback] {total_exploitable}/{global_target} exploitables — "
                f"global_target atteint, pas de fallback nécessaire."
            )

    # --- Phase export ---

    # Login wall sans aucune offre en base : rien à exporter
    if login_wall and total_collected == 0:
        logger.warning("Login wall dès la première collecte — aucune offre collectée, export annulé.")
        write_run_stats({
            "iterations": indeed_iterations,
            "pages_collected": 0,
            "total_collected": 0,
            "n_selected": 0,
            "n_review": 0,
            "target_results": target_results,
            "login_wall": True,
            "sources": source_stats,
        })
        logger.info("=== Pipeline job-bot terminé (sans export) ===")
        return

    # Login wall avec offres existantes — matching final avant export
    if login_wall and total_collected > 0:
        logger.info(
            f"[Indeed] Login wall — {total_collected} offre(s) en base, "
            f"lancement du matching avant export..."
        )
        if not run_step(3, "Matching et scoring", STEPS[2][2], logger):
            logger.error("Arrêt sur erreur matching.")
            sys.exit(1)
        n_selected, n_review = count_results()
        logger.info(f"Résultats post-matching : selected={n_selected}, review={n_review}")

    write_run_stats({
        "iterations": indeed_iterations,
        "pages_collected": indeed_iterations,
        "total_collected": total_collected,
        "n_selected": n_selected,
        "n_review": n_review,
        "target_results": target_results,
        "login_wall": login_wall,
        "sources": source_stats,
    })

    if not run_step(4, "Export des URLs sélectionnées", STEPS[3][2], logger):
        logger.error("Arrêt sur erreur export.")
        sys.exit(1)

    logger.info("=== Pipeline job-bot terminé avec succès ===")


def main():
    from_step        = parse_from_step()
    skip_wttj        = parse_skip_wttj()
    wttj_interactive = parse_wttj_interactive()
    force_run        = parse_force_run()
    logger           = setup_logging()

    if from_step is not None:
        # --from-step=N : pipeline Indeed direct, WTTJ jamais déclenché
        logger.info(f"=== Pipeline job-bot démarré (depuis étape {from_step}) ===")
        run_direct(from_step, logger)
    else:
        config        = load_config()
        pending_limit = config.get("pipeline", {}).get("pending_limit", 10)
        if not force_run:
            pending_count = _count_pending_jobs()
            if pending_count > pending_limit:
                logger.warning(
                    f"[Blocage] {pending_count} offre(s) non traitée(s) > seuil {pending_limit}.\n"
                    f"  Traitez les offres dans tracking.csv ou relancez avec --force-run pour bypasser."
                )
                sys.exit(0)
        logger.info("=== Pipeline job-bot démarré (mode boucle intelligente) ===")
        run_smart_loop(config, logger, skip_wttj=skip_wttj, wttj_interactive=wttj_interactive)


if __name__ == "__main__":
    main()
