# Interface web locale — job-bot

*Dernière mise à jour : 2026-06-04*

## Lancer l'interface

```bash
python web_ui.py
```

Puis ouvrir **http://127.0.0.1:5000** dans un navigateur.

> Alternative : `python app/src/web/app.py` (exécution directe, même résultat).

L'interface écoute uniquement sur `127.0.0.1` (localhost). Elle n'est pas exposée sur le réseau.

---

## Navigation

| Page | URL | Rôle |
|---|---|---|
| Run courant | `/` | Offres exploitables + actions de run |
| Historique | `/history` | Toutes les offres connues (tracking.csv) |
| Rapports | `/reports` | Consultation des rapports Markdown |
| Paramètres | `/settings` | Édition contrôlée de candidate_profile.yaml |
| Maintenance | `/maintenance` | Nettoyage + compteur offres en attente |
| Aide | `/aide` | Guide non-technique de l'outil |

---

## Page : Run courant (`/`)

### Alerte pending (blocage run)

Si le nombre d'offres non traitées dépasse `pipeline.pending_limit` (défaut : 10) :
- Bannière rouge : run complet bloqué
- Bouton 🚀 désactivé avec tooltip explicatif
- Solution : traiter les offres dans cette page, nettoyer via Maintenance, ou augmenter le seuil dans Paramètres

### Boutons d'action

| Bouton | Équivalent CLI | Comportement |
|---|---|---|
| 🚀 Collecter + matcher + exporter | `python run.py --wttj-interactive` | Pipeline complet. Ouvre Chrome pour WTTJ + LinkedIn. Confirmation requise. Durée : 3–10 min. |
| 🔄 Recalculer scoring + rapport | `python run.py --from-step=3` | Re-score les offres stockées, génère le rapport. Ne collecte rien. |
| 📄 Régénérer rapport seul | `python run.py --from-step=4` | Reconstruit le rapport depuis le dernier selected_jobs.json. |

Les runs tournent en arrière-plan (thread daemon). La page se rafraîchit automatiquement à la fin (polling toutes les 2 s). Un seul run simultané — les boutons sont désactivés pendant un run en cours.

### Sortie du dernier run

La sortie (logs) du dernier run est affichée sous forme de terminal noir dès qu'il est terminé, avec le code retour.

### Filtres par source

Onglets client-side (JS, sans rechargement) : **Toutes | WTTJ | HelloWork | LinkedIn | Indeed** avec compteur par source.

### Tri des offres

Barre de tri au-dessus des filtres :

| Bouton | Comportement |
|---|---|
| Score ↓ | Score décroissant (défaut) |
| Récent d'abord | Date de publication croissante (inconnues en dernier) |
| Ancien d'abord | Date de publication décroissante (inconnues en dernier) |
| Source | Tri alphabétique par plateforme |
| Statut | selected en premier, review ensuite |

Le tri est JS-only (pas de rechargement page).

### Nettoyage des titres

Les titres pollués WTTJ (format "Titre Entreprise Titre Entreprise description...") sont automatiquement nettoyés pour l'affichage. Le titre brut reste intact dans les fichiers de données et est accessible en info-bulle au survol.

### Traiter une offre

1. Choisir dans le dropdown de chaque offre
2. Cliquer **💾 Enregistrer les décisions**

| Label UI | Valeur CSV |
|---|---|
| ✅ Postulé | `applied` |
| 🙈 Ignoré | `ignored` |
| ❌ Pas pertinent | `manually_rejected` |
| 🔁 Déjà postulé | `already_applied` |
| 🚫 Refus entreprise | `rejected_by_company` |

Les offres traitées disparaissent du run courant au rechargement suivant.

---

## Page : Historique (`/history`)

Tableau de toutes les offres connues dans `tracking.csv`, triées par date de traitement décroissante.

- **Filtre par statut** (client-side, JS)
- **Recherche texte** sur titre / entreprise / source (client-side)
- **Tooltip** sur les titres : le titre brut original est accessible au survol

---

## Page : Rapports (`/reports`)

