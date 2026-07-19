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

## [0.1.0] - 2026-07-19
- Squelette initial du dashboard neige : pipeline ensemble + maille fine,
  pages Vue d'ensemble / Explorer un run / Convergence, entrée `snow_app.py`,
  versioning et changelog séparés du canicule.
