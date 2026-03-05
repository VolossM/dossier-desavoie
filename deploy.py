#!/usr/bin/env python3
"""
deploy.py — Déploiement versionné du dossier DeSavoie/Chicherit
Usage :
  python deploy.py                   # détecter versions, mettre à jour manifest, push GitHub
  python deploy.py --local           # serveur HTTP local uniquement (port 8080)
  python deploy.py --dry-run         # simuler sans push ni modification
  python deploy.py -m "commentaire"  # message de commit personnalisé
  python deploy.py --hash-only       # recalculer les hashes sans push

Stratégie de versionnage :
  Windows ajoute automatiquement " (1)", " (2)"… aux fichiers téléchargés en double.
  Ce script détecte le numéro le plus élevé disponible pour chaque module,
  le copie sous le nom canonique (sans suffixe), calcule son SHA-256,
  met à jour manifest.json, puis git add/commit/push.
  Le hash de chaque version est conservé dans manifest.json pour détecter
  toute altération ultérieure.
"""

import os
import sys
import re
import json
import hashlib
import shutil
import subprocess
import argparse
import http.server
import threading
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────
SCRIPT_DIR   = Path(__file__).parent.resolve()
DATA_DIR     = SCRIPT_DIR / "data"
MANIFEST     = SCRIPT_DIR / "manifest.json"
DOWNLOADS    = Path(os.path.expanduser("~")) / "Downloads"
LOCAL_PORT   = 8080
MAX_VERSIONS = 9   # Cherche jusqu'à " (9)"

# ─────────────────────────────────────────────
# Utilitaires console
# ─────────────────────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
RED    = "\033[31m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
CYAN   = "\033[36m"
DIM    = "\033[2m"

def h(t):    print(f"\n{BOLD}{CYAN}▶ {t}{RESET}")
def ok(t):   print(f"  {GREEN}✓{RESET}  {t}")
def warn(t): print(f"  {YELLOW}⚠{RESET}  {t}")
def info(t): print(f"  {DIM}{t}{RESET}")
def err(t):  print(f"  {RED}✗{RESET}  {t}"); sys.exit(1)

def run(cmd, cwd=None, check=True):
    result = subprocess.run(
        cmd, shell=True, cwd=str(cwd or SCRIPT_DIR),
        capture_output=True, text=True
    )
    if check and result.returncode != 0:
        err(f"Commande échouée : {cmd}\n{result.stderr.strip()}")
    return result

# ─────────────────────────────────────────────
# SHA-256
# ─────────────────────────────────────────────
def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

# ─────────────────────────────────────────────
# Détection de version Windows
# ─────────────────────────────────────────────
def find_latest_version(canonical_path: Path) -> tuple[Path, int]:
    """
    Cherche dans Downloads (et dans le dossier du fichier) la version
    la plus récente d'un fichier, en tenant compte des suffixes Windows " (N)".
    Retourne (chemin_trouvé, numéro_version) ou (None, 0) si rien.

    Exemple :
      canonical : data/reseau_goldberg.ftm.jsonl
      cherche   : ~/Downloads/reseau_goldberg.ftm.jsonl
                  ~/Downloads/reseau_goldberg (1).ftm.jsonl
                  ~/Downloads/reseau_goldberg (2).ftm.jsonl  ← retourne celui-ci si le plus élevé
    """
    stem = canonical_path.stem       # "reseau_goldberg.ftm"
    suffix = canonical_path.suffix   # ".jsonl"
    # Pour les noms comme "manifest.json" (pas de double extension)
    # on traite stem = "manifest", suffix = ".json"

    candidates = []

    # Cherche dans Downloads
    for search_dir in [DOWNLOADS, canonical_path.parent]:
        if not search_dir.exists():
            continue
        # Version de base (sans suffixe Windows)
        base = search_dir / canonical_path.name
        if base.exists():
            candidates.append((base, 0))
        # Versions numérotées
        for n in range(1, MAX_VERSIONS + 1):
            versioned = search_dir / f"{stem} ({n}){suffix}"
            if versioned.exists():
                candidates.append((versioned, n))

    if not candidates:
        return None, 0

    # Garder la version la plus élevée parmi celles trouvées
    best_path, best_n = max(candidates, key=lambda x: (x[1], x[0].stat().st_size))
    return best_path, best_n

# ─────────────────────────────────────────────
# Comparaison avec le hash du manifest
# ─────────────────────────────────────────────
STATUS_OK      = "✓ conforme"
STATUS_NEW     = "↑ nouvelle version"
STATUS_ALTERED = "✗ ALTÉRÉ"
STATUS_MISSING = "— absent"
STATUS_UNKNOWN = "? non vérifié"

def check_integrity(file_entry: dict, current_path: Path) -> tuple[str, str]:
    """
    Retourne (statut, hash_actuel).
    file_entry : entrée du manifest pour ce fichier.
    """
    if not current_path.exists():
        return STATUS_MISSING, ""
    current_hash = sha256(current_path)
    expected = file_entry.get("sha256")
    if not expected:
        return STATUS_UNKNOWN, current_hash
    if current_hash == expected:
        return STATUS_OK, current_hash
    # Hash différent : nouvelle version légitime ou altération ?
    # On ne peut distinguer que si l'utilisateur a validé explicitement.
    # Par défaut on signale STATUS_NEW (à confirmer manuellement si suspect).
    return STATUS_NEW, current_hash

# ─────────────────────────────────────────────
# Mise à jour du manifest
# ─────────────────────────────────────────────
def update_manifest(manifest_data: dict, updates: list[dict], dry_run: bool) -> dict:
    """
    updates : liste de { "path": ..., "sha256": ..., "version": ..., "size": ... }
    """
    files = manifest_data.get("files", [])
    update_map = {u["path"]: u for u in updates}
    changed = False
    for entry in files:
        p = entry.get("path")
        if p in update_map:
            u = update_map[p]
            old_hash = entry.get("sha256", "")
            if old_hash != u["sha256"]:
                entry["sha256"]   = u["sha256"]
                entry["version"]  = u["version"]
                entry["size"]     = u["size"]
                entry["deployed"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                changed = True
    # Même chose pour manifest lui-même
    manifest_data["_last_deploy"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not dry_run and changed:
        with open(MANIFEST, "w", encoding="utf-8") as f:
            json.dump(manifest_data, f, ensure_ascii=False, indent=2)
    return manifest_data, changed

# ─────────────────────────────────────────────
# Serveur local
# ─────────────────────────────────────────────
def serve_local():
    os.chdir(SCRIPT_DIR)
    handler = http.server.SimpleHTTPRequestHandler
    # Silencer les logs de requête
    class QuietHandler(handler):
        def log_message(self, fmt, *args): pass
        def log_request(self, *args): pass

    with http.server.HTTPServer(("", LOCAL_PORT), QuietHandler) as httpd:
        url = f"http://localhost:{LOCAL_PORT}/"
        print(f"\n  {BOLD}Serveur local démarré{RESET}")
        print(f"  URL : {CYAN}{url}{RESET}")
        print(f"  {DIM}Ctrl+C pour arrêter{RESET}\n")
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n  Serveur arrêté.")

# ─────────────────────────────────────────────
# Chargement config deploy
# ─────────────────────────────────────────────
def load_deploy_config() -> dict:
    config_path = SCRIPT_DIR / ".deploy_config.json"
    if config_path.exists():
        with open(config_path) as f:
            return json.load(f)
    return {}

# ─────────────────────────────────────────────
# Pipeline principal
# ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Déploiement dossier DeSavoie")
    parser.add_argument("-m", "--message",   default="", help="Message de commit")
    parser.add_argument("--local",     action="store_true", help="Serveur local uniquement")
    parser.add_argument("--dry-run",   action="store_true", help="Simuler sans modifier")
    parser.add_argument("--hash-only", action="store_true", help="Recalculer hashes uniquement")
    parser.add_argument("--no-open",   action="store_true", help="Ne pas ouvrir le navigateur")
    args = parser.parse_args()

    # Mode serveur local
    if args.local:
        serve_local()
        return

    h("Détection des versions (Downloads → dossier data)")

    # Charger le manifest
    if not MANIFEST.exists():
        err(f"manifest.json introuvable dans {SCRIPT_DIR}")
    with open(MANIFEST, encoding="utf-8") as f:
        manifest_data = json.load(f)

    files = manifest_data.get("files", [])
    updates = []
    rows = []

    for entry in files:
        rel_path = entry.get("path", "")
        canonical = SCRIPT_DIR / rel_path
        found_path, version_n = find_latest_version(canonical)

        if found_path is None:
            status = STATUS_MISSING
            current_hash = entry.get("sha256", "")
            size = 0
            rows.append((rel_path.split("/")[-1], "—", status, "—"))
            continue

        # Copier vers le chemin canonique si ce n'est pas déjà lui
        if not args.dry_run and found_path != canonical:
            canonical.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(found_path, canonical)

        current_hash = sha256(found_path if args.dry_run else canonical)
        size = (found_path if args.dry_run else canonical).stat().st_size
        expected = entry.get("sha256", "")

        if not expected:
            status = STATUS_UNKNOWN
        elif current_hash == expected:
            status = STATUS_OK
        else:
            status = STATUS_NEW

        version_label = f"v{version_n}" if version_n > 0 else "base"
        rows.append((rel_path.split("/")[-1], version_label, status, f"{size:,} o"))
        updates.append({
            "path": rel_path, "sha256": current_hash,
            "version": version_n, "size": size
        })

    # Afficher le tableau de statut
    col1 = max(len(r[0]) for r in rows) + 2
    col2 = 8
    col3 = 18
    print(f"\n  {'Fichier':<{col1}} {'Version':<{col2}} {'Intégrité':<{col3}} Taille")
    print(f"  {'─'*col1} {'─'*col2} {'─'*col3} {'─'*10}")
    for name, ver, status, size in rows:
        color = GREEN if "conforme" in status else YELLOW if "nouvelle" in status or "?" in status else RED if "ALTÉRÉ" in status else DIM
        print(f"  {name:<{col1}} {ver:<{col2}} {color}{status:<{col3}}{RESET} {size}")

    if args.hash_only or args.dry_run:
        if not args.dry_run:
            _, changed = update_manifest(manifest_data, updates, dry_run=False)
            if changed:
                ok("manifest.json mis à jour avec les nouveaux hashes.")
            else:
                ok("Aucun changement détecté dans les hashes.")
        else:
            info("Mode dry-run : aucune modification effectuée.")
        return

    # Mettre à jour manifest.json
    _, manifest_changed = update_manifest(manifest_data, updates, dry_run=False)
    if manifest_changed:
        ok("manifest.json mis à jour.")
    else:
        info("manifest.json inchangé.")

    h("Git — staging et commit")

    # Vérifier s'il y a des changements
    status_out = run("git status --porcelain", check=False).stdout.strip()
    if not status_out:
        ok("Aucun changement à committer — dossier déjà à jour.")
        deploy_config = load_deploy_config()
        if deploy_config.get("pages_url"):
            print(f"\n  URL publique : {CYAN}{deploy_config['pages_url']}{RESET}\n")
        return

    run("git add -A")

    # Construire le message de commit
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    changed_files = [r[0] for r in rows if r[2] in (STATUS_NEW, STATUS_UNKNOWN)]
    auto_msg = f"deploy {ts}"
    if changed_files:
        auto_msg += f" — {', '.join(changed_files[:3])}"
        if len(changed_files) > 3:
            auto_msg += f" +{len(changed_files)-3}"
    commit_msg = args.message if args.message else auto_msg

    run(f'git commit -m "{commit_msg}"')
    ok(f"Commit : {commit_msg}")

    h("Push vers GitHub")

    # Récupérer le remote avec token si disponible
    deploy_config = load_deploy_config()
    remote_auth = deploy_config.get("remote_auth")
    if remote_auth:
        push_cmd = f"git push {remote_auth} main"
    else:
        push_cmd = "git push origin main"

    push_result = run(push_cmd, check=False)
    if push_result.returncode != 0:
        warn(f"Push échoué : {push_result.stderr.strip()}")
        warn("Vérifiez votre connexion et les droits du token GitHub.")
        warn("Pour relancer manuellement : git push origin main")
    else:
        ok("Push effectué.")

    # Résumé
    pages_url = deploy_config.get("pages_url", "")
    if pages_url:
        print(f"""
  {BOLD}Déploiement terminé{RESET}
  URL : {CYAN}{pages_url}{RESET}
  {DIM}(mise à jour GitHub Pages dans ~30 secondes){RESET}
""")
        if not args.no_open:
            time.sleep(2)
            webbrowser.open(pages_url)
    else:
        print(f"\n  {BOLD}Déploiement terminé.{RESET} Lancez setup.py pour configurer GitHub Pages.\n")

if __name__ == "__main__":
    main()
