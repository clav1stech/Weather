# Design — Sortie de git de la donnée parquet

> Statut : **plan de transition**, branche `dev/data-store` (chantier structurant, bump Y).
> Ce document fige les décisions et la procédure. Il ne modifie encore aucun code ni aucune donnée.

## 1. Problème

Le pipeline réécrit un parquet **entier** à chaque cycle (toutes les 2 h pour la base
principale, toutes les 15 min pour les obs 6 min…) et le **committe**. Un parquet est
binaire compressé : git ne sait pas le *deltaifier*, chaque commit ajoute donc un blob
quasi complet qui reste dans `.git` **pour toujours**.

Chiffres constatés (2026-07-26) :

| Élément | Valeur |
|---|---|
| `data/database_paris.parquet` | 9,3 Mo, 4,66 M lignes, ~40 j d'historique live |
| Versions du fichier dans l'historique | 171 commits |
| Poids cumulé de ces versions (avant compression git) | 722 Mo |
| Poids `.git` | 103 Mo, **croissance non bornée** |

Aucun rangement « hot/cold » *interne au fichier* n'y change quoi que ce soit : tant que
le fichier reste committé, `.git` grossit. **Git est utilisé comme base de données
binaire** — c'est le vrai problème structurel à corriger.

## 2. Décisions figées

| Arbitrage | Choix | Justification |
|---|---|---|
| **Backend de stockage** | GitHub Release assets | Aucun compte ni secret neuf : la CI a déjà `GITHUB_TOKEN` (`contents:write`), le repo **public** donne des URLs de download directes (lecture sans auth). Swappable (cf. §4). |
| **Historique `.git` (103 Mo)** | **Gelé** — on ne réécrit rien | Coût unique déjà payé. Purger réécrirait tous les SHAs → casse la règle *tags immuables* de CLAUDE.md, force-push + re-clone partout. On stoppe la croissance, on ne rembobine pas. |
| **Lecture dashboard** | **Partitions mensuelles concaténées** | Mois clos = immuables (cachés une fois) ; seul le mois courant se re-télécharge. Scale (dans 1 an : ~12 fichiers de ~8 Mo, la plupart cachés). |
| **Release porteur** | Release dédié `data` (tag léger `data-store`) | Séparé des releases de version `vX.Y.Z`, jamais mélangé. |
| **Clé de partition** | **mois du `run_date`** | Un run = une partition entière (comme le rollover neige). `persist()` déduplique par `(run_date, model)` → toujours au sein d'une partition, jamais à cheval. Partitionner par `valid_time` scinderait un run (horizon 16 j déborde le mois) — proscrit. |
| **Périmètre de départ** | `database_paris.parquet` (canicule) **d'abord** | Le gros churn. Les petits flux et snow suivent, même mécanique. |
| **Backstop** | Assets mensuels immuables + xlsx legacy intouchés | Chaque mois clos est figé une fois pour toutes ; les xlsx Météociel restent l'assurance-vie ultime, jamais touchés. |

## 3. Architecture cible

```
CI (toutes les 2 h)                         Dashboard (Streamlit Cloud + local)
─────────────────────                       ────────────────────────────────────
1. store.download(mois courant [+ précéd.]) 1. store.list() → assets mensuels
2. persist() → merge (INCHANGÉ)             2. store.download(chaque partition)
3. split par mois du run_date                  · mois clos = immuable → caché 1 fois
4. store.upload(partitions modifiées)          · mois courant = re-téléchargé
   → PLUS AUCUN commit data                  3. concat → même DataFrame qu'avant

Rollover mensuel (implicite) :
   après fenêtre de grâce, la partition du mois précédent
   n'est plus jamais ré-uploadée → figée = backstop immuable.
```

Invariants préservés (règles absolues CLAUDE.md, cf. §9) :

- **`persist()` et toutes les gardes anti-régression sont identiques.** Seul change *où*
  on lit l'état existant (asset au lieu du disque git) et *où* partent les octets finaux.
- **Écriture atomique** locale `.tmp` → `os.replace`, **puis** upload de l'asset.
- **`.bak` datés** conservés avant toute réécriture d'une partition.
- **xlsx legacy** intouchés, en lecture seule.

