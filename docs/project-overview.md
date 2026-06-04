# job-bot — Vue d'ensemble du projet

*Dernière mise à jour : 2026-05-12*

---

## Ce que fait job-bot

job-bot est un outil personnel qui automatise la veille d'offres d'emploi DevOps / Cloud en France.

Au lieu de consulter manuellement quatre sites d'emploi chaque matin, job-bot :
1. Collecte les offres publiées récemment sur chaque site.
2. Les note selon un profil candidat configurable.
3. Génère un rapport avec uniquement les offres pertinentes.
4. Propose une interface web locale pour consulter et traiter les offres.

L'outil tourne entièrement en local — aucune donnée n'est envoyée à un serveur externe.

---

## Les sources d'offres

| Source | Mode de collecte | Connexion requise |
|---|---|---|
| **Welcome to the Jungle (WTTJ)** | Navigateur automatisé (Chrome) | Oui — compte WTTJ |
| **HelloWork** | Requêtes web directes (pas de navigateur) | Non |
| **LinkedIn** | Navigateur automatisé (Chrome) | Oui — compte LinkedIn |
| **Indeed** | Navigateur automatisé (Chrome) | Non — mais soumis à des blocages |

### Ce que "navigateur automatisé" veut dire

Pour WTTJ et LinkedIn, l'outil ouvre une vraie fenêtre Chrome visible à l'écran. L'utilisateur peut être invité à se connecter manuellement si la session a expiré. Une fois connecté, Chrome navigue automatiquement et récupère les offres.

### Résistance aux blocages

Si une source est temporairement inaccessible (session expirée, blocage, login obligatoire), le pipeline continue avec les autres sources sans planter.

---

## Le matching — comment les offres sont notées

Chaque offre reçoit un **score** calculé à partir du profil candidat défini dans `app/config/candidate_profile.yaml`.

### Critères de scoring

| Critère | Points |
|---|---|
| Titre du poste correspond (DevOps, Cloud, SRE…) dans l'intitulé | +40 |
| Titre du poste trouvé dans la description | +20 |
| Localisation correcte (Paris, Lyon, remote…) | +10 à +20 |
| Mode de travail (remote, hybride) | +6 |
| Niveau d'expérience junior | +18 |
| Chaque mot-clé technique obligatoire (aws, docker, kubernetes…) | +10 |
| Chaque mot-clé technique secondaire (python, gitlab, helm…) | +4 |
| Ancienneté de l'offre | +10 à −5 selon fraîcheur |
| Mot-clé excluant dans le titre | −35 à −45 |

### Mots-clés obligatoires (must-have)

L'offre doit contenir au moins **un** de ces mots pour ne pas être rejetée :
`aws` · `terraform` · `docker` · `kubernetes` · `ansible`

### Rejets automatiques

Une offre est automatiquement rejetée si :
- Elle contient des mots de séniorité dans le titre (`senior`, `lead`, `staff`, `confirmé`…)
- Elle concerne une alternance, un stage, ou du freelance
- Elle exige plus de 3 ans d'expérience explicite
- Elle ne contient aucun des mots-clés obligatoires
- Son score total est trop bas (< 40 points)

---

## Les statuts possibles

### Statuts automatiques (attribués par le matcher)

| Statut | Signification |
|---|---|
| ⭐ **selected** | Offre recommandée — correspond bien au profil |
| 🔍 **review** | À examiner manuellement — partiellement compatible |
| ❌ **rejected** | Rejetée automatiquement — ne correspond pas au profil |

### Statuts manuels (attribués par l'utilisateur)

| Statut | Signification |
|---|---|
| ✅ **applied** | Candidature envoyée |
| 🙈 **ignored** | Ignorée volontairement |
| ❌ **manually_rejected** | Pas pertinent (jugement manuel) |
| 🔁 **already_applied** | Déjà postulé à cette offre |
| 🚫 **rejected_by_company** | Refus reçu de l'entreprise |

Les offres avec un statut manuel ne sont jamais re-collectées lors des runs suivants.

