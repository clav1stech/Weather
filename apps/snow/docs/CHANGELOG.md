# Changelog — Dashboard Neige (Megève)

Versioning INDÉPENDANT du canicule : la constante `SNOW_APP_VERSION` vit dans
`snow_app.py` (racine) et ne se synchronise JAMAIS avec `APP_VERSION`
(canicule). Numérotation et historique totalement séparés — jamais mélangés
avec `CHANGELOG.md` racine. Tags GitHub : préfixe `snow-vX.Y`.

Sémantique de version propre à la phase de développement :
- **X = 0 : phase développement/beta.** Toute la branche dev/snow reste en
  0.Y.Z. **Y évolue librement** pour ponctuer les chantiers structurants
  (toute la branche EST le chantier — à la différence du canicule où Y
  signale une branche dédiée) ; Z pour les évolutions courantes.
- **Le passage à 1.0.0** (bascule en production, tag `snow-v1.0`) n'intervient
  qu'au **merge réel en main**, jamais avant, et uniquement sur instruction
  explicite de l'utilisateur. Aucun tag/release publié avant ce merge.

## [0.4.4] - 2026-07-20
- Collecteur AROME-IFS Météo-France direct : pluie et neige horaires plus
  température aux sites village/sommet de H+1 à H+45, quota respecté,
  archivage local commun et synthèse historique.
- Hiérarchie courte échéance sans double comptage : AROME-PI prioritaire
  sur six heures, puis AROME-IFS, puis AROME France/ICON-D2 via Open-Meteo en
  comparaison ou repli. Workflow et page pipeline étendus au septième flux.

## [0.4.3] - 2026-07-20
- Page locale « Lancer le pipeline neige » étendue aux six collectes
  actives, regroupées par rôle et accompagnées de leur modèle, source et
  horizon ; AROME-IFS reste explicitement signalé comme non collecté.
- Barre latérale enrichie avec les sources Météo-France PNT, Open-Meteo et
  observations, ainsi que les modèles et horizons effectivement utilisés.

## [0.4.2] - 2026-07-20
- PE-ARPEGE : 35 membres sur les cycles complets 00/12Z, cumuls journaliers
  H24/H48/H72/H96, priorité de relais après PE-AROME et tests de cycle.
- Job neige 2 h et rollover HOT/COLD étendus aux parquets Météo-France
  régionaux, sans workflow horaire supplémentaire.

## [0.4.1] - 2026-07-20
- Collecteurs WCS Météo-France pour AROME-PI et PE-AROME : décodage GRIB
  direct en mémoire, contrôle de complétude et no-op sur cycle inchangé.
- Archive historique compacte des moyennes par cycle/modèle/site/échéance,
  sans remplacer les membres bruts HOT/COLD nécessaires au recalibrage.

## [0.4.0] - 2026-07-20
- Vue d'ensemble neige : coupe verticale horaire pluie/neige/sec/mixte aux
  quatre altitudes et bilan quotidien cm/mm ; la LPN pilote le flux HD tandis
  qu'AROME-PI peut fournir une phase directe lorsqu'il est disponible.
- Classification par membre pondérée sur J0–J+15, avec zone mixte explicite,
  précipitation au village et seuils centralisés dans `weather_type.py`, à
  affiner in situ au fil de la saison.

## [0.3.3] - 2026-07-19
- Convergence enrichie : nombre de runs paramétrable, bande mean ± spread
  par modèle, heatmap des révisions sur les pivots météo et mode
  « Tous modèles » à poids égal sur une composition stable, avec amplitude
  Min–Max des moyennes du run le plus récent.

## [0.3.2] - 2026-07-19
- Vue d'ensemble neige : les traces isolées sous les seuils de pertinence ne
  sont plus présentées comme un épisode, tout en restant disponibles dans
  l'exploration brute. Les graphiques exposent désormais P10–P90 par modèle et
  Min–Max du super-ensemble, avec titres et légendes rendus plus lisibles.

## [0.3.1] - 2026-07-19
- Contrôle opérationnel des runs membres et mean/spread : fraîcheur empirique,
  portée contiguë, complétude et cycles attendus, calculés avec les mêmes
  fonctions que le pipeline. Nouvelle page locale de lancement des trois flux,
  avec logs pleine largeur et simulation du rollover strictement dry-run ;
  exécution/rendu mutualisés dans `core/ui/pipeline.py`.

## [0.3.0] - 2026-07-19
- Archivage hot/cold : mécanique générique `core/pipeline/hot_cold.py`
  (sauvegardes datées, vérification stricte de non-perte, dry-run), rollover
  hebdomadaire des trois parquets neige vers `*_archive` (fenêtre 45 j, job CI
  `rollover-snow`), lecture hot+cold côté dashboard (convergence,
  observations). Côté canicule : activation PRÉPARÉE mais non déclenchée
  (script + job CI manuels, procédure post-merge documentée).

## [0.2.0] - 2026-07-19
- Page Observations — stations Météo-France des Alpes du Nord (DPPaquetObs
  département 74) : Combloux / Mont d'Arbois / Aiguille du Midi en référence,
  Chamonix / Annecy-Meythet en appoint. Pipeline `fetch_observations.py`
  (mécanique générique `core/pipeline/observations.py`, identifiants vérifiés
  contre le paquet API réel), cartes temps réel, iso 0 °C observée, comparaison
  inter-stations, Tx/Tn des jours révolus, job CI horaire `fetch-snow-obs`.

## [0.1.0] - 2026-07-19
- Squelette initial du dashboard neige : pipeline ensemble + maille fine,
  pages Vue d'ensemble / Explorer un run / Convergence, entrée `snow_app.py`,
  versioning et changelog séparés du canicule.
