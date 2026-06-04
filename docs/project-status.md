# job-bot — État du projet

Dernière mise à jour : 2026-05-06

---

## Architecture actuelle

### Pipeline complet (une seule commande)
```
python run.py --wttj-interactive
```

### Flux d'exécution
```
1. WTTJ — collect_wttj.py --interactive --append
           scrape_wttj.py --interactive
           matcher.py  (matching intermédiaire pour log)

2. HelloWork — collect_hellowork.py --append --limit N
               (description extraite à la collecte, pas de scraping séparé)
               matcher.py  (matching intermédiaire pour log)

3. LinkedIn — collect_linkedin.py --interactive --append --limit N
              (mode interactif, profil Chrome dédié, filtre titre au collecte)
              matcher.py  (matching intermédiaire si nouvelles offres)
              ↳ toujours non bloquant : annulation/CAPTCHA → pipeline continue

4. Indeed — collect_indeed_urls.py --page 0 --append
            scrape_indeed.py
            matcher.py  (matching intermédiaire)
            ↳ si login wall : non bloquant, poursuite avec offres existantes

5. Export — export_selected_urls.py
            ├── déduplication potentielle (titre + entreprise normalisés)
            ├── → selected_urls.txt  (top 10 URLs principales)
            └── → report_YYYY-MM-DD.md  (rapport Markdown complet)
```

### Séquençage interne run.py
- `_purge_indeed_jobs()` appelé après WTTJ : purge toutes les offres non-WTTJ de la session précédente. HelloWork, LinkedIn et Indeed re-collectent à chaque run avec `--append`.
- Le matching est lancé après chaque source si de nouvelles offres ont été ajoutées.
- `indeed.enabled`, `hellowork.enabled`, `linkedin.enabled` dans le YAML contrôlent les sources sans toucher run.py.

---

## Workflow quotidien

| Cas | Commande |
|---|---|
| Run standard | `python run.py --wttj-interactive` |
| Session WTTJ expirée | Même commande — reconnexion gérée en interactif |
| Sans WTTJ (test / Chrome cassé) | `python run.py --skip-wttj` |
| Relancer matching + export | `python run.py --from-step=3` |
| Relancer export seul | `python run.py --from-step=4` |

> `--from-step=3` et `--from-step=4` ne déclenchent aucun scraping (LinkedIn inclus).

### Après le run
1. Ouvrir `app/data/exports/report_YYYY-MM-DD.md`
2. Consulter le header (stats par source, doublons potentiels éventuels)
3. Parcourir les offres ⭐ SELECTED et 🔍 REVIEW
4. Marquer les offres traitées avec `mark_job.py`

```bash
python app/src/jobs/mark_job.py --id "wttj-devops-engineer_paris" --status applied
python app/src/jobs/mark_job.py --id "hellowork-78526340" --status ignored
python app/src/jobs/mark_job.py --id "linkedin-4409356977" --status manually_rejected
```

---

## Sources intégrées

| Source | Technologie | Intégré run.py | Quota par run | Non bloquant |
|---|---|---|---|---|
| WTTJ | Selenium (compte connecté) | ✅ `--wttj-interactive` | `max_pages: 3`, `max_jobs: 50` | ✅ (exit 3 ignoré) |
| HelloWork | requests + BeautifulSoup | ✅ automatique | `max_jobs: 20` configurable | ✅ |
| LinkedIn | Selenium (interactif, compte connecté) | ✅ après HelloWork | `limit_per_run: 3` configurable | ✅ |
| Indeed | Selenium (public) | ✅ automatique | `max_pages_per_run: 1` | ✅ (login wall) |

### Comportement en cas d'échec
- **WTTJ session expirée** (exit 3) : log warning, pipeline continue sans WTTJ
- **HelloWork erreur réseau** : log warning, pipeline continue sans HelloWork
- **LinkedIn annulation / CAPTCHA** (exit ≠ 0) : log warning, pipeline continue vers Indeed
- **Indeed login wall** (exit 2) : log warning, matching/export sur WTTJ + HelloWork + LinkedIn existants

### Config YAML par source
```yaml
linkedin:
  enabled: true
  interactive: true        # seul mode supporté (V1)
  limit_per_run: 3         # offres max à enrichir par run
  search_urls: [...]       # utilisé par collect_linkedin.py standalone

hellowork:
  enabled: true
  max_jobs: 20
  search_urls: [...]

indeed:
  enabled: true
  max_pages_per_run: 1
  search_queries: [...]

pipeline:
  target_results: 5
  enable_hellowork_complementary: true
  hellowork_limit_complementary: 10
```

