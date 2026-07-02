import pathlib
import re
import zipfile
from datetime import datetime

# Configuration
project_dir = pathlib.Path(__file__).parent
export_dir = project_dir / "Export"
ENABLE_ZIP_EXPORT = False

# Extraire la version depuis meteo_app.py
def _get_app_version():
    meteo_app = project_dir / "meteo_app.py"
    with open(meteo_app, "r", encoding="utf-8") as f:
        match = re.search(r'APP_VERSION\s*=\s*"(\d+)\.(\d+)\.(\d+)"', f.read())
        if match:
            return int(match.group(1)), int(match.group(2)), int(match.group(3))
        return 2, 0, 0

VERSION_X, VERSION_Y, _ = _get_app_version()

# Dossiers ignorés (noms de dossier, appliqués à tous les niveaux)
EXCLUDED_DIRS = {
    "data",
    "Export",
    "Forecasts",
    "__pycache__",
    ".git",
    ".claude",
    ".devcontainer",
}

# Fichiers ignorés explicitement
EXCLUDED_FILES = {
    "export_project.py",
    "Forecast_legacy.py",
}

# Extensions incluses
INCLUDED_EXTENSIONS = {".py", ".txt", ".yml", ".bat", ".gitignore"}

export_dir.mkdir(parents=True, exist_ok=True)


def get_next_patch_version():
    pattern = re.compile(rf"_v{VERSION_X}\.{VERSION_Y}\.(\d+)$")
    max_z = -1
    for f in export_dir.iterdir():
        if not f.is_file():
            continue
        m = pattern.search(f.stem)
        if m:
            max_z = max(max_z, int(m.group(1)))
    return max_z + 1


def collect_files():
    files = []
    for path in sorted(project_dir.rglob("*")):
        if not path.is_file():
            continue
        # Ignorer si un dossier parent est exclu
        if any(part in EXCLUDED_DIRS for part in path.relative_to(project_dir).parts[:-1]):
            continue
        # Ignorer fichier lui-même si exclu
        if path.name in EXCLUDED_FILES:
            continue
        # Garder uniquement les extensions pertinentes (.gitignore n'a pas d'extension)
        suffix = path.suffix if path.suffix else path.name
        if suffix not in INCLUDED_EXTENSIONS:
            continue
        files.append(path)
    return files


def export_project():
    version_tag = f"v{VERSION_X}.{VERSION_Y}.{get_next_patch_version()}"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"export_{project_dir.name}_{timestamp}_{version_tag}"

    files = collect_files()
    if not files:
        print("Aucun fichier trouvé.")
        return

    txt_path = export_dir / f"{base_name}.txt"
    with open(txt_path, "w", encoding="utf-8", newline="\n") as out:
        for f in files:
            rel = f.relative_to(project_dir).as_posix()
            out.write(f"===== {rel} =====\n\n")
            try:
                out.write(f.read_text(encoding="utf-8"))
            except Exception as e:
                out.write(f"[Erreur lecture : {e}]")
            out.write("\n\n")

    print(f"Export texte : {txt_path}")

    if ENABLE_ZIP_EXPORT:
        zip_path = export_dir / f"{base_name}.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for f in files:
                zf.write(f, arcname=f.relative_to(project_dir))
        print(f"Archive ZIP  : {zip_path}")


if __name__ == "__main__":
    export_project()
