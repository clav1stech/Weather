# -*- coding: utf-8 -*-
"""Page « Indicateur de canicule » — vue vulgarisée du domaine canicule.

Exception voulue (invariant CLAUDE.md) : les sections « évolution au fil des
runs » et « confiance » réutilisent la MÉCANIQUE de la page Convergence
(super-ensembles complétés, cf. trend_daily_medians), pas
latest_complete_run_sub — comparer des runs entre eux exige des pools à
modèles équivalents (backfill). Ne pas « unifier » les deux."""

from datetime import datetime

import pandas as pd
import streamlit as st

import config as C
from app.data.runsets import latest_complete_run_sub, latest_z500_sub, trend_daily_medians
from app.data.t2m import t2m_signature, txtn_by_day
from app.stats.climato import clim_normal, clim_params
from app.stats.ensemble import daily_aggregate, daily_risk, super_ensemble
from app.ui.components import complete_runs_caption
from app.domains.heatwave.charts import (
    calendrier_risques, confiance_chart, ligne_de_flottaison, tendance_heatmap)
from app.domains.heatwave.logic import (
    PROB_CANICULE_QUASI, PROB_RISQUE_MARQUE, PROB_RISQUE_MODERE, episode_chaleur,
    signal_synoptique, tendance_recente)


def page_grand_public(runs, sig):
    st.title("🌞 Indicateur de canicule")
    if runs.empty:
        st.warning("Base vide.")
        return
    sub, sources, _ = latest_complete_run_sub(sig)
    st.caption("Super-ensemble des **derniers runs complets** par modèle · "
               f"{complete_runs_caption(sources)}")

    with st.expander("❓ Comment lire cet indicateur — la température à 850 hPa (T850)"):
        st.markdown(
            "**Pourquoi « 850 hPa » ?** Cet indicateur ne suit pas la température au sol, "
            "mais la **température de l'air vers 1 500 m d'altitude** (niveau de pression "
            "850 hPa, noté *T850*). C'est la référence des météorologues pour juger d'une "
            "vague de chaleur : elle décrit la masse d'air sur toute la région, sans être "
            "faussée par les effets locaux (vent, humidité du sol, chaleur urbaine).\n\n"
            "**Elle ne varie quasiment pas entre le jour et la nuit.** Contrairement au "
            "thermomètre au sol qui grimpe l'après-midi et retombe la nuit, la T850 reste "
            "stable sur 24 h : **une seule valeur résume donc la journée entière.**\n\n"
            "**Repères (plaine, été) — au sol il fait en gros _T850 + 15 °C_ :**\n"
            "- **≈ 14–15 °C à 850 hPa → ~30 °C au sol** : chaleur notable.\n"
            "- **≈ 18–20 °C à 850 hPa → ~35 °C au sol** : canicule exceptionnelle.\n\n"
            "En clair, **au-delà de ~18 °C de T850, le signal doit alerter.**\n\n"
            "**⚠️ Le « +15 °C » n'est qu'un ordre de grandeur.** Pour une même T850, la "
            "température réelle au sol (T2m) peut varier de plusieurs degrés. "
            "Ce qui creuse l'écart :\n"
            "- **Ensoleillement et durée du jour** : ciel clair et journées longues → le sol "
            "chauffe plus fort l'air en surface.\n"
            "- **Sécheresse du sol** : un sol sec n'évapore plus d'eau, donc toute l'énergie "
            "solaire part en chaleur (les canicules s'auto-amplifient avec la sécheresse).\n"
            "- **Subsidence anticyclonique** : l'air qui descend se comprime, se réchauffe et "
            "écrase la couche d'air près du sol.\n"
            "- **Advection / vent** : un flux de sud peut amener de l'air encore plus chaud à "
            "basse altitude.\n\n"
            "À l'inverse, **nuages, sol humide, vent marin ou matinée** réduisent l'écart. "
            "La T850 indique le *potentiel* de chaleur ; ces facteurs décident jusqu'où il "
            "se réalise au sol.")

    with st.expander("📡 D'où viennent ces prévisions ? (modèles, runs, super-ensemble)"):
        n_main = len(C.MAIN_LABELS)
        models_bullets = "\n".join(f"- **{m['label']}** — {m['desc']}." for m in C.MODELS)
        st.markdown(
            f"**{len(C.MODELS)} modèles d'ensemble sont combinés :**\n"
            f"{models_bullets}\n\n"
            "**Pourquoi un « ensemble » ?** Chaque modèle est relancé avec de légères "
            "variations des conditions initiales, produisant des dizaines de **scénarios "
            "(« membres »)**. Leur dispersion = la mesure de l'incertitude.\n\n"
            "**Runs 0Z / 6Z / 12Z / 18Z.** Un *run* est un calcul lancé à heure fixe en "
            "**temps universel (UTC)**. Chaque modèle a son propre rythme : certains "
            f"(dont les {n_main} modèles principaux) cyclent jusqu'à 4 fois par jour, "
            "d'autres (ex. GEM) seulement 2 fois (0Z/12Z) — la base se met à jour modèle "
            "par modèle, pas par fournée unique.\n\n"
            "**Mise à jour automatique.** Le pipeline interroge l'API Open-Meteo 4 fois par "
            "jour et ne retient, pour chaque modèle, que les échéances réellement "
            "renouvelées par rapport au run précédent (comparaison échéance par échéance) — "
            "jamais de queue recopiée de l'ancien cycle sous une étiquette erronée.\n\n"
            "**Super-ensemble.** Plutôt qu'un seul modèle, cette appli **met en commun tous "
            "les scénarios des modèles disponibles** : c'est le *super-ensemble*. Une "
            "prévision partagée par de nombreux scénarios issus de modèles différents est "
            "plus solide.\n\n"
            "**Ce qu'affichent les graphiques :**\n"
            "- *Indicateur de canicule* et *Vue d'ensemble* → le **super-ensemble** "
            "combinant, pour **chaque modèle, son dernier run à horizon plein** "
            "(les cycles trop courts sont écartés) — pas forcément le même cycle "
            "d'un modèle à l'autre.\n"
            "- *Explorer un run* → au choix : le super-ensemble (onglet **Panache**), "
            "**un seul modèle** détaillé scénario par scénario (onglet **Spaghetti**), ou la "
            "**comparaison des médianes** de chaque modèle (onglet **Modèles**).")

    with st.expander("⚙️ Réglages avancés"):
        col_a, col_b = st.columns(2)
        seuil_chaleur = col_a.number_input("Seuil chaleur (°C @850)", 10.0, 25.0,
                                           float(C.SEUIL_CHALEUR_850), 0.5)
        seuil_canicule = col_b.number_input("Seuil canicule (°C @850)", 10.0, 30.0,
                                            float(C.SEUIL_CANICULE_850), 0.5)
        if seuil_canicule <= seuil_chaleur:
            st.warning("Seuil canicule corrigé (doit dépasser le seuil chaleur).")
            seuil_canicule = seuil_chaleur + 0.5

        st.markdown("---")
        st.caption(
            "**Normale climatique (T850).** Modélisée par un cosinus saisonnier "
            "`moyenne + amplitude × cos(2π(jour − pic)/365)` — ce sont des valeurs "
            "**estimées**, pas une normale officielle calculée sur une série d'observations. "
            "Ajustez-les ici si elles ne correspondent pas à votre référence ; le réglage "
            "s'applique à toute l'appli (cartes KPI et graphiques) tant que la session reste "
            "ouverte.")
        col_m, col_amp, col_pic = st.columns(3)
        mean0, amp0, peak0 = clim_params()
        col_m.number_input("Moyenne annuelle (°C @850)", -5.0, 20.0,
                           float(mean0), 0.5, key="clim_mean")
        col_amp.number_input("Amplitude saisonnière (°C)", 0.0, 15.0,
                             float(amp0), 0.5, key="clim_amplitude")
        col_pic.number_input("Jour du pic (1-365, ~17 juil. = 198)",
                             1, 365, int(peak0), 1, key="clim_peak_doy")
        st.caption(f"Normale du jour actuel : {clim_normal(pd.Timestamp(datetime.now().date())):.1f} °C")

    syn = super_ensemble(sub)
    if syn is None or syn.empty:
        st.error("Aucune donnée exploitable.")
        return
    st.caption(f"Synthèse combinant jusqu'à {int(syn['n_membres'].max())} membres "
               f"({', '.join(sorted(sub['model'].unique()))}) par échéance.")

    jours = daily_risk(sub, seuil_canicule)
    if jours is None or jours.empty:
        st.error("Risque non calculable.")
        return

    eleve = jours["prob"] >= PROB_CANICULE_QUASI
    high_dates = jours.loc[eleve, "date"].sort_values().tolist()
    high_set = set(high_dates)
    today = pd.Timestamp(datetime.now().date())
    pic = jours.loc[jours["prob"].idxmax()]
    c1, c2, c3 = st.columns(3)
    if not high_dates:
        # Statut GRADUÉ (mêmes paliers que le calendrier) : « aucune canicule
        # probable » ne veut pas dire « rien à signaler » — un pic à 37 % ou une
        # semaine de chaleur notable doivent apparaître, pas un statut vide.
        avenir = jours[jours["date"] >= today]
        chauds = avenir[avenir["Médiane"] >= seuil_chaleur]
        pic_av = avenir.loc[avenir["prob"].idxmax()] if not avenir.empty else None
        if pic_av is not None and pic_av["prob"] >= PROB_RISQUE_MARQUE:
            c1.metric("Statut canicule", "🟠 Risque à surveiller",
                      help=f"Pas de canicule probable (≥ {PROB_CANICULE_QUASI:.0%}) à ce "
                           f"stade, mais le risque monte à {pic_av['prob']:.0%} "
                           f"le {pic_av['date']:%a %d %b}.")
        elif pic_av is not None and pic_av["prob"] >= PROB_RISQUE_MODERE:
            c1.metric("Statut canicule", "🟡 Signal faible",
                      help=f"Quelques scénarios voient une canicule (jusqu'à "
                           f"{pic_av['prob']:.0%} le {pic_av['date']:%a %d %b}) — "
                           f"minoritaires, à suivre.")
        elif not chauds.empty:
            c1.metric("Statut canicule", "🌡️ Chaleur sans canicule",
                      help=f"Pas de canicule en vue, mais de la chaleur notable "
                           f"(≥ {seuil_chaleur:.0f} °C @850) est prévue autour du "
                           f"{chauds.iloc[0]['date']:%a %d %b}.")
        else:
            c1.metric("Statut canicule", "🟢 Aucune en vue")
        # 2e carte adaptée au niveau d'alerte : jours à surveiller (risque marqué),
        # sinon jours de chaleur notable, sinon rien à quantifier.
        n_watch = int((avenir["prob"] >= PROB_RISQUE_MARQUE).sum()) if not avenir.empty else 0
        if n_watch:
            c2.metric("Jours à surveiller", f"{n_watch} jour{'s' if n_watch > 1 else ''}",
                      help=f"Jours avec au moins {PROB_RISQUE_MARQUE:.0%} de risque de canicule.")
        elif not chauds.empty:
            n_ch = len(chauds)
            c2.metric("Chaleur notable", f"{n_ch} jour{'s' if n_ch > 1 else ''}",
                      help=f"Jours dont la médiane atteint {seuil_chaleur:.0f} °C @850 "
                           f"(≈ 30 °C au sol), sans franchir le seuil canicule.")
        else:
            c2.metric("Durée prévue", "—")
    else:
        # Durée/fin d'épisode TOLÉRANTES aux creux chauds (episode_chaleur) :
        # un jour sous PROB_CANICULE_QUASI mais ≥ seuil_chaleur en médiane ne
        # coupe pas l'épisode affiché. Le badge Statut, lui, reste strict
        # (aujourd'hui ∈ high_set), définition inchangée.
        if today in high_set:
            ep_cours = episode_chaleur(jours, seuil_chaleur, depuis=today)
            c1.metric("Statut canicule", "🔴 En cours",
                      help=f"Au moins jusqu'au {ep_cours['fin']:%a %d %b}")
        else:
            prochaine = next((d for d in high_dates if d > today), high_dates[0])
            c1.metric("Prochaine canicule", prochaine.strftime("%a %d %b"))
        ep = episode_chaleur(jours, seuil_chaleur)
        if ep["jours_creux"]:
            aide = (f"Du {ep['debut']:%a %d %b} au {ep['fin']:%a %d %b} : "
                    f"{ep['jours_canicule']} jour{'s' if ep['jours_canicule'] > 1 else ''} "
                    f"de canicule probable (≥ {PROB_CANICULE_QUASI:.0%}) et "
                    f"{ep['jours_creux']} jour{'s' if ep['jours_creux'] > 1 else ''} "
                    f"de creux — probabilité moindre mais chaleur notable maintenue "
                    f"(≥ {seuil_chaleur:.0f} °C @850) : l'épisode ne s'interrompt pas.")
        else:
            aide = (f"Du {ep['debut']:%a %d %b} au {ep['fin']:%a %d %b}, "
                    f"jours consécutifs de canicule probable "
                    f"(≥ {PROB_CANICULE_QUASI:.0%}).")
        c2.metric("Durée de l'épisode",
                  f"{ep['duree']} jour{'s' if ep['duree'] > 1 else ''}", help=aide)
    c3.metric("Pic de risque", f"{pic['prob'] * 100:.0f} %",
              help=f"{pic['date']:%a %d %b} · médiane {pic['Médiane']:.1f} °C")

    # ── Contexte atmosphérique (Z500) — appui discret du message T850 ─────────
    # Signal qualitatif uniquement : la valeur brute du géopotentiel ne parle
    # qu'aux spécialistes (lecture technique : Explorer un run → onglet 🌀 Z500).
    # Pool DÉDIÉ (latest_z500_sub) plutôt que `sub` : chaque modèle apporte sa
    # dernière valeur z500 connue, quitte à remonter à un run plus ancien que
    # celui retenu pour T850. None (z500 absent de toute la base, ex. runs
    # legacy) → rien d'affiché, le message principal reste strictement identique.
    signal = signal_synoptique(latest_z500_sub(sig), today)
    if signal is not None:
        icone, libelle, phrase = signal
        st.markdown(f"{icone} **Configuration atmosphérique : {libelle}.** {phrase}")
        st.caption("Lecture de la circulation d'altitude (géopotentiel 500 hPa) sur les "
                   f"{len(C.MODELS)} modèles combinés — un éclairage en appui de "
                   "l'indicateur principal ci-dessus, pas un critère de risque "
                   "supplémentaire.")

    st.subheader("📈 Évolution de la chaleur prévue")
    st.caption("Courbe foncée = médiane ; bande rouge = P10–P90 ; pointillés bleus = "
               "normale climatique saisonnière ; orange/rouge = seuils d'alerte.")
    st.plotly_chart(ligne_de_flottaison(syn, seuil_chaleur, seuil_canicule,
                                        "Température à 850 hPa — tendance et incertitude"),
                    width="stretch")

    st.subheader("🗓️ Calendrier du risque de canicule")
    # Tx/Tn haute résolution (flux annexe, parquet séparé — cf. app/data/t2m.py) :
    # appui d'affichage en lecture seule sur ~4 jours, jamais un critère de
    # risque. On ne garde que les jours du calendrier (≥ aujourd'hui) ; absence
    # de données (fichier manquant, horizon dépassé) = cas normal, rien d'affiché.
    txtn = txtn_by_day(t2m_signature())
    txtn = txtn[txtn["date"] >= today] if not txtn.empty else txtn
    if txtn.empty:
        st.caption(f"Chaque case = un jour, coloré selon P(≥ {seuil_canicule:.0f} °C @850).")
    else:
        modeles = " · ".join(dict.fromkeys(txtn["model"]))  # ordre d'apparition, sans doublon
        st.caption(f"Chaque case = un jour, coloré selon P(≥ {seuil_canicule:.0f} °C @850). "
                   f"Le chiffre = température **max prévue au sol** en °C (modèles haute résolution "
                   f"{modeles}, sur ~7 jours). Le détail — min, modèle, fiabilité (un seul "
                   "modèle au-delà de ~4 jours, ou désaccord marqué entre eux) — s'affiche "
                   "au survol, ou d'un tap sur mobile.")
    st.plotly_chart(calendrier_risques(jours, seuil_canicule, txtn), width="stretch")

    # ── Tendance récente des runs (vulgarisé, en un coup d'œil) ──────────────
    st.subheader("🧭 Les modèles changent-ils d'avis ?")
    st.markdown(
        "Les modèles recalculent la prévision plusieurs fois par jour. La ligne ci-dessous "
        "compare les **calculs des ~3 derniers jours** : pour chaque jour à venir, elle dit "
        "si la prévision a été **revue à la hausse** (🔴, l'épisode se confirme ou "
        "s'intensifie), **à la baisse** (🔵, il se dégonfle) ou si elle est **stable** "
        "(⚪, prévision mûre, plus fiable).")
    trend = trend_daily_medians(sig)  # `today` déjà défini plus haut (KPI)
    tend = tendance_recente(trend)
    tend = tend[tend["target"] >= today] if not tend.empty else tend
    if tend.empty:
        st.info("Pas encore assez de runs en base pour mesurer une tendance.")
    else:
        # Verdict global qualitatif : moyenne des révisions sur la période à venir
        # (seuil plus bas que par jour : une dérive d'ensemble se voit sur la moyenne).
        d_moy = float(tend["delta"].mean())
        if d_moy >= 0.3:
            st.markdown("📈 **Tendance récente : vers plus chaud** — les derniers calculs "
                        "renforcent globalement la chaleur prévue.")
        elif d_moy <= -0.3:
            st.markdown("📉 **Tendance récente : vers moins chaud** — les derniers calculs "
                        "revoient globalement la chaleur à la baisse.")
        else:
            st.markdown("➡️ **Tendance récente : stable** — les derniers calculs confirment "
                        "globalement la prévision.")
        st.plotly_chart(tendance_heatmap(tend), width="stretch")
        st.caption("Pour l'analyse détaillée run par run (avec les valeurs), voir la page "
                   "*Révisions & convergence*.")

    # ── Confiance : la médiane n'est pas une certitude (vulgarisé) ───────────
    st.subheader("🎯 Quelle confiance accorder à ces chiffres ?")
    st.markdown(
        "**La médiane n'est pas une promesse.** C'est simplement le scénario du milieu : "
        "un scénario sur deux est plus chaud, un sur deux plus froid. La vraie information "
        "est la **fourchette** ci-dessous : les modèles jugent très probable (8 chances "
        "sur 10) que la journée tombe dedans. Plus la barre est courte, plus les scénarios "
        "sont d'accord — plus elle s'allonge (c'est inévitable au-delà de quelques jours), "
        "plus il faut lire « ça peut encore bouger », dans un sens comme dans l'autre.")
    daily = daily_aggregate(syn)
    daily = daily[daily["date"] >= today] if daily is not None else None
    if daily is None or daily.empty:
        st.info("Fourchettes journalières non calculables.")
    else:
        st.caption("Couleur de la barre = accord des scénarios : 🟢 groupés (bonne "
                   "confiance) · 🟡 partagés · 🟠 très dispersés (chiffre indicatif). "
                   "Trait foncé = scénario médian.")
        st.plotly_chart(confiance_chart(daily, seuil_chaleur, seuil_canicule),
                        width="stretch")
        # Alertes d'asymétrie, RECALCULÉES à chaque affichage depuis la prévision
        # courante (jamais de contenu figé) : les cas où la médiane, seule,
        # induirait en erreur — queue chaude (minorité de scénarios caniculaires
        # sous une médiane sage) et queue froide (médiane chaude mais rechute
        # possible). Formulation courte, sans valeur brute (« X scénarios sur 10 »).
        info = daily.merge(jours[["date", "prob"]], on="date", how="left")
        chauds, froids = [], []
        for _, r in info.iterrows():
            up, down = r["P90"] - r["Médiane"], r["Médiane"] - r["P10"]
            prob = r.get("prob")
            if pd.notna(prob) and prob >= 0.15 and r["Médiane"] < seuil_canicule:
                sur10 = max(1, round(prob * 10))
                chauds.append((prob, f"**{r['date']:%a %d %b}** : la médiane reste sous le "
                                     f"seuil canicule, mais **{sur10} scénario{'s' if sur10 > 1 else ''} "
                                     f"sur 10 le dépasse{'nt' if sur10 > 1 else ''}** — "
                                     f"le risque n'est pas écarté."))
            elif down - up >= 1.5 and r["Médiane"] >= seuil_chaleur:
                froids.append((down - up, f"**{r['date']:%a %d %b}** : la médiane est élevée, "
                                          f"mais une partie des scénarios reste bien plus "
                                          f"fraîche — la chaleur n'est pas encore acquise."))
        notes = ([n for _, n in sorted(chauds, reverse=True)[:2]]
                 + [n for _, n in sorted(froids, reverse=True)[:2]])[:3]
        if notes:
            st.markdown("**⚖️ À ne pas manquer derrière la médiane** *(recalculé à chaque "
                        "mise à jour)* **:**\n" + "\n".join(f"- {n}" for n in notes))