---

## LinkedIn — fonctionnement détaillé

### Collecte (collect_linkedin.py)
- Mode interactif uniquement : Chrome s'ouvre, l'utilisateur se connecte et valide, puis Entrée
- Profil Chrome dédié : `app/data/chrome_profile/linkedin` (séparé du profil WTTJ)
- Phase 1 : extraction des métadonnées des cards visibles (`li[data-occludable-job-id]`)
  - Filtre titre actif avant enrichissement : Senior, Expert, Lead, Staff, Architect, Confirmé, Alternance, Stage, Freelance, etc.
  - Les titres filtrés ne consomment pas le `--limit`
- Phase 2 : clic panneau JS pur → vérification URL → extraction description (`#job-details`)
  - Fallback : navigation directe `/jobs/view/{id}/`
- Toujours lancé avec `--append` depuis run.py : ne modifie jamais les offres WTTJ/HelloWork/Indeed

### Matching (matcher.py)
- Les rejets forts sont identiques aux autres sources : `too_senior`, `experience_too_high`, `excluded_title_keyword`, `no_must_have`, `score_too_low`
- **Règle prudente** : toute offre LinkedIn qui aurait été `selected` est downgradée en `review / linkedin_manual_review`
- Raison : validation manuelle requise pendant la phase de rodage. Sera levée après confirmation de qualité des résultats sur plusieurs runs.

### Commandes standalone
```bash
# Test collecte (3 offres, sans modifier jobs_raw)
python app/src/jobs/collect_linkedin.py --interactive --limit 3

# Collecte avec append (mode run.py)
python app/src/jobs/collect_linkedin.py --interactive --append --limit 5
```

---

## Déduplication / doublons potentiels

### Principe
La même offre peut exister sur plusieurs sources avec des URLs différentes.

### Stratégie V1 (conservative)
- **Quand** : dans `export_selected_urls.py`, après chargement de `selected_jobs.json`
- **Clé** : `(normalize(title), normalize(company))` — exact match après suppression accents, ponctuation, casse
- **Résultat** : les jobs triés par score sont répartis :
  - `main_jobs` : jusqu'à `MAX_URLS=10` offres uniques → export URLs + rapport principal
  - `potential_duplicates` : même clé qu'une offre déjà retenue → section `⚠️` en bas du rapport

### Ce qui n'est PAS modifié
- `jobs_raw.json`, `selected_jobs.json`, `tracking.csv` : inchangés

---

## Fichiers clés

| Fichier | Rôle | Intégré run.py |
|---|---|---|
| `run.py` | Orchestrateur principal | — |
| `app/config/candidate_profile.yaml` | Profil candidat + config pipeline + config sources | — |
| `.claude/settings.json` | Permissions Claude Code (auto-approve safe commands) | — |
| `app/src/logger.py` | Logger centralisé | — |
| `app/src/jobs/collect_indeed_urls.py` | Collecte URLs Indeed | ✅ étape 1 |
| `app/src/jobs/scrape_indeed.py` | Enrichit descriptions Indeed | ✅ étape 2 |
| `app/src/matching/matcher.py` | Scoring + classification (règle prudente LinkedIn) | ✅ étape 3 |
| `app/src/jobs/export_selected_urls.py` | Rapport Markdown + URLs + déduplication | ✅ étape 4 |
| `app/src/jobs/collect_wttj.py` | Collecte jobs-matches WTTJ | ✅ via run.py |
| `app/src/jobs/scrape_wttj.py` | Enrichit descriptions WTTJ | ✅ via run.py |
| `app/src/jobs/collect_hellowork.py` | Collecte offres HelloWork (requests+BS4) | ✅ via run.py |
| `app/src/jobs/collect_linkedin.py` | Collecte offres LinkedIn (Selenium interactif) | ✅ via run.py |
| `app/src/jobs/mark_job.py` | Mise à jour statut manuel dans tracking.csv | ❌ utilitaire |
| `app/src/jobs/clean_wttj_jobs.py` | Purge one-shot entrées WTTJ | ❌ utilitaire |
| `app/src/jobs/setup_wttj_session.py` | — | ❌ **obsolète** |
| `app/src/jobs/update_job_description.py` | — | ❌ **obsolète** |
| `app/data/jobs/jobs_raw.json` | Offres collectées toutes sources | généré |
| `app/data/jobs/selected_jobs.json` | Offres selected + review (avant dédup) | généré |
| `app/data/jobs/selected_urls.txt` | Top 10 URLs principales (après dédup) | généré |
| `app/data/tracking.csv` | Historique complet de tous les runs | persistant |
| `app/data/run_stats.json` | Stats du dernier run | généré |
| `app/data/exports/report_YYYY-MM-DD.md` | Rapport lisible avec stats par source | généré |
| `app/data/chrome_profile/wttj/` | Profil Chrome WTTJ (session persistante) | — |
| `app/data/chrome_profile/linkedin/` | Profil Chrome LinkedIn (session persistante) | — |

