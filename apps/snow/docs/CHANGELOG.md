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
