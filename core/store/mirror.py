# -*- coding: utf-8 -*-
"""Écriture d'un parquet vers le magasin externe, par partitions mensuelles
(« sortie de git », docs/DESIGN_sortie_git.md).

Config-agnostique comme tout core/ : le magasin et les réglages arrivent en
paramètres, jamais par un import de config.

Seules les partitions dont l'ensemble de lignes a CHANGÉ sont ré-uploadées ;
les mois clos restent intacts (backstop immuable). Chaque partition uploadée
est re-téléchargée et comparée à sa tranche — jamais un upload « à l'aveugle ».

L'appel est encapsulé pour ne JAMAIS faire échouer le pipeline appelant : un
échec est journalisé, pas propagé. La conséquence diffère selon l'application —
côté canicule (double écriture) git a déjà tout, côté neige (magasin source de
vérité) le flux repart simplement du magasin au poll suivant, `persist()`
recalculant la fusion par clé.

Le pipeline canicule racine garde sa propre implémentation inline (partie
critique, cf. CLAUDE.md) — ce module sert les pipelines neige, qui passent déjà
par core/pipeline/."""

import os
import tempfile

import pandas as pd

from core.store.partition import partition_name, same_rows, split_by_month


def changed_months(existing, combined, time_col) -> list:
    """Clés de mois dont l'ensemble de lignes diffère entre `existing` et
    `combined` — fonction pure, testable. Une partition inchangée n'est jamais
    ré-uploadée (un mois clos reste figé = backstop immuable)."""
    if combined is None or combined.empty:
        return []
    if existing is None or existing.empty:
        return sorted(split_by_month(combined, time_col))
    changed = pd.concat([existing, combined]).drop_duplicates(keep=False)
    if changed.empty:
        return []
    return sorted(pd.to_datetime(changed[time_col]).dt.strftime("%Y-%m").unique())


def mirror_to_store(store, existing, combined, db_path, time_col, *, log=print):
    """Uploade vers `store` les partitions mensuelles modifiées de `combined`
    (le préfixe d'asset se déduit du basename de `db_path`). Encapsulé pour ne
    JAMAIS faire échouer le pipeline appelant. Renvoie la liste des partitions
    effectivement vérifiées (vide si rien à faire ou si le miroir a échoué)."""
    done = []
    try:
        months = changed_months(existing, combined, time_col)
        if not months:
            return done
        prefix = os.path.splitext(os.path.basename(db_path))[0]
        c_month = pd.to_datetime(combined[time_col]).dt.strftime("%Y-%m").to_numpy()
        with tempfile.TemporaryDirectory() as tmp:
            for key in months:
                sub = combined[c_month == key]
                if sub.empty:
                    continue
                name = partition_name(prefix, key)
                path = os.path.join(tmp, name)
                sub.to_parquet(path, index=False)
                store.upload(name, path)
                back = os.path.join(tmp, "back_" + name)
                store.download(name, back)
                if not same_rows(pd.read_parquet(back), sub):
                    raise RuntimeError(f"partition {name} divergente après upload")
                log(f"   🪞 miroir store : {name} ({len(sub):,} lignes) vérifié")
                done.append(name)
    except Exception as exc:  # noqa: BLE001 — jamais bloquant pour le pipeline
        log(f"   ⚠️  miroir store échoué (repris au poll suivant) : {exc}")
    return done

