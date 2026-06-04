# job-bot — Guide d'installation (Windows)

## Prérequis

- **Python 3.10 ou supérieur** — [python.org/downloads](https://www.python.org/downloads/)
  - Cocher "Add Python to PATH" lors de l'installation
- **Google Chrome** — requis uniquement pour les sources WTTJ et LinkedIn
- **Git** (optionnel) — pour cloner le projet

---

## Étapes d'installation

### 1. Récupérer le projet

**Option A — Cloner avec Git :**
```powershell
git clone <url-du-repo> job-bot
cd job-bot
```

**Option B — Télécharger le ZIP :**
- Télécharger et décompresser le dossier
- Ouvrir PowerShell dans le dossier extrait

---

### 2. Créer l'environnement virtuel

```powershell
python -m venv .venv-aihawk
```

---

### 3. Activer l'environnement virtuel

```powershell
.\.venv-aihawk\Scripts\Activate.ps1
```

Vous devez voir `(.venv-aihawk)` au début de la ligne PowerShell.

> **Problème fréquent :** Si PowerShell affiche une erreur d'exécution de scripts :
> ```powershell
> Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
> ```
> Puis relancez la commande d'activation.

---

### 4. Installer les dépendances

```powershell
pip install -r requirements.txt
```

---

### 5. Initialiser les dossiers et la configuration

```powershell
python app/src/jobs/setup_local.py
```

Ce script :
- Crée tous les dossiers de données nécessaires
- Initialise les fichiers de données vides (`jobs_raw.json`, `tracking.csv`…)
- Copie `candidate_profile.example.yaml` → `candidate_profile.yaml` **seulement s'il n'existe pas encore** (ne jamais écraser une config existante)

---

### 6. Adapter votre profil candidat

Ouvrez `app\config\candidate_profile.yaml` et modifiez :
- `target_titles` — les intitulés de poste que vous recherchez
- `must_have_keywords` — les compétences indispensables
- `nice_to_have_keywords` — les compétences appréciables
- `locations` — vos villes cibles
- Les URLs de recherche pour chaque source (Indeed, HelloWork, WTTJ, LinkedIn)

Vous pouvez aussi modifier ces paramètres depuis l'interface web (page **Paramètres**).

---

### 7. Lancer l'interface web

```powershell
python web_ui.py
```

Puis ouvrir dans un navigateur : **http://127.0.0.1:5000**

---

## Lancer un run de collecte

### Sans scraping interactif (HelloWork + Indeed uniquement)

```powershell
python run.py --skip-wttj
```

### Avec WTTJ (nécessite une connexion Chrome)

```powershell
python run.py --wttj-interactive
```

Une fenêtre Chrome s'ouvre. Si vous n'êtes pas connecté à WTTJ, connectez-vous manuellement puis appuyez sur Entrée dans le terminal.

### Recalculer le matching sans scraper

```powershell
python run.py --from-step=3
```

---

## Commandes utiles

| Action | Commande |
|---|---|
| Lancer l'interface web | `python web_ui.py` |
| Run complet (avec WTTJ) | `python run.py --wttj-interactive` |
| Run sans WTTJ | `python run.py --skip-wttj` |
| Recalculer scoring seul | `python run.py --from-step=3` |
| Exporter les résultats | `python run.py --from-step=4` |
| Initialiser après clone | `python app/src/jobs/setup_local.py` |
| Remettre les données à zéro | `python app/src/jobs/reset_data.py` |

---

## Problèmes fréquents

### PowerShell bloque l'activation du venv

**Erreur :** `Activate.ps1 cannot be loaded because running scripts is disabled`

**Solution :**
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

---

### Chrome s'ouvre pour WTTJ / LinkedIn

C'est normal. Ces sources nécessitent une session authentifiée.

- Pour **WTTJ** : connectez-vous sur welcometothejungle.com dans la fenêtre Chrome, puis appuyez sur Entrée dans le terminal.
- Pour **LinkedIn** : même procédure (nécessite `linkedin.enabled: true` dans le profil).
- Pour éviter Chrome : utilisez `--skip-wttj` ou désactivez les sources dans `candidate_profile.yaml`.

---

### LinkedIn ou Indeed bloque avec une page de connexion

Ces sites bloquent parfois les accès automatisés. Le run continue sans ces sources et les résultats HelloWork/WTTJ restent disponibles.

Pour Indeed : vérifiez que vos URLs de recherche dans `candidate_profile.yaml` sont valides en les ouvrant dans un navigateur.

---

### Aucun résultat après un run

Vérifications à faire :
1. Vos `must_have_keywords` sont-ils présents dans les offres réelles ? Essayez d'assouplir ou d'en supprimer certains.
2. Vos `target_titles` correspondent-ils aux intitulés réels sur les sites ?
3. Vos URLs de recherche (Indeed, HelloWork) renvoient-elles des offres dans un navigateur normal ?
4. Le profil est-il bien configuré (`candidate_profile.yaml` existe) ?

---

### Le run est bloqué — "trop d'offres non traitées"

Le paramètre `pipeline.pending_limit` limite le nombre d'offres non examinées tolérées avant d'arrêter la collecte.

**Solution :** ouvrez l'interface web (`python web_ui.py`), traitez les offres en attente (statut `selected` ou `review`), puis relancez le run.

Ou augmentez temporairement `pending_limit` dans `candidate_profile.yaml`.

---

### Comment nettoyer toutes les données et repartir de zéro

```powershell
python app/src/jobs/reset_data.py
```

Cela remet tous les fichiers de données à vide sans supprimer votre configuration.
