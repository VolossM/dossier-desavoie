#!/usr/bin/env python3
"""
init_git.py — Initialisation Git dans Downloads et lancement de setup.py
Affaire DeSavoie / Chicherit

Ce script est le seul point d'entrée manuel. Il :
  1. Vérifie les prérequis (Python 3.8+, git)
  2. Se place dans le dossier Downloads de l'utilisateur courant
  3. Crée le .gitignore qui exclut tout sauf les fichiers du projet
  4. Initialise le dépôt Git local (idempotent)
  5. Détecte la version la plus récente de setup.py dans Downloads
     (Windows peut l'avoir renommé "setup (1).py", "setup (2).py"…)
  6. Délègue à setup.py pour la création du dépôt GitHub et le push initial

Usage : double-clic sur init_git.py depuis n'importe quel emplacement.
Prérequis : Git installé (https://git-scm.com/downloads), Python 3.8+.
"""

import os, sys, re, subprocess, shutil, traceback
from pathlib import Path

# ─────────────────────────────────────────────
# Console helpers
# ─────────────────────────────────────────────
R="\033[0m"; B="\033[1m"; RED="\033[31m"; GRN="\033[32m"
YLW="\033[33m"; CYN="\033[36m"; DIM="\033[2m"

def h(t):    print(f"\n{B}{CYN}> {t}{R}")
def ok(t):   print(f"  {GRN}ok{R}  {t}")
def warn(t): print(f"  {YLW}!{R}   {t}")
def info(t): print(f"  {DIM}{t}{R}")
def fail(t): print(f"\n  {RED}{B}ERREUR :{R} {t}"); sys.exit(1)

def run(cmd, cwd=None, check=True):
    r = subprocess.run(cmd, shell=True, cwd=str(cwd or DOWNLOADS),
                       capture_output=True, text=True)
    if check and r.returncode != 0:
        print(f"  stdout : {r.stdout.strip()}")
        print(f"  stderr : {r.stderr.strip()}")
        fail(f"Commande echouee : {cmd}")
    return r

# ─────────────────────────────────────────────
# Détection de la version la plus récente d'un fichier
# Windows renomme les doublons : "setup.py", "setup (1).py", etc.
# ─────────────────────────────────────────────
def find_latest(folder: Path, stem: str, suffix: str) -> Path | None:
    """Retourne le chemin de la version la plus récente de stem+suffix dans folder."""
    candidates = []
    base = folder / f"{stem}{suffix}"
    if base.exists():
        candidates.append((0, base))
    for n in range(1, 10):
        v = folder / f"{stem} ({n}){suffix}"
        if v.exists():
            candidates.append((n, v))
    if not candidates:
        return None
    return max(candidates, key=lambda x: x[0])[1]

# ─────────────────────────────────────────────
# Contenu du .gitignore : exclure tout Downloads
# sauf les fichiers du projet DeSavoie
# ─────────────────────────────────────────────
GITIGNORE = """\
# Downloads — exclure tout par defaut
*

# Reintegrer uniquement les fichiers du projet DeSavoie/Chicherit
!index.html
!setup.py
!deploy.py
!manifest.json
!init_git.py
!.gitignore
!.deploy_config.json
!data/
!data/*.jsonl
"""

# ─────────────────────────────────────────────
# Corps principal
# ─────────────────────────────────────────────
DOWNLOADS = Path.home() / "Downloads"

def main():
    # 1. Prérequis
    h("Verification des prerequis")
    if sys.version_info < (3, 8):
        fail("Python 3.8+ requis.")
    ok(f"Python {sys.version_info.major}.{sys.version_info.minor}")

    git_v = subprocess.run("git --version", shell=True, capture_output=True, text=True)
    if git_v.returncode != 0:
        fail("Git non trouve. Installez Git : https://git-scm.com/downloads")
    ok(git_v.stdout.strip())

    if not DOWNLOADS.exists():
        fail(f"Dossier Downloads introuvable : {DOWNLOADS}")
    ok(f"Downloads : {DOWNLOADS}")

    # 2. .gitignore
    h("Configuration .gitignore")
    gi = DOWNLOADS / ".gitignore"
    if gi.exists():
        warn(".gitignore existant — remplacement.")
    gi.write_text(GITIGNORE, encoding="utf-8")
    ok(".gitignore ecrit (exclusion totale de Downloads sauf fichiers projet).")

    # 3. Git init (idempotent)
    h("Initialisation Git locale")
    git_dir = DOWNLOADS / ".git"
    if git_dir.exists():
        warn(".git existant — reutilisation (idempotent).")
    else:
        run("git init -b main")
        ok("git init -b main")

    # Credential Manager Windows : desactiver pour eviter l'interference
    run('git config credential.helper ""')
    ok("Credential Manager desactive pour ce depot.")

    # 4. Détection de setup.py (avec versionning Windows)
    h("Detection de setup.py")
    setup_path = find_latest(DOWNLOADS, "setup", ".py")
    if setup_path is None:
        fail("setup.py introuvable dans Downloads. Telechargez-le d'abord.")

    if setup_path.name != "setup.py":
        # Renommer la version détectée en "setup.py" canonique
        canon = DOWNLOADS / "setup.py"
        warn(f"Version trouvee : {setup_path.name} — copie vers setup.py canonique.")
        shutil.copy2(setup_path, canon)
        setup_path = canon
    ok(f"setup.py : {setup_path}")

    # 5. Lancer setup.py
    h("Lancement de setup.py")
    info("Le script setup.py va prendre le relais pour :")
    info("  - Authentification GitHub (token)")
    info("  - Creation du depot distant")
    info("  - Commit initial et push")
    info("  - Activation de GitHub Pages")
    print()

    result = subprocess.run(
        [sys.executable, str(setup_path)],
        cwd=str(DOWNLOADS)
    )
    # setup.py gère lui-même sa pause finale — on ne ré-affiche pas d'erreur


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        pass
    except Exception:
        print(f"\n  {RED}{B}Erreur inattendue :{R}")
        traceback.print_exc()
    finally:
        # Pause uniquement si setup.py n'a pas pris le relais
        # (si setup.py a été lancé, c'est lui qui gère la pause finale)
        try:
            input("\n  init_git.py termine. Appuyez sur Entree pour fermer...")
        except Exception:
            pass
