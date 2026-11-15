# ensim-trader-gaspard
lien vers le dashboard : https://bourdas-rougeault-gaspard-bi-2026.github.io/ensim-trader-gaspard/

## Compte rendu du TP : ENSIM Trade Center 

### 1. Objectif

Le but du projet est de produire un fichier `decision.json` à la racine du dépôt. À chaque tick, le marché du prof le lit pour chacun des trois produits (A, B, C). Chaque valeur est comprise entre -1 et +1 : une valeur positive achète `100 × valeur` unités, une valeur négative en vend, 0 ne fait rien.

L'objectif n'est pas seulement de finir avec la plus grande valeur totale. Il faut aussi tenir compte du **risque** (volatilité, drawdown) et se comparer aux **trois baselines** (random trader, random buyer, random seller).

### 2. Mise en place

- Dépôt public avec `decision.json` (tout à 0 au départ), URL brute envoyée au prof :
  `{"id": "gaspard", "name": "Gaspard Bourdas-Rougeault", "decision_url": ".../ensim-trader-gaspard/main/decision.json"}`
- Les deux flux utilisés :
  - `MARKET_URL` : historique des prix, commun à tous les traders ;
  - `HOLDINGS_URL` : mon argent et mes produits (`traders/gaspard/holdings.json`).
- Départ : 1000 d'argent + 1000 unités de chaque produit.

### 3. Analyse des données

Avant de choisir une méthode, j'ai regardé les 343 ticks d'historique, en particulier l'**autocorrélation des variations de prix** (est-ce qu'une hausse a tendance à être suivie d'une hausse ou d'une baisse quelques ticks plus tard) :

| Produit | Ce qu'on observe | Période du cycle |
|---|---|---|
| A | cycle assez bruité (autocorrélation ≈ +0,44 vers 46-48 ticks) | ~48 ticks |
| B | au début j'ai cru à une marche aléatoire (autocorrélation ≈ 0 à court terme), mais en prenant du recul on voit un cycle long + une tendance qui monte | ~96 ticks |
| C | cycle très régulier (autocorrélation +0,89 à 24 ticks) + tendance qui baisse | ~24 ticks |

Pour B, une sinusoïde de 96 ticks explique environ 90 % des variations une fois la tendance retirée, alors que sur des marches aléatoires simulées ce chiffre dépasse rarement 60 %. Le cycle est donc probablement réel, mais on n'en a vu que 3-4, donc c'est le signal le moins sûr des trois.

### 4. Le signal choisi

Comme les trois produits font des cycles, j'ai choisi du **retour à la moyenne** avec un z-score (même idée que les bandes de Bollinger) :

```
z = (prix actuel - moyenne des n derniers prix) / écart-type des n derniers prix
si |z| < 0.5        -> on ne fait rien (trop proche de la moyenne, c'est du bruit)
sinon signal = -z   (borné entre -1 et 1)
```

Prix au-dessus de la moyenne → il va redescendre → on vend. Prix en dessous → il va remonter → on achète.

La seule différence entre les produits est la taille de la fenêtre `n`, adaptée à la longueur du cycle :

| Produit | Fenêtre |
|---|---|
| A | 48 ticks (≈ un cycle) |
| B | 50 ticks (≈ un demi-cycle) |
| C | 12 ticks (≈ un demi-cycle) |

Le signal passe ensuite par `apply_budget_guard` (fourni), qui réduit les achats pour garder au moins 50 d'argent.

**Méthodes essayées puis abandonnées :**
- suivi de tendance (croisement de deux moyennes mobiles exponentielles) sur B : il perdait de l'argent, B n'a pas de tendance courte exploitable ;
- ajustement d'une sinusoïde par moindres carrés : résultats proches mais beaucoup plus compliqué à expliquer, et un peu moins bon une fois les trois produits combinés.

### 5. Validation (backtest)

Pour ne pas choisir des paramètres « trop parfaits » pour le passé, j'ai séparé l'historique en deux :
- ticks 0-200 pour **régler** les fenêtres ;
- ticks 200-343 pour **vérifier**, sur des données non utilisées pour le réglage.

Hypothèses de la simulation : départ 1000 + 1000 de chaque produit, décision calculée avec les prix connus au tick t et exécutée au prix du tick t+1, pas de frais.

| Période | Ma stratégie | Ne rien faire | Drawdown max (stratégie / rien) |
|---|---|---|---|
| 0-200 (réglage) | +88,4 % | +9,4 % | 11,7 % / 17,8 % |
| 200-343 (vérification) | +45,2 % | -5,4 % | 5,7 % / 17,2 % |
| 0-343 (complet) | +161,0 % | +3,3 % | 11,7 % / 17,8 % |

La stratégie gagne plus **et** baisse moins que « ne rien faire », y compris sur la période de vérification. Ajouter B a fait passer le test de +40,8 % à +45,2 % et le drawdown de 7,8 % à 5,7 %.

### 6. Automatisation

Le workflow GitHub Actions `.github/workflows/auto_decide.yml` lance `scripts/algorithm.py` toutes les 30 minutes (le marché avance environ toutes les heures). Le script :
1. récupère les prix et mon portefeuille ;
2. calcule le signal de chaque produit et écrit `decision.json` ;
3. ajoute une ligne dans `docs/data/journal.json` à chaque nouveau tick (prix, z, décision, raison en une phrase, ma valeur, valeur des baselines).

Automatiser ne retire pas le jugement humain : il est dans le choix de la méthode, des fenêtres et du seuil, que je surveille avec le dashboard.

### 7. Dashboard

Le dashboard (`docs/index.html`, publié avec GitHub Pages) reprend les quatre catégories demandées :

- **Performance** : classement actuel et valeur totale au fil des ticks, comparée aux trois baselines ;
- **Risque** : drawdown dans le temps, volatilité, et simulation de la stratégie sur tout l'historique ;
- **Signal par produit** : prix avec moyenne mobile et zone « on ne fait rien », z-score avec les seuils, décision actuelle ;
- **Journal des décisions** : une ligne par tick avec la raison de chaque décision.

Sources : `MARKET_URL`, `HOLDINGS_URL`, mon `journal.json`, et le fichier `leaderboard.json` publié par le prof dans son dépôt (seule source trouvée pour la valeur des baselines, il n'est pas cité dans le manuel).

### 8. Limites

- Tout repose sur l'hypothèse que les cycles gardent la même période. S'ils changent, le z-score peut acheter dans une baisse qui continue.
- B : seulement 3-4 cycles observés, période moins sûre.
- Le backtest ne compte pas de frais et suppose une exécution au prix du tick suivant.
- Le journal ne commence qu'au tick 343 (date de mon inscription), donc les courbes réelles sont courtes au début.

### 9. Compromis

J'ai cherché à **battre les baselines sans augmenter le risque** plutôt qu'à maximiser seulement la valeur finale :
- le seuil de 0,5 évite de trader sur du bruit, même si on rate quelques petits mouvements ;
- la réserve de 50 d'argent empêche de tout dépenser ;
- j'ai gardé une méthode simple, que je peux expliquer, plutôt qu'une méthode un peu plus complexe aux résultats proches.