---

## L'interface web

L'interface se lance avec :
```bash
python web_ui.py
```
puis s'ouvre dans un navigateur à l'adresse **http://127.0.0.1:5000**.

Elle n'est accessible que depuis la machine locale — elle n'est pas visible sur le réseau.

### Les pages disponibles

**Page principale (`/`)** — Offres du run courant
- Affiche les offres `selected` et `review` triées par score
- Permet de filtrer par source (WTTJ, HelloWork, LinkedIn, Indeed)
- Permet de trier par score, date de publication, statut ou source
- Permet de marquer les décisions (postulé, ignoré…) directement
- Permet de lancer un nouveau run (pipeline complet, matching seul, export seul)

**Historique (`/history`)** — Toutes les offres connues
- Tableau de toutes les offres jamais vues, avec statuts et dates
- Recherche par titre, entreprise ou source
- Filtres par statut

**Rapports (`/reports`)** — Rapports Markdown générés
- Affiche les rapports des runs précédents
- Sélecteur de date pour naviguer entre les rapports

**Paramètres (`/settings`)** — Configuration du profil
- Modifie les mots-clés, les localisations, les titres cibles
- Contrôle les sources actives et leurs limites
- Sauvegarde automatique avant chaque modification

**Maintenance (`/maintenance`)** — Nettoyage des données
- Affiche le nombre d'offres non traitées
- Permet de supprimer les offres rejetées automatiquement pour libérer de l'espace
- Effectue des sauvegardes avant toute suppression

---

## Les rapports

Après chaque run, un rapport Markdown est généré dans `app/data/exports/`.

### Ce qu'il contient

- Résumé du run : nombre d'offres analysées, exploitables, exportées
- Tableau par source : statut, nombre d'offres exploitables, offres sélectionnées/en review/rejetées
- Fiche détaillée pour chaque offre retenue :
  - Titre, entreprise, localisation, date de publication
  - Score et mots-clés détectés
  - Résumé de la description
  - Commandes pour marquer l'offre
- Section doublons potentiels si une même offre apparaît sur plusieurs sources

### Exemple de tableau source

| Source | Statut | Exploitables | Selected | Review | Rejeté | Ignoré |
|---|---|---:|---:|---:|---:|---:|
| WTTJ | ✓ ok | 3/3 | 3 | 0 | 21 | 7 |
| HelloWork | ✓ ok | 5/3 | 3 | 2 | 5 | 0 |
| LinkedIn | ∅ vide | 0/3 | 0 | 0 | 1 | 0 |
| Indeed | 🔒 bloqué | 0/3 | 0 | 0 | 0 | 0 |

---

## Limites connues

| Limite | Détail |
|---|---|
| **WTTJ — session Chrome** | La session expire. Si le navigateur demande une reconnexion, l'utilisateur doit intervenir manuellement dans la fenêtre Chrome. |
| **LinkedIn — session Chrome** | Même comportement. La collecte peut être bloquée par un CAPTCHA. |
| **Indeed — blocage fréquent** | Indeed exige une connexion de plus en plus souvent. Le pipeline continue sans Indeed en cas de blocage. |
| **LinkedIn — review systématique** | Les offres LinkedIn prometteuses passent en "review" plutôt que "selected" — l'utilisateur doit valider manuellement pendant la phase de rodage. |
| **Date de publication** | Seul HelloWork fournit la date de publication dans le format lisible par job-bot. WTTJ, LinkedIn et Indeed ne transmettent pas cette information. |
| **Titres WTTJ** | Certains titres WTTJ contiennent des métadonnées parasites (nom d'entreprise répété, dates). Le nettoyage automatique corrige la plupart des cas. |
| **Pas d'envoi automatique** | job-bot ne postule pas automatiquement — il identifie uniquement les offres pertinentes. Les candidatures restent manuelles. |
| **Local uniquement** | Aucune API externe, aucun service cloud, aucune base de données distante. Tout est stocké en local. |
