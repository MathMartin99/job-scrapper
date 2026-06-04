# job-bot

Assistant personnel local de veille emploi avec interface web.

Il scrape automatiquement plusieurs sites d'offres d'emploi, score les annonces selon votre profil, et vous présente chaque matin une liste courte d'offres pertinentes à traiter.

**100 % local.** Aucune donnée n'est envoyée à l'extérieur.

---

## Sommaire

- [Fonctionnalités](#fonctionnalités)
- [Sources supportées](#sources-supportées)
- [Architecture](#architecture)
- [Prérequis](#prérequis)
- [Installation](#installation)
- [Configuration](#configuration)
- [Lancement](#lancement)
- [Interface web](#interface-web)
- [Démo rapide](#démo-rapide)
- [Commandes CLI](#commandes-cli)
- [Troubleshooting](#troubleshooting)
- [Sécurité et confidentialité](#sécurité-et-confidentialité)
- [Limites connues](#limites-connues)

---

## Fonctionnalités

- **Collecte multi-sources** — scrape Welcome to the Jungle, HelloWork, LinkedIn et Indeed en un seul run
- **Scoring automatique** — chaque offre est notée sur des critères configurables (titres, mots-clés, localisation, niveau)
- **Filtre de séniorité** — rejette automatiquement les offres senior / lead / manager / alternance
- **Interface web locale** — Flask, accessible sur `http://127.0.0.1:5000`
- **Suivi des candidatures** — statuts manuels (postulé, ignoré, refus entreprise…) persistés dans un CSV
- **Rapports Markdown** — un rapport par run avec stats par source et fiches détaillées
- **Maintenance** — nettoyage des offres rejetées sans toucher aux décisions manuelles
- **Paramètres via l'UI** — modification du profil candidat sans toucher au YAML à la main

---

## Sources supportées

| Source | Mode | Connexion requise |
|---|---|---|
| **Welcome to the Jungle** (WTTJ) | Selenium Chrome | Oui — compte WTTJ |
| **HelloWork** | requests + BeautifulSoup | Non — source publique |
| **LinkedIn** | Selenium Chrome | Oui — compte LinkedIn |
| **Indeed** | Selenium Chrome | Non — mais bloque parfois |

> **Sans WTTJ/LinkedIn :** `python run.py --skip-wttj` collecte HelloWork + Indeed uniquement, sans ouvrir Chrome.

---

## Architecture

```
job-bot/
├── run.py                          # Pipeline principal (CLI)
├── web_ui.py                       # Point d'entrée interface web
├── requirements.txt
│
├── app/
│   ├── config/
│   │   ├── candidate_profile.yaml         # Votre profil (ignoré par git)
│   │   └── candidate_profile.example.yaml # Profil exemple générique
│   │
│   ├── data/
│   │   ├── jobs/
│   │   │   ├── jobs_raw.json        # Offres collectées (ignoré par git)
│   │   │   ├── selected_jobs.json   # Offres scorées (ignoré par git)
│   │   │   └── selected_urls.txt    # Top URLs export (ignoré par git)
│   │   ├── exports/                 # Rapports Markdown (ignorés par git)
│   │   ├── logs/                    # Logs de run (ignorés par git)
│   │   ├── backups/                 # Sauvegardes automatiques (ignorées par git)
│   │   ├── cv/                      # Vos CVs locaux (ignoré par git)
│   │   └── tracking.csv             # Suivi des candidatures (ignoré par git)
│   │
│   └── src/
│       ├── jobs/
│       │   ├── collect_wttj.py      # Collecte WTTJ (Selenium)
│       │   ├── scrape_wttj.py       # Enrichissement descriptions WTTJ
│       │   ├── collect_hellowork.py # Collecte HelloWork (requests)
│       │   ├── collect_indeed_urls.py
│       │   ├── scrape_indeed.py
│       │   ├── collect_linkedin.py
│       │   ├── cleanup_jobs.py      # Nettoyage base d'offres
│       │   ├── export_selected_urls.py
│       │   ├── setup_local.py       # Init dossiers + fichiers après clone
│       │   └── reset_data.py        # Remise à zéro complète
│       ├── matching/
│       │   └── matcher.py           # Algorithme de scoring
│       └── web/
│           ├── app.py               # Application Flask
│           └── templates/           # Templates HTML (Jinja2)
│
└── docs/
    ├── install.md                   # Guide d'installation détaillé
    └── demo-guide.md                # Guide de présentation
```

### Pipeline d'un run

```
[Étape 1] Collecte URLs  →  [Étape 2] Scraping détails  →  [Étape 3] Matching  →  [Étape 4] Export
                  WTTJ / HelloWork / LinkedIn / Indeed
```

- `--from-step=3` : relance uniquement matching + export (pas de scraping)
- `--from-step=4` : régénère uniquement le rapport

---

## Prérequis

- **Python 3.10+** — [python.org/downloads](https://www.python.org/downloads/)
  - Windows : cocher **"Add Python to PATH"** lors de l'installation
- **Google Chrome** — requis pour WTTJ et LinkedIn uniquement
- **Git** — optionnel, pour cloner

---

## Installation

### Windows (PowerShell)

```powershell
# 1. Cloner ou télécharger le projet
git clone <url-du-repo> job-bot
cd job-bot

# 2. Créer l'environnement virtuel
python -m venv .venv-aihawk

# 3. Activer l'environnement virtuel
.\.venv-aihawk\Scripts\Activate.ps1

# 4. Installer les dépendances
pip install -r requirements.txt

# 5. Initialiser les dossiers et fichiers de données
python app/src/jobs/setup_local.py
```

> **Problème PowerShell :** Si l'activation du venv échoue avec une erreur de politique d'exécution :
> ```powershell
> Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
> ```

### macOS / Linux

```bash
git clone <url-du-repo> job-bot
cd job-bot
python3 -m venv .venv-aihawk
source .venv-aihawk/bin/activate
pip install -r requirements.txt
python app/src/jobs/setup_local.py
```

---

## Configuration

### 1. Copier le profil exemple

```powershell
# Windows
copy app\config\candidate_profile.example.yaml app\config\candidate_profile.yaml

# macOS / Linux
cp app/config/candidate_profile.example.yaml app/config/candidate_profile.yaml
```

### 2. Adapter le profil à votre profil

Ouvrez `app/config/candidate_profile.yaml` et modifiez les sections clés :

```yaml
# Titres de poste ciblés — l'offre doit contenir au moins l'un d'eux
target_titles:
  - Développeur Python
  - Backend Engineer
  - Software Engineer

# Mots-clés obligatoires — l'offre est rejetée si aucun n'est détecté
must_have_keywords:
  - python
  - api
  - backend

# Mots-clés souhaitables — ajoutent des points au score
nice_to_have_keywords:
  - docker
  - fastapi
  - postgresql

# Villes cibles
locations:
  - Paris
  - Lyon
  - remote

# Mots-clés à exclure — rejet automatique si présents dans le titre
exclude_keywords:
  - senior
  - lead
  - manager
  - alternance
  - stage

# Limite d'offres non traitées avant blocage du run
pipeline:
  pending_limit: 20
```

> Vous pouvez aussi modifier ces paramètres depuis l'interface web → **Paramètres**.

### 3. Configurer les URLs de recherche

Dans `candidate_profile.yaml`, adaptez les URLs pour chaque source à votre métier :

```yaml
indeed:
  enabled: true
  search_queries:
    - url: "https://fr.indeed.com/jobs?q=developpeur+python&l=Paris+%2875%29&radius=30"
      label: "Python Paris"
      max_jobs: 8

hellowork:
  search_urls:
    - label: "Python Paris"
      url: "https://www.hellowork.com/fr-fr/emploi/metier_developpeur-python-ville_paris-75000.html"
      max_jobs: 10
```

---

## Lancement

```powershell
# Activer le venv (si ce n'est pas déjà fait)
.\.venv-aihawk\Scripts\Activate.ps1

# Lancer l'interface web
python web_ui.py
```

Ouvrir dans un navigateur : **http://127.0.0.1:5000**

> L'interface est locale uniquement. Elle n'est pas accessible depuis un autre appareil.

---

## Interface web

### Pages disponibles

| Page | URL | Description |
|---|---|---|
| **Run courant** | `/` | Offres du dernier run, triées par score |
| **Historique** | `/history` | Toutes les offres traitées manuellement |
| **Rapports** | `/reports` | Rapports Markdown générés après chaque run |
| **Paramètres** | `/settings` | Modification du profil candidat |
| **Maintenance** | `/maintenance` | Nettoyage des offres rejetées |
| **Aide** | `/aide` | Documentation intégrée |

### Boutons de run

| Bouton | Équivalent CLI | Description |
|---|---|---|
| **▶ Run complet** | `python run.py --wttj-interactive` | Collecte toutes les sources + matching + export |
| **🔄 Recalculer scoring + rapport** | `python run.py --from-step=3` | Matching + export sans scraping (rapide, ~30s) |
| **📄 Régénérer rapport seul** | `python run.py --from-step=4` | Export uniquement |

### Statuts automatiques

| Statut | Signification |
|---|---|
| `selected` ⭐ | Offre recommandée — correspond bien au profil |
| `review` 🔍 | À examiner manuellement — partiellement compatible |
| `rejected` | Rejetée automatiquement (séniorité, contrat, score trop bas) |
| `skipped` | Déjà traitée dans un run précédent |

### Statuts manuels

| Statut | Signification |
|---|---|
| **Postulé** (`applied`) | Candidature envoyée |
| **Ignoré** (`ignored`) | Vu, pas intéressant, pas de candidature |
| **Pas pertinent** (`manually_rejected`) | L'outil a eu tort — offre non pertinente |
| **Déjà postulé** (`already_applied`) | Candidature antérieure déjà envoyée |
| **Refus entreprise** (`rejected_by_company`) | L'entreprise a refusé |

> **Important :** une offre avec un statut manuel n'est plus jamais remontée dans les runs suivants.

### Maintenance

La page **Maintenance** permet de nettoyer les offres rejetées automatiquement accumulées au fil des runs.

1. **Analyser (dry-run)** — affiche ce qui serait supprimé, sans rien modifier
2. **Appliquer le nettoyage** — supprime les offres rejetées non manuellement traitées

Les offres avec un statut manuel (`applied`, `ignored`…) ne sont **jamais** supprimées.
Des sauvegardes automatiques sont créées dans `app/data/backups/` avant toute suppression.

---

## Démo rapide

Voici les étapes pour tester l'outil de bout en bout après installation :

1. **Lancer l'interface web**
   ```powershell
   python web_ui.py
   ```
   Ouvrir **http://127.0.0.1:5000**

2. **Configurer votre profil** — aller dans **Paramètres** et adapter :
   - Les titres de postes recherchés
   - Les mots-clés obligatoires (vos compétences clés)
   - Les villes cibles

3. **Lancer un run sans WTTJ** (pas besoin de Chrome connecté)
   ```powershell
   python run.py --skip-wttj
   ```
   Ou depuis l'interface, cliquer **▶ Run complet** (ouvrira Chrome pour WTTJ).

4. **Attendre la fin du run** — la page se rafraîchit automatiquement. Durée : 2 à 10 minutes selon les sources.

5. **Traiter les offres** — sur la page **Run courant** :
   - Ouvrir chaque offre qui semble intéressante (clic sur le titre)
   - Choisir un statut (Postulé / Ignoré / Pas pertinent) dans le menu déroulant
   - Cliquer **💾 Enregistrer les décisions**

6. **Consulter le rapport** — aller dans **Rapports** pour voir le résumé complet avec stats par source.

7. **Répéter chaque jour** — les offres déjà traitées ne reviennent jamais.

---

## Commandes CLI

```powershell
# Run complet (WTTJ interactif + HelloWork + LinkedIn + Indeed)
python run.py --wttj-interactive

# Run sans WTTJ (HelloWork + Indeed uniquement, pas de Chrome)
python run.py --skip-wttj

# Recalculer le matching et l'export sans scraper
python run.py --from-step=3

# Régénérer le rapport uniquement
python run.py --from-step=4

# Forcer un run même si la limite d'offres non traitées est atteinte
python run.py --force-run

# Initialiser les dossiers et fichiers après clone
python app/src/jobs/setup_local.py

# Remettre toutes les données à zéro
python app/src/jobs/reset_data.py

# Test HelloWork seul (standalone)
python app/src/jobs/collect_hellowork.py --append --limit 5
```

---

## Troubleshooting

### Chrome s'ouvre mais rien ne se passe

WTTJ ou LinkedIn a détecté l'automatisation. Fermez Chrome, attendez quelques minutes, et relancez. Si le problème persiste, lancez sans WTTJ/LinkedIn :

```powershell
python run.py --skip-wttj
```

Et désactivez LinkedIn dans `candidate_profile.yaml` : `linkedin: enabled: false`

---

### WTTJ demande une connexion

Lors du premier run, une fenêtre Chrome s'ouvre sur welcometothejungle.com. Connectez-vous manuellement dans cette fenêtre, puis appuyez sur **Entrée** dans le terminal pour continuer.

Le pipeline continue sans WTTJ si la connexion échoue — HelloWork et Indeed sont toujours collectés.

---

### LinkedIn demande une connexion / CAPTCHA

Même procédure que WTTJ : connectez-vous dans la fenêtre Chrome qui s'ouvre, puis appuyez sur **Entrée**.

Si LinkedIn affiche un CAPTCHA ou bloque, le pipeline continue sans LinkedIn.

---

### Indeed affiche un "login wall"

Indeed bloque parfois l'accès automatique. Ce blocage est temporaire (quelques heures).

Le run continue avec les autres sources. Les offres Indeed existantes dans `jobs_raw.json` ne sont pas effacées.

---

### Aucun résultat après un run

Vérifications à faire :

1. Vos `must_have_keywords` sont-ils présents dans les offres réelles ? Réduisez la liste ou choisissez des termes plus courants.
2. Vos `target_titles` correspondent-ils aux intitulés réels sur les sites ? Ouvrez une URL de recherche manuellement pour vérifier.
3. Vos URLs de recherche (Indeed, HelloWork) renvoient-elles des résultats dans un navigateur normal ?
4. Le score minimum (`MIN_SCORE = 40` dans `matcher.py`) est-il trop élevé pour votre profil ?

---

### `candidate_profile.yaml` manquant

```
FileNotFoundError: app/config/candidate_profile.yaml
```

```powershell
copy app\config\candidate_profile.example.yaml app\config\candidate_profile.yaml
```

---

### Erreur d'import ou de dépendances

```powershell
# Vérifier que le venv est activé (voir (.venv-aihawk) en début de ligne)
.\.venv-aihawk\Scripts\Activate.ps1

# Réinstaller les dépendances
pip install -r requirements.txt
```

---

### Le run est bloqué — "trop d'offres non traitées"

Le paramètre `pipeline.pending_limit` dans `candidate_profile.yaml` limite le nombre d'offres non examinées tolérées.

**Solution :** traiter les offres en attente depuis l'interface web (`/`), puis relancer. Ou augmenter `pending_limit` dans les Paramètres.

---

## Sécurité et confidentialité

> Ces fichiers et dossiers ne doivent **jamais** être partagés ni commités.

| Élément | Raison |
|---|---|
| `app/data/chrome_profile/` | Contient les cookies et sessions Chrome (accès LinkedIn, WTTJ) |
| `app/data/cv/` | Contient votre CV |
| `app/config/candidate_profile.yaml` | Peut contenir des URLs de recherche personnelles |
| `app/data/tracking.csv` | Contient l'historique de vos candidatures |
| `app/data/exports/report_*.md` | Contient les rapports personnels |
| `.env`, `.env.*` | Variables d'environnement éventuelles |

Tous ces éléments sont déjà exclus via `.gitignore`. Vérifiez avant chaque `git push` avec `git status`.

---

## Limites connues

- **Pas d'IA générative** — le scoring est basé sur du matching de mots-clés, pas sur un LLM.
- **WTTJ et LinkedIn nécessitent une session Chrome authentifiée** — la session expire périodiquement.
- **Indeed bloque parfois** — le run continue sans cette source.
- **Les URLs de recherche sont à configurer manuellement** pour chaque métier et ville.
- **Un seul run à la fois** — pas de parallélisation des sources.
- **Windows uniquement testé** — macOS/Linux devrait fonctionner mais n'est pas garanti.
- **Pas de scheduler intégré** — le lancement quotidien doit être fait manuellement ou via un planificateur système (Task Scheduler, cron).

---

## Initialisation et remise à zéro

```powershell
# Initialiser après clone (crée les dossiers, NE touche pas à la config existante)
python app/src/jobs/setup_local.py

# Remettre toutes les données à zéro (garde candidate_profile.yaml)
python app/src/jobs/reset_data.py
```