---

## Règles de matching

### Scoring (matcher.py)
| Condition | Points |
|---|---|
| Target title dans le titre | +40 |
| Target title dans la description | +20 |
| Localisation dans le titre/loc | +20 |
| Localisation dans la description | +10 |
| Mode de travail (remote/hybride) | +6 |
| Niveau d'expérience junior | +18 |
| Chaque must-have keyword | +10 |
| Chaque nice-to-have keyword | +4 |
| Keyword exclu dans le titre | -35 |
| Marqueur sénior | -45 |

### Arbre de décision (classify_job)
1. Page invalide → `rejected` (invalid_page)
2. Titre exclu (stage/alternance/freelance/architecte) → `rejected` (excluded_title_keyword)
3. Marqueur sénior dans le titre → `rejected` (too_senior)
4. Expérience explicite **> 3 ans** → `rejected` (experience_too_high)
5. 0 must-have → `rejected` (no_must_have)
6. Score < 40 → `rejected` (score_too_low)
7. must-have ≥ 2 ET score ≥ 70 ET target_title :
   - plateforme ≠ linkedin → `selected`
   - plateforme = linkedin → `review` (linkedin_manual_review)
8. must-have ≥ 2 ET score ≥ 70 SANS target_title → `review` (missing_target_title)
9. must-have ≥ 2 ET score 40–69 → `review` (mid_score)
10. must-have = 1 ET score ≥ 50 → `review` (low_must_have)
11. → `rejected` (score_too_low)

### Must-have keywords (rejet si 0 détecté)
`aws` · `terraform` · `docker` · `kubernetes` · `ansible`

---

## Ce qui a été fait

### Fondations
- `run.py` : orchestrateur unique, mode boucle intelligente + `--from-step=N`
- `app/src/logger.py` : logging fichier + console horodaté
- `.gitignore` : exclut PII, logs, profil Chrome, fichiers générés
- `.claude/settings.json` : permissions Claude Code (auto-approve safe commands)

### Matching et classification
- `matcher.py` : scoring multicritère, filtre expérience (15 patterns regex FR+EN), arbre de décision, gate `selected`, règle prudente LinkedIn, écriture dans `tracking.csv`
- `mark_job.py` : mise à jour statut manuel directement dans `tracking.csv`

### Rapport et export
- `export_selected_urls.py` : rapport Markdown (badges ⭐/🔍), stats par source, déduplication V1 (section ⚠️)

### Pipeline Indeed
- `collect_indeed_urls.py` : multi-villes depuis YAML, `--page N`, `--append`, login wall (exit 2)
- `scrape_indeed.py` : skip automatique des offres déjà enrichies
- Source active quotidienne : `indeed.enabled=true`, `indeed.max_pages_per_run=1`

### Pipeline WTTJ
- `collect_wttj.py` : collecte jobs-matches, mode `--interactive`, filtre soft senior, exclusion statuts manuels
- `scrape_wttj.py` : enrichissement descriptions, sélecteurs multi-fallback + JS heuristic

### Pipeline HelloWork
- `collect_hellowork.py` : requests + BeautifulSoup (pas de Selenium), sélecteurs stables `data-cy`
- Intégré dans `run.py` via `run_hellowork_step()`, toujours `--append`

### Pipeline LinkedIn ✅ (2026-05-06)
- `collect_linkedin.py` : Selenium UC mode interactif, profil Chrome dédié, clic panneau JS pur
- Filtre titre pré-enrichissement : Senior, Expert, Lead, Staff, Architect, Confirmé, Alternance, Stage, Freelance, etc. (word-boundary + normalisation accents)
- Parsing title/company/location : détection titre répété, split company/location via marqueurs + regex `, Île-de-France`
- Intégré dans `run.py` via `run_linkedin_step()` après HelloWork, avant Indeed
- Règle prudente dans `matcher.py` : `platform=linkedin` + `selected` → `review / linkedin_manual_review`

---

## Bilan d'avancement

