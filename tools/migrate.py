# -*- coding: utf-8 -*-
"""Script de migration (One-off) : Fichiers Excel (Météociel) → Base Parquet.

Parcourt tous les anciens fichiers Forecast-*.xlsx du dossier `Forecasts/`,
extrait les onglets modèles (ECMWF, AIFS, GEFS), pivote les données au format
"Tidy" et les intègre dans `data/database_paris.parquet`.

Rôle historique : c'est ce script qui a rétro-rempli la base avant la bascule
pipeline (config.PIPELINE_LIVE_SINCE). Il n'a plus vocation à être relancé —
pour combler une absence ponctuelle, passer par l'import ciblé du dashboard
(app/data/legacy_import.py), qui offre bien plus de garde-fous. Conservé comme
référence du mapping xlsx → schéma plat.
"""

import os
import glob
import re
import shutil
import datetime as dt
import pandas as pd

# --------------------------------------------------------------------------- #
# Configuration — chemins ancrés sur la racine du projet (parent de tools/),
# le script reste donc lançable depuis n'importe quel répertoire courant.
# --------------------------------------------------------------------------- #
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OLD_DIR = os.path.join(ROOT, "Forecasts")
NEW_DB_PATH = os.path.join(ROOT, "data", "database_paris.parquet")
SCHEMA = ['run_date', 'model', 'member', 'valid_time', 't850']


def parse_filename(filename):
    """Extrait la date et l'heure du run depuis le nom du fichier."""
    m = re.search(r"Forecast-(\d{8})-(0Z|6Z|12Z|18Z)\.xlsx", os.path.basename(filename))
    if not m:
        return None
    date_str, hour_str = m.group(1), m.group(2)
    return dt.datetime(
        year=int(date_str[4:8]),
        month=int(date_str[2:4]),
        day=int(date_str[0:2]),
        hour=int(hour_str.replace("Z", ""))
    )


def extract_member_id(col_name):
    """Convertit le nom de colonne (ex: '1', 'DET', 'GFS') en entier."""
    col_str = str(col_name).strip().upper()
    if col_str in ["DET", "GFS"]:
        return 0  # Le déterministe / run de contrôle devient le membre 0
    m = re.search(r"(\d+)", col_str)
    return int(m.group(1)) if m else None


def migrate():
    files = glob.glob(os.path.join(OLD_DIR, "Forecast-*.xlsx"))
    if not files:
        print(f"Aucun fichier Excel trouvé dans {OLD_DIR}/.")
        return

    frames = []
    
    for f in files:
        if "~$" in os.path.basename(f): 
            continue
            
        run_date = parse_filename(f)
        if not run_date:
            continue
            
        print(f"Lecture de {os.path.basename(f)} (Run: {run_date})")
        
        try:
            xl = pd.ExcelFile(f)
        except Exception as e:
            print(f"  Impossible de lire {f} : {e}")
            continue

        for sheet in xl.sheet_names:
            model_name = sheet.strip()
            if model_name not in ["ECMWF", "AIFS", "GEFS"]:
                continue
                
            try:
                # Les données commençaient ligne 4 (skiprows=3) dans l'ancienne archi
                df = pd.read_excel(xl, sheet_name=sheet, skiprows=3).dropna(how="all")
            except Exception:
                continue
                
            if "Date" not in df.columns:
                continue
                
            df["valid_time"] = pd.to_datetime(df["Date"], errors="coerce")
            df = df.dropna(subset=["valid_time"])
            
            member_cols = [c for c in df.columns if str(c) not in ["Date", "Ech.", "Ech", "valid_time"]]
            
            # Pivot des données (Large -> Plat)
            melted = df.melt(
                id_vars=["valid_time"], 
                value_vars=member_cols, 
                var_name="raw_member", 
                value_name="t850"
            )
            
            # Nettoyage et application du schéma
            melted["t850"] = pd.to_numeric(melted["t850"], errors="coerce")
            melted = melted.dropna(subset=["t850"])
            
            melted["run_date"] = run_date
            melted["model"] = model_name
            melted["member"] = melted["raw_member"].apply(extract_member_id)
            
            melted = melted.dropna(subset=["member"])
            melted["member"] = melted["member"].astype(int)
            
            frames.append(melted[SCHEMA])

    if not frames:
        print("Aucune donnée valide n'a été extraite.")
        return

    # Concaténation de l'historique récupéré
    master_df = pd.concat(frames, ignore_index=True)
    master_df["run_date"] = pd.to_datetime(master_df["run_date"])
    master_df["valid_time"] = pd.to_datetime(master_df["valid_time"])
    
    # Dédoublonnage de sécurité
    master_df = master_df.drop_duplicates(subset=["run_date", "model", "member", "valid_time"])

    # Fusion avec la base existante si le nouveau script a déjà tourné
    os.makedirs(os.path.dirname(NEW_DB_PATH), exist_ok=True)
    if os.path.exists(NEW_DB_PATH):
        # Copie datée dans data/backups/ (gitignoré) — même convention que
        # l'import ciblé du dashboard (app/data/legacy_import.py).
        backup_dir = os.path.join(os.path.dirname(NEW_DB_PATH), "backups")
        os.makedirs(backup_dir, exist_ok=True)
        backup_path = os.path.join(
            backup_dir,
            os.path.basename(NEW_DB_PATH).replace(
                ".parquet", f"_backup_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.parquet"))
        shutil.copy2(NEW_DB_PATH, backup_path)
        print(f"  Sauvegarde créée : {backup_path}")
        existing = pd.read_parquet(NEW_DB_PATH)
        for col in ["run_date", "valid_time"]:
            if getattr(existing[col].dtype, "tz", None) is not None:
                existing[col] = existing[col].dt.tz_convert(None)
        master_df = pd.concat([master_df, existing], ignore_index=True)
        master_df = master_df.drop_duplicates(subset=["run_date", "model", "member", "valid_time"])

    # Normalisation finale timezone (évite les comparaisons tz-naive vs tz-aware)
    for col in ["run_date", "valid_time"]:
        master_df[col] = pd.to_datetime(master_df[col], utc=True).dt.tz_convert(None)

    # Tri final et écriture
    master_df = master_df.sort_values(["run_date", "model", "member", "valid_time"]).reset_index(drop=True)
    master_df.to_parquet(NEW_DB_PATH, index=False)
    
    print(f"\n✅ Migration réussie : {len(master_df):,} lignes historiques centralisées dans {NEW_DB_PATH}.")

if __name__ == "__main__":
    migrate()