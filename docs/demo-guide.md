# job-bot — Guide de démo

*Pour une présentation à quelqu'un de non technique*

---

## Prérequis avant la démo

Avant de commencer :

- [ ] Python est installé et les dépendances sont présentes (`pip install -r requirements.txt`)
- [ ] `candidate_profile.yaml` est configuré (copié depuis `candidate_profile.example.yaml`)
- [ ] Un run a déjà été effectué (des offres sont disponibles dans `selected_jobs.json`)
- [ ] L'interface web tourne (`python web_ui.py`)

> Si vous n'avez pas encore de données : lancez d'abord `python run.py --skip-wttj` pour collecter des offres via HelloWork et Indeed.

---

## Lancer l'interface

```bash
python web_ui.py
```

Puis ouvrir dans un navigateur :

**http://127.0.0.1:5000**

> L'interface est locale. Elle n'est pas accessible depuis un autre appareil sur le réseau.

---

## Parcours de démo conseillé

### 1. Page principale (`/`) — Les offres du dernier run

**Ce qu'on voit :** la liste des offres triées par score.

**Ce qu'on montre :**
- Les badges **SELECTED** (offres recommandées) et **REVIEW** (à examiner)
- Le score de chaque offre (nombre de points)
- Le badge de source (WTTJ, HelloWork, LinkedIn, Indeed)
- Les filtres par source en haut de page
- Les boutons de tri (par score, par date, par source)

**Message clé à faire passer :**
> "Ce sont les offres que l'outil a jugées pertinentes parmi toutes celles collectées. Il en a analysé des dizaines, il en garde une dizaine."

---

### 2. Une fiche offre — le scoring

**Ce qu'on montre :**
- Le titre du poste, l'entreprise, la localisation
- Les mots-clés détectés dans la description (obligatoires et souhaitables)
- Le score calculé et les critères remplis

**Message clé :**
> "L'outil a lu toute la description et vérifié les critères : niveau junior, le métier cherché, la ville ou le remote, et les technologies. Chaque critère rapporte des points."

---

### 3. Les statuts manuels

**Ce qu'on montre :**
- Les boutons d'action sur chaque offre : Postulé / Ignoré / Pas pertinent / Déjà postulé
- Qu'un statut manuel exclut définitivement l'offre des prochains runs

**Message clé :**
> "Une fois qu'on a traité une offre, elle ne revient plus dans les prochains runs, même si le scraper la recollecte."

---

### 4. Les rapports (`/reports`)

**Ce qu'on montre :**
- Le sélecteur de date pour choisir un rapport
- Le tableau récapitulatif en haut : sources actives, nombre d'offres collectées, statut de chaque source
- Les fiches offres détaillées avec mots-clés et score

**Message clé :**
> "Chaque run génère un rapport comme celui-ci. On voit d'un coup d'œil combien d'offres ont été collectées par source et lesquelles valent la peine d'être regardées."

---

### 5. Les paramètres (`/settings`)

**Ce qu'on montre :**
- Les titres cibles (ce qu'on cherche)
- Les mots-clés obligatoires (technologies indispensables)
- Les sources activées / désactivées
- Les villes cibles

**Message clé :**
> "Tout ça est configurable depuis l'interface, sans toucher au code. Si on veut chercher un autre métier ou une autre ville, on change ici."

---

### 6. La maintenance (`/maintenance`)

**Ce qu'on montre :**
- Le compteur d'offres en base
- Le bouton d'analyse pour voir ce qui peut être nettoyé
- La notion de nettoyage sûr : seules les offres rejetées automatiquement sont supprimées, jamais celles traitées manuellement

**Message clé :**
> "L'outil accumule des données au fil des runs. Cette page permet de faire le ménage sans risquer de perdre des décisions personnelles."

---

### 7. Lancer un run en direct (optionnel)

> ⚠️ Ne lancer un run complet que si les conditions sont réunies.
> Un run complet peut ouvrir Chrome (WTTJ, LinkedIn) et durer plusieurs minutes.

**Pour montrer uniquement le matching et l'export (sans scraping) :**
- Cliquer sur **🔄 Recalculer scoring + rapport** dans l'interface
- La page se rafraîchit automatiquement à la fin (toutes les 2 secondes)
- Durée : 10 à 30 secondes, aucun navigateur ne s'ouvre

**Pour un run complet avec scraping (si Chrome est disponible) :**
- Cliquer sur **▶ Run complet**
- Chrome peut s'ouvrir pour WTTJ ou LinkedIn — une connexion manuelle peut être demandée
- Durée : 3 à 10 minutes

---

## Ce qu'il faut éviter de montrer

### Page Historique (`/history`)

La page Historique affiche l'intégralité de `tracking.csv`, incluant toutes les candidatures et décisions passées.

**Si des données personnelles doivent rester confidentielles** (entreprises contactées, dates de candidature…), éviter d'ouvrir cette page.

Options :
- Réinitialiser les données avant la démo : `python app/src/jobs/reset_data.py`
- Passer rapidement sur la page sans s'arrêter sur le contenu

---

## Script de présentation (2 minutes)

> "job-bot, c'est un assistant de veille emploi entièrement local.
>
> Chaque matin, il parcourt automatiquement les sites d'emploi : Welcome to the Jungle, HelloWork, LinkedIn, Indeed. Il lit les descriptions de postes et les compare à un profil configuré : le métier recherché, les technologies indispensables, la ville ou le remote, le niveau d'expérience.
>
> En quelques minutes, il sort une liste d'une dizaine d'offres vraiment pertinentes — avec un score, les mots-clés détectés, et un résumé de chaque fiche. Plus besoin de scroller indéfiniment sur les sites.
>
> On peut traiter chaque offre depuis l'interface : postulée, ignorée, pas pertinente. Et tout ça est mémorisé pour que les prochains runs ne remontent pas les mêmes offres.
>
> C'est entièrement local — rien ne passe par un serveur externe. Les données restent sur la machine."

---

## Commandes de rappel

| Action | Commande |
|---|---|
| Lancer l'interface | `python web_ui.py` |
| Run complet (avec WTTJ) | `python run.py --wttj-interactive` |
| Run sans WTTJ | `python run.py --skip-wttj` |
| Recalculer sans scraper | `python run.py --from-step=3` |
| Réinitialiser les données | `python app/src/jobs/reset_data.py` |

---

## Limites à mentionner honnêtement

- **WTTJ et LinkedIn nécessitent une connexion** — Chrome s'ouvre et une authentification manuelle peut être demandée lors du premier run.
- **Indeed bloque parfois** — le run continue sans cette source, les résultats des autres sources sont conservés.
- **Le scraping n'est pas infaillible** — certaines offres peuvent manquer si le site change sa structure.
- **C'est un outil personnel, pas un produit fini** — il n'y a pas de support, pas de mises à jour automatiques.
- **Aucun LLM, aucune IA générative** — le scoring est basé sur des règles de matching de mots-clés, pas sur un modèle de langage.