## 4. L'abstraction `DataStore` (rend le backend swappable)

Pipeline et dashboard ne parlent jamais directement à `gh` ni à `boto3`, mais à une
interface fine — c'est ce qui rend le choix « GitHub assets » peu engageant.

```python
# core/store/base.py  (config-agnostique, comme tout core/)
class DataStore(Protocol):
    def list(self, prefix: str) -> list[Asset]:        # nom + etag/updated_at + taille
        ...
    def download(self, name: str, dest: str) -> None:  # → fichier local
        ...
    def upload(self, name: str, src: str) -> None:     # remplace si existe (--clobber)
        ...
```

- `core/store/github.py` : implémentation via `gh release download/upload --clobber`
  (CLI déjà présent en CI) ou l'API REST GitHub. Release cible = `data-store`.
- Bascule éventuelle vers R2/B2 = **remplacer ce seul module** (`store_r2.py`) + poser
  2 secrets. Le **format parquet et le nommage des partitions sont identiques** quel que
  soit le backend → migrer la donnée = copier les fichiers d'un bucket à l'autre, une fois.

**Nommage des partitions** : `database_paris_YYYY-MM.parquet` (mois du `run_date`).
Le préfixe encode le flux → un même release `data-store` héberge tous les flux sans
collision (`database_paris_2026-07.parquet`, `database_paris_observations_2026-07.parquet`…).

## 5. Partitionnement mensuel & frontière de bascule

- **Clé = mois du `run_date`** (cf. §2). `split_by_month(df)` = pur `groupby`, testable,
  sans effet de bord.
- **Quelles partitions le pipeline touche** : un fetch frais contient des runs dont le
  `run_date` tombe presque toujours dans le mois courant, mais **en début de mois** un run
  de fin de mois précédent peut encore se compléter (modèle en cours de calcul à cheval).
  Règle robuste et bornée : le pipeline charge **mois courant + mois précédent**, merge le
  frais, et ré-uploade **uniquement les partitions dont le contenu a changé**.
- **Rollover = implicite.** Passé la fenêtre de grâce (aucun `run_date` frais ne tombe
  plus dans le mois précédent), sa partition n'est plus jamais ré-uploadée : elle est de
  fait **figée = immuable = backstop**. Pas de fichier hot/cold à jongler, pas de job de
  rollover dédié pour ce flux — c'est la simplification recherchée.
- **Manifeste** (optionnel, recommandé) : un petit `database_paris_manifest.json` (asset)
  listant chaque partition close avec sa taille + un hash — permet au dashboard et à un
  contrôle de vérifier l'intégrité sans tout retélécharger.

## 6. Pipeline modifié (`Forecast.py`)

Changement **localisé** au point d'entrée/sortie de la persistance, la logique métier ne
bouge pas :

1. `load_existing()` → au lieu de lire `C.DB_PATH` sur disque, `store.download()` du/des
   mois pertinents vers un fichier local temporaire, puis lecture identique.
2. `persist(fresh, existing)` → **inchangé** (dédup, anti-régression, `_validate`,
   `filter_fresh_rows`, `complete_missing_vars`, écriture atomique locale).
3. Après écriture locale : `split_by_month()` → `store.upload()` des seules partitions
   modifiées. `.bak` datée de chaque partition touchée avant upload.
4. **Boucle de retry CI** : `reset --hard` + relance git n'a plus lieu d'être pour la
   donnée. Le nouveau cycle en cas d'échec d'upload = re-`download` de l'asset (source de
   vérité à jour) → re-`persist` → re-`upload`. Naturellement idempotent (`persist` relit
   toujours le disque et recalcule la fusion par clé), exactement l'esprit du retry actuel.

## 7. Dashboard modifié (`app/data/db.py`)

- `load_db()` : `store.list("database_paris_")` → `store.download()` chaque partition
  (cache) → `pd.concat` → **même DataFrame qu'aujourd'hui**. Le reste de la stack
  (`load_existing`, sélections de runs, stats) ne voit aucune différence.
- **Cache** : `@st.cache_data` clé par `(nom, etag)`. Mois clos → etag stable → caché
  d'un rerun à l'autre. Mois courant → etag change à chaque cycle → re-fetch ciblé.
