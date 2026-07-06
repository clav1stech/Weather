# Design — Archivage hot/cold de la base (croissance illimitée)

> **Statut : proposition, NON implémentée.** Document de décision. Aucune ligne de
> pipeline n'est modifiée tant que ce design n'est pas validé. Rédigé lors du
> chantier perf v2.5 (index+cache dashboard) qui a traité le point CPU mais **pas**
> la croissance mémoire — c'est l'objet de ce document.

## 1. Problème

`data/database_paris.parquet` croît **linéairement** : mesuré à ~58 000 lignes/jour
(1,05 M lignes / 54,8 Mo sur 18 jours au 06/07/2026). Projection :

| Échéance | Lignes | Mémoire `load_db` |
|---|---|---|
| Aujourd'hui | 1,05 M | 55 Mo |
| +6 mois | ~10,5 M | ~540 Mo |
| +12 mois | ~21 M | ~1,1 Go |

Streamlit Cloud plafonne autour de **1 Go de RAM** par app. `load_db` charge la base
**entière** en mémoire (mise en cache une fois par process) : vers 9-12 mois, l'app
approchera la limite et deviendra instable, **indépendamment** des optimisations CPU
du chantier v2.5 (index local + cache), qui ne réduisent pas l'empreinte mémoire.

**Fait clé** : seules **2 pages sur 7** ont besoin de l'historique complet
(*Explorer un run* — sélecteur de tout run archivé ; *Contrôle des runs* — matrice de
présence de tous les runs). Toutes les autres (Vue d'ensemble, Canicule, Convergence)
n'utilisent que les **runs récents** (horizon 16 j ; convergence/tendance ≈ 8 derniers
runs). Les 8 derniers runs = **30 %** des lignes ; le reste est de l'historique
consulté rarement.

## 2. Contraintes (invariants CLAUDE.md — non négociables)

- **Intégrité absolue** : aucune perte/dégradation d'un run. Écriture atomique
  (`.tmp` + `os.replace`), dédup par **(run_date, modèle)**, jamais de régression d'un
  run existant. Sauvegarde datée avant toute réécriture.
- **Legacy xlsx intouchables** (assurance-vie). Hors périmètre.
- **Schéma `C.SCHEMA` stable** ; `load_existing` réaligne le parquet historique.
- **Pipeline = partie critique** : fichiers à la racine, `config.BASE_DIR`, chemins
  Streamlit Cloud — ne pas déplacer, ne pas remanier à la légère.
- **Fraîcheur** : un nouveau run doit rester visible immédiatement (invalidation cache
  par redéploiement CI / bouton Rafraîchir).

## 3. Option retenue (recommandée) : deux parquets hot / cold

Découper la base en **deux fichiers de même schéma** :

- `data/database_paris.parquet` (**HOT**) — uniquement les runs des **N derniers jours**
  (proposition : **N = 35 j**, couvre horizon 16 j + fenêtre convergence + marge). C'est
  le fichier que le pipeline écrit à chaque poll, et que les pages interactives lisent.
- `data/database_paris_archive.parquet` (**COLD**) — tous les runs plus anciens. Écrit
  **uniquement** par un job de bascule (« rollover »), jamais par le poll courant.

### Pourquoi cette option
- Le pipeline `persist()` continue d'écrire **exactement comme aujourd'hui** dans le
  fichier HOT (petit, borné) — surface de risque minimale sur le code critique.
- `load_db()` (pages interactives) ne charge que le HOT → mémoire **bornée** (~35 j ≈
  100-110 Mo stable, ne croît plus).
- Aucune donnée affichée ne change : *Explorer* et *Contrôle* lisent en plus le COLD via
  une fonction dédiée `load_db_full(_sig)` (concat hot+cold, cachée, chargée **à la
  demande** seulement quand on ouvre ces deux pages).

### Job de rollover (nouveau, séparé)
- Fréquence : hebdomadaire (cron dédié, `concurrency.group: weather-data-push` partagé).
- Logique : lire hot + cold ; déplacer vers cold les runs dont `run_date <
  now − N jours` ; réécrire les DEUX fichiers atomiquement ; **sauvegarde datée** des
  deux avant.
- **Non-régression stricte** : `concat(hot, cold)` après rollover doit être un
  sur-ensemble exact de `concat(hot, cold)` avant (mêmes lignes, mêmes valeurs, aucun
  (run_date, modèle) perdu ni régressé) — vérifié par empreinte avant/après, sinon
  abandon (fichiers intacts).
- Idempotent : relançable sans effet si rien à basculer.

### Changements dashboard (minimes, non-régressifs)
- Nouveau `load_db_full(_sig)` dans `app/data/db.py` : `concat([load_db(_sig),
  load_archive(_sig)])`, cachée, réservée à *Explorer* et *Contrôle*.
- `list_runs` / présence sur *Contrôle* passent à `load_db_full` (matrice inchangée).
- Toutes les autres pages restent sur `load_db` (HOT) — plus rapides et à mémoire bornée.
- `db_signature()` combine les mtimes des deux fichiers.

### Migration initiale (one-off, `tools/`)
- Script `tools/split_hot_cold.py` : sauvegarde datée → découpe le parquet actuel en
  hot (≤ N j) + cold (> N j) → vérifie `concat == original` **au bit près** avant de
  remplacer. Legacy non touché.

## 4. Option alternative : dataset parquet partitionné

Répertoire `data/database_paris/` partitionné par mois de `run_date`, lu via pyarrow
avec *predicate pushdown* (ne lit que les partitions demandées). Plus scalable, natif
pyarrow, pas de job de rollover. **Mais** : change le format de stockage (impacte
`C.DB_PATH`, `migrate.py`, chemins Streamlit Cloud, `load_existing`, tous les flux
annexes qui supposent un fichier unique) → surface de risque bien plus large sur le
pipeline critique. **Non recommandé en premier** : à réserver si la croissance devient
ingérable même en hot/cold.

## 5. Ce que ce design ne traite PAS
- **Taille du dépôt git** : le COLD reste committé par la CI → le repo continue de
  grossir sur disque (le problème mémoire runtime, lui, est résolu). Externaliser le
  COLD (stockage objet, Git LFS) est une étape ultérieure distincte.
- Les **flux annexes** (t2m, observations, vintages) ont leur propre croissance ; même
  raisonnement applicable séparément si besoin, non couvert ici.

## 6. Décision attendue
1. Valider l'option hot/cold (§3) et la valeur de **N** (35 j proposé).
2. Chantier dédié (branche, bump Y) : migration one-off + `load_db_full` + job rollover,
   chacun sous double non-régression (calculs + rendu) et sauvegardes datées.