- Liste les fichiers `app/data/exports/report_*.md`, triés du plus récent au plus ancien.
- Sélecteur déroulant pour choisir la date.
- Rendu HTML via la librairie Python `markdown` (tables, code blocks, blockquotes).
- Fallback texte brut si `markdown` n'est pas installé.

---

## Page : Paramètres (`/settings`)

Formulaire contrôlé pour modifier `app/config/candidate_profile.yaml`.

### Champs exposés

**Export**
- `balanced_by_source` (checkbox)
- `max_per_source` (nombre, 1–20)
- `target_per_source` (nombre, 1–20) — cible d'exploitables par source
- `global_target` (nombre, 1–50) — cible totale avant fallback
- `allow_source_fallback` (checkbox)
- `include_statuses` (checkboxes)

**Sources**
- WTTJ : `max_jobs`, `max_pages`
- HelloWork : `enabled`, `max_jobs`
- LinkedIn : `enabled`, `interactive`, `limit_per_run`
- Indeed : `enabled`, `max_pages_per_run`

**Pipeline**
- `pending_limit` — seuil d'offres non traitées avant blocage du run complet

**Ciblage**
- `target_titles`, `locations`, `must_have_keywords`, `nice_to_have_keywords`, `exclude_keywords`

### Protections

1. **Run en cours** → formulaire bloqué.
2. **Validation** → listes obligatoires non vides.
3. **Backup auto** → `app/config/backups/candidate_profile_YYYYMMDD_HHMMSS.yaml`.
4. **Round-trip YAML** → relu avant écriture.

> ⚠️ Les commentaires YAML sont retirés lors de la sauvegarde. Les clés non exposées sont préservées.

---

## Page : Maintenance (`/maintenance`)

### Compteur pending

Affiche le nombre d'offres `selected`/`review` non encore traitées manuellement.

- `pending_count > pending_limit` → alerte rouge, run bloqué
- `pending_count > 0` → alerte bleue
- `pending_count == 0` → alerte verte

### Nettoyage

| Bouton | Action |
|---|---|
| 🔍 Analyser (dry-run) | Affiche les stats (total / conservés / supprimables) sans modifier |
| 🗑 Appliquer le nettoyage | Backup + suppression des lignes non protégées |

**Fichiers nettoyés :** `jobs_raw.json` · `tracking.csv` · `selected_jobs.json` · `selected_urls.txt`

**Protégés (jamais supprimés) :** `applied` · `ignored` · `manually_rejected` · `already_applied` · `rejected_by_company`

**Sauvegardes** dans `app/data/backups/` : `jobs_raw_YYYYMMDD_HHMMSS.json` et `tracking_YYYYMMDD_HHMMSS.csv`

---

## Page : Aide (`/aide`)

Documentation non-technique accessible depuis la navbar.

Couvre : objectif de l'outil · ce qu'est un run · scoring · tous les statuts (auto + manuels) · comment traiter une offre · rapports · paramètres · authentification WTTJ/LinkedIn/Indeed · maintenance · raccourcis.

---

## Fichiers

| Fichier | Rôle |
|---|---|
| `web_ui.py` | Launcher racine |
| `app/src/web/app.py` | Flask app — routes, helpers, runner background |
| `app/src/web/templates/base.html` | Layout commun (nav, CSS, JS polling) |
| `app/src/web/templates/index.html` | Page run courant |
| `app/src/web/templates/history.html` | Page historique |
| `app/src/web/templates/reports.html` | Page rapports |
| `app/src/web/templates/settings.html` | Page paramètres |
| `app/src/web/templates/maintenance.html` | Page maintenance + nettoyage |
| `app/src/web/templates/aide.html` | Page aide non-technique |

---

## Limites connues

- `source_order` non éditable depuis l'UI.
- `search_urls`/`search_queries` non exposés (structures imbriquées).
- Pas de pagination dans l'historique.
- Pas d'expand/collapse de la description par offre.
- Pas d'authentification (local uniquement).
- Date de publication uniquement disponible pour HelloWork (WTTJ, LinkedIn, Indeed ne la transmettent pas).