| Bloc | % | Note |
|---|---|---|
| Fondations | **90 %** | Stable |
| Matching | **70 %** | Fonctionnel, règle LinkedIn ajoutée ; manque tests unitaires, cv_matcher, LLM |
| Reporting | **85 %** | Stats par source, déduplication V1 ; manque HTML |
| Indeed | **90 %** | Source active quotidienne, non bloquant |
| WTTJ | **90 %** | Intégré dans run.py, stable |
| HelloWork | **85 %** | Intégré dans run.py, requests+BS4 |
| LinkedIn | **80 %** | Intégré, interactif, filtre titre, règle prudente ; sélection directe à valider |
| Stockage / historique | **25 %** | tracking.csv + mark_job.py ; SQLite non fait |
| Multi-sources | **95 %** | 4 sources actives + matching global + déduplication V1 |
| Candidatures | **0 %** | Pas commencé |

---

## Points de vigilance

- **Session WTTJ** : durée de vie du profil Chrome inconnue. Si exit code 3, relancer avec `--wttj-interactive`.
- **Session LinkedIn** : même principe que WTTJ. Si login wall détecté, le pipeline continue sans LinkedIn. Relancer standalone pour reconnecter : `python app/src/jobs/collect_linkedin.py --interactive --limit 3`.
- **Indeed login wall** : fréquent. Non bloquant : le pipeline exporte WTTJ + HelloWork + LinkedIn.
- **LinkedIn limit_per_run=3** : intentionnellement bas pendant la phase de rodage. Augmenter dans le YAML quand la qualité des résultats est confirmée.
- **LinkedIn toujours en review** : la règle `linkedin_manual_review` est volontaire pour éviter les faux positifs. À lever dans `matcher.py` quand la qualité est validée sur plusieurs runs.
- **`_purge_indeed_jobs()`** : supprime toutes les offres non-WTTJ à chaque run complet. HelloWork, LinkedIn et Indeed re-collectent donc à chaque run. Nom trompeur — comportement volontaire.
- **Titres WTTJ** : incluent parfois des métadonnées (secteur, taille entreprise). Non corrigé pour l'instant.
- **Déduplication V1 prudente** : doublons exacts (titre + entreprise normalisés) uniquement.
- **`run_stats.json` périmé** : si une collecte standalone modifie `jobs_raw.json` avant `--from-step=3`, le rapport affiche une ligne neutre (stale-check). Comportement intentionnel.
- **`setup_wttj_session.py`** et **`update_job_description.py`** : obsolètes, ne plus utiliser.

---

## Prochaines priorités

**P1 — Stabiliser les runs quotidiens avec LinkedIn**
Effectuer 3-5 runs complets (`python run.py --wttj-interactive`) sur des jours consécutifs.
Observer : qualité des offres LinkedIn en review ? Titres filtrés pertinents ? Descriptions complètes ?
Si la qualité est confirmée → lever la règle `linkedin_manual_review` dans `matcher.py`.

**P2 — Augmenter limit_per_run LinkedIn si pertinent**
Passer `linkedin.limit_per_run` de 3 à 5 ou 10 dans le YAML après validation P1.

**P3 — Observer la section "Doublons potentiels"**
Avec 4 sources actives, des doublons cross-sources peuvent apparaître.
Vérifier si la détection (titre + entreprise exacts) génère des faux positifs.

**P4 — Titres WTTJ (non urgent)**
Les titres WTTJ contiennent des métadonnées de la page. Non prioritaire.

**P5 — Export HTML (optionnel)**
Le rapport Markdown est lisible mais un rendu HTML faciliterait la navigation.

---

## Commandes de test et utilitaires

```bash
# Run complet (WTTJ + HelloWork + LinkedIn + Indeed)
python run.py --wttj-interactive

# Run sans WTTJ (HelloWork + LinkedIn + Indeed)
python run.py --skip-wttj

# Valider le matching sans scraping
python run.py --from-step=3

# Valider l'export seul
python run.py --from-step=4

# Test LinkedIn standalone (3 offres, sans modifier jobs_raw)
python app/src/jobs/collect_linkedin.py --interactive --limit 3

# Test HelloWork standalone (5 offres)
python app/src/jobs/collect_hellowork.py --append --limit 5

# Test enrichissement WTTJ limité
python app/src/jobs/scrape_wttj.py --interactive --limit 3

# Marquer une offre traitée
python app/src/jobs/mark_job.py --id "wttj-devops-engineer_paris" --status applied
python app/src/jobs/mark_job.py --id "hellowork-78526340" --status ignored
python app/src/jobs/mark_job.py --id "linkedin-4409356977" --status manually_rejected

# Purge WTTJ avec backup (one-shot)
python app/src/jobs/clean_wttj_jobs.py
```