- **`db_signature()`** dérivé des etags combinés des assets (invalide le cache au bon
  moment), à la place de la signature fichier/mtime actuelle.
- **Dégradation silencieuse** (invariant) : store injoignable → dernier cache / message
  « données momentanément indisponibles », jamais un crash. Aucune donnée re-committée
  dans git comme filet (ce serait réintroduire le problème) : le filet est le cache + les
  assets immuables.
- **Streamlit Cloud** : lit désormais les assets au runtime (repo public → sans token).
  Cold start = re-download borné ; optimisation cache disque possible plus tard.
- **Empreinte mémoire (contrepartie du rollover implicite, §5)** : un flux sorti de git
  n'est plus jamais retaillé, donc ce que le dashboard charge croît sans borne s'il
  charge tout. Côté neige, les lignes MEMBRES du flux ensemble sont ~97 % du volume et
  ne servent qu'aux runs récents : elles sont lues sur une FENÊTRE
  (`snow_config.ENS_MEMBERS_WINDOW_DAYS`), tandis que le flux mean/spread — support de
  la convergence, ~1 % du volume — reste lu sur tout l'historique. La fenêtre s'applique
  DANS la lecture parquet (filtres pyarrow poussés jusqu'aux groupes de lignes), jamais
  après coup : un filtrage a posteriori aurait déjà matérialisé la base entière.
  Les bases sont partagées via `@st.cache_resource` et non `@st.cache_data`, qui en
  désérialise une copie complète par appelant.

## 8. Transition en phases (preuve de non-régression à chaque étape)

Principe directeur : **git reste la source de vérité jusqu'à ce qu'on prouve que le store
la suit à l'identique.** On ne coupe git qu'après cette preuve.

- **Phase 0 — Abstraction, zéro changement de comportement.**
  `core/store/` + `store/github.py` + `split_by_month`/`concat` (fonctions pures, tests
  unitaires). Rien n'est branché sur le pipeline ni le dashboard. Non-régression : néant à
  prouver (aucun chemin de prod modifié).

- **Phase 1 — Amorçage + double écriture (git reste maître).**
  - *Seed* one-off : lire l'actuel `database_paris.parquet`, `split_by_month`, uploader
    `…_2026-06.parquet` / `…_2026-07.parquet` sur le release `data-store`. **Prouver**
    `concat(assets) == fichier original` (lignes + schéma identiques). Lecture pure de
    l'existant → **zéro risque** pour la prod.
  - Pipeline : continue de committer dans git **comme aujourd'hui** (inchangé, sûr) **et**
    uploade en plus dans le store. Un contrôle compare les deux à chaque cycle.
  - Filet : git intact, on peut arrêter à tout moment sans rien perdre. `.git` grossit
    encore un peu pendant cette fenêtre de validation (bornée à quelques jours) — accepté.

- **Phase 2 — Dashboard lit le store (derrière un flag), git toujours committé.**
  - `load_db()` lit depuis le store si `WEATHER_STORE=1`, sinon disque git (repli).
  - **Non-régression obligatoire** : `tools/check_non_regression.py` (calculs) +
    `tools/ui_snapshot.py` (rendu AppTest) en `capture` (lecture disque) puis `check`
    (lecture store) — **100 % identique** attendu (même base, même heure ronde).

- **Phase 3 — Coupure de git.** *(faite, 2026-09-01, cinq flux canicule)*
  - **Prérequis, fait** : le pipeline lit l'existant DANS LE MAGASIN
    (`WEATHER_STORE_SOURCE=1` dans les cinq jobs canicule, branché au niveau de
    `load_existing()`). Validé sur ~44 h de cycles CI verts avant la coupure.
  - Pipeline : les cinq jobs (`fetch-api`, `fetch-t2m-hd`, `fetch-observations`,
    `fetch-observations-6m`, `fetch-vintages`) ont cessé `git add data/*.parquet`
    + commit + push ; ils **uploadent seulement** (`WEATHER_STORE_WRITE`, déjà
    actif). Retrait au passage de leur `concurrency.group`/boucle de retry sur
    le push, devenus sans objet.
  - **Écart assumé au plan initial** : les cinq parquets **restent trackés dans
    git** (pas de `git rm --cached` ni d'entrée `.gitignore`) — précédent réel de
    la neige (§8 Phase 4, ci-dessous), qui n'a jamais fait ce retrait non plus.
    Ils restent simplement **FIGÉS** (plus aucun commit ne les touche), ce qui
    préserve le repli local/dev (`store_active()` = git par défaut en local,
    `dev`/tests/harnais n'ont jamais besoin du réseau) sans rien casser — un
    `git rm --cached` aurait privé tout nouveau clone local du fichier.
    L'historique reste **gelé** (décision §2) dans les deux cas.
  - Dashboard : inchangé, lit déjà le store par défaut en ligne (`store_active()`
    = `not IS_LOCAL`, `WEATHER_STORE` ne servant qu'à forcer l'un ou l'autre).
  - **Preuve de non-régression, faite avant la coupure** : `tools/check_non_regression.py`
    et `tools/ui_snapshot.py` en `capture` (lecture disque git) puis `check`
    (lecture store, `WEATHER_STORE=1`) — 100 % identique (35 sorties de calcul,
    rendu des 7 pages).

- **Phase 4 — Nettoyage & généralisation.** *(neige faite ; canicule faite, cf. Phase 3)*
  - **Canicule : les cinq flux écrivent désormais uniquement dans le magasin**
    (Phase 3 ci-dessus faite), dashboard en lecture magasin avec repli git sur
    les parquets figés. `rollover-canicule` et `scrape-legacy` (xlsx/csv, texte
    mergeable) ne sont pas concernés, ils committent toujours normalement.
  - **Neige : passée directement en magasin seul** (pas de double écriture — le
    mécanisme était déjà éprouvé côté canicule, et l'app est hors production).
    La CI ne committe plus aucun parquet neige ; les fichiers restés dans git
    sont figés et ne servent que de repli de lecture. Le pipeline lit son
    existant DANS le magasin (le fichier local en CI étant la copie gelée).
  - Corollaire : le rollover hot/cold neige devient sans objet (rollover
    implicite du partitionnement mensuel, §5) — job CI `rollover-snow` supprimé.

## 9. Sécurité de la donnée de prod (règles absolues)

Rien dans ce chantier ne doit risquer d'altérer/perdre `data/database_paris.parquet` ni
les xlsx legacy. Garanties, en plus des invariants du §3 :

- **Git reste source de vérité jusqu'au cutover** (Phase 3). Avant, tout échec = repli sur
  git, aucune perte.
- **Seed = lecture pure** de l'existant, ne réécrit jamais le fichier de prod.
- **Double écriture validée** N cycles avant de couper git.
- **`.bak` datée** de chaque partition avant tout upload qui la remplace.
- **Assets mensuels immuables** = backstop ; **xlsx legacy** = assurance-vie ultime,
  intouchés.
- **Rollback par phase** : Phase 0-1 = supprimer les assets, rien d'autre ; Phase 2 =
  éteindre le flag ; Phase 3 = ré-ajouter les étapes `git add`/commit/push dans les jobs
  workflow (les fichiers sont restés trackés, jamais retirés du suivi — cf. Phase 3
  révisée). Aucun point de non-retour avant d'avoir la preuve à l'identique.
- **Branche & CI** : `dev/data-store` ne committe **aucune modification** des
  `data/*.parquet` existants (règle CLAUDE.md) — les jobs CI ne poussent que sur `main`.
  Pour travailler avec de la donnée fraîche en local sans risquer de la committer :
  `tools/refresh_data_from_main.sh` (skip-worktree).

## 10. Points à trancher en cours de route (pas des arbitrages de fond)

- Fenêtre de grâce exacte avant de figer un mois (proposé : 5 jours).
- Manifeste JSON d'intégrité : oui/non (recommandé oui).
- Optimisation cache disque sur Streamlit Cloud (peut attendre l'après-cutover).
- Ordre précis de généralisation aux flux annexes (Phase 4).

## 11. Hors périmètre

- Purge de l'historique `.git` (décision : gelé).
- Migration vers R2/B2 (préparée par l'abstraction `DataStore`, non planifiée).
- Changement du format parquet ou du schéma (`C.SCHEMA` stable, cf. CLAUDE.md).
