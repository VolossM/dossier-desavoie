#!/usr/bin/env python3
"""
setup.py — Creation du depot GitHub Pages et push initial
Affaire DeSavoie / Chicherit — Frise d'investigation

Usage : double-clic (lance par init_git.py) ou python setup.py
Prérequis : Git installe, token GitHub Personnel (scope: repo)
"""

import os, sys, json, subprocess, urllib.request, urllib.error, getpass, traceback
from pathlib import Path

# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────
REPO_NAME        = "dossier-desavoie"
REPO_DESCRIPTION = "Affaire DeSavoie/Chicherit — Frise d'investigation (confidentiel)"
REPO_PRIVATE     = True   # False = public (Pages gratuit); True = necessite GitHub Pro pour Pages

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

# ─────────────────────────────────────────────
# Détection de Git (PATH restreint sous double-clic Windows)
# ─────────────────────────────────────────────
def find_git():
    candidates = [
        "git",
        r"C:\Program Files\Git\cmd\git.exe",
        r"C:\Program Files (x86)\Git\cmd\git.exe",
        str(Path.home() / "AppData" / "Local" / "Programs" / "Git" / "cmd" / "git.exe"),
        str(Path.home() / "scoop" / "apps" / "git" / "current" / "cmd" / "git.exe"),
    ]
    for candidate in candidates:
        r = subprocess.run(f'"{candidate}" --version',
                           shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode == 0:
            return candidate
    return None

GIT = None  # initialise dans main()

def run(cmd, cwd=None, check=True):
    full_cmd = cmd.replace("git ", f'"{GIT}" ', 1) if cmd.startswith("git ") else cmd
    proc = subprocess.Popen(
        full_cmd, shell=True, cwd=cwd or SCRIPT_DIR,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    raw_out, raw_err = proc.communicate()
    r_stdout = raw_out.decode("utf-8", errors="replace")
    r_stderr = raw_err.decode("utf-8", errors="replace")

    class Result:
        def __init__(self, rc, out, err):
            self.returncode = rc; self.stdout = out; self.stderr = err

    r = Result(proc.returncode, r_stdout, r_stderr)
    if check and r.returncode != 0:
        print(f"  stdout : {r.stdout.strip()}")
        print(f"  stderr : {r.stderr.strip()}")
        fail(f"Commande echouee : {cmd}")
    return r

def api(method, path, data=None, token=None):
    url = f"https://api.github.com{path}"
    hdrs = {"Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "dossier-desavoie-setup"}
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read()), resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:    msg = json.loads(raw).get("message", raw)
        except: msg = raw
        return {"error": msg}, e.code

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ─────────────────────────────────────────────
# Corps principal
# ─────────────────────────────────────────────
def main():
    global GIT

    # 1. Prérequis
    h("Verification des prerequis")
    if sys.version_info < (3, 8):
        fail("Python 3.8+ requis.")
    ok(f"Python {sys.version_info.major}.{sys.version_info.minor}")

    GIT = find_git()
    if GIT is None:
        fail("Git non trouve. Installez Git : https://git-scm.com/downloads")
    git_version = subprocess.run(f'"{GIT}" --version',
                                 shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
    ok(f"{git_version}  (chemin : {GIT})")

    # 2. Token GitHub
    h("Token GitHub Personnel")
    info("Creez un token sur : https://github.com/settings/tokens/new")
    info("Scope requis : repo (acces complet aux depots prives)")
    token = os.environ.get("GITHUB_TOKEN") or getpass.getpass("  Token (masque) : ").strip()
    if not token:
        fail("Token vide.")
    resp, status = api("GET", "/user", token=token)
    if status != 200:
        fail(f"Token invalide ou expire : {resp.get('error','')}")
    username = resp["login"]
    ok(f"Authentifie : {username}")

    # 3. Depot GitHub
    h(f"Depot GitHub : {username}/{REPO_NAME}")
    existing, s = api("GET", f"/repos/{username}/{REPO_NAME}", token=token)
    if s == 200:
        warn(f"Depot {username}/{REPO_NAME} existant — reutilisation.")
        repo_url  = existing["html_url"]
        clone_url = existing["clone_url"]
    else:
        resp, s = api("POST", "/user/repos", token=token, data={
            "name": REPO_NAME, "description": REPO_DESCRIPTION,
            "private": REPO_PRIVATE, "auto_init": False,
            "has_issues": False, "has_wiki": False,
        })
        if s not in (200, 201):
            fail(f"Creation depot echouee ({s}) : {resp.get('error','')}")
        repo_url  = resp["html_url"]
        clone_url = resp["clone_url"]
        ok(f"Depot cree : {repo_url}")

    auth_url = clone_url.replace("https://", f"https://{username}:{token}@")

    # 4. Depot Git local
    h("Depot Git local")
    if os.path.exists(os.path.join(SCRIPT_DIR, ".git")):
        warn(".git existant — reutilisation.")
    else:
        run("git init -b main")
        ok("git init")

    remotes = run("git remote -v", check=False).stdout
    if "origin" in remotes:
        run(f"git remote set-url origin {auth_url}")
        ok("Remote origin mis a jour.")
    else:
        run(f"git remote add origin {auth_url}")
        ok("Remote origin ajoute.")

    run('git config credential.helper ""')
    ok("Credential Manager desactive.")

    if not run("git config user.email", check=False).stdout.strip():
        run('git config user.email "deploy@dossier-desavoie"')
        run('git config user.name "Dossier DeSavoie"')
        ok("git config user defini.")

    # 5. .gitignore
    gi = os.path.join(SCRIPT_DIR, ".gitignore")
    if not os.path.exists(gi):
        with open(gi, "w") as f:
            f.write("__pycache__/\n*.pyc\n.DS_Store\nThumbs.db\n.env\n.deploy_config.json\n")
        ok(".gitignore cree.")
    elif ".deploy_config.json" not in open(gi).read():
        with open(gi, "a") as f:
            f.write(".deploy_config.json\n")

    # 6. Nettoyage : retirer .deploy_config.json du suivi Git s'il y est
    #    (il peut y avoir été ajouté par une exécution précédente avant que
    #    le .gitignore soit en place — sa présence déclenche le Push Protection
    #    de GitHub car il contenait autrefois le token en clair)
    h("Nettoyage secrets — historique Git")
    tracked = run("git ls-files .deploy_config.json", check=False).stdout.strip()
    if tracked:
        run("git rm --cached .deploy_config.json")
        warn(".deploy_config.json retire du suivi Git (ne sera plus versionne).")
        # Vérifier si le commit devient vide après le retrait du fichier.
        # Si le commit ne contenait que .deploy_config.json, l'amend produirait
        # un commit vide — Git le refuse. On supprime alors le commit entièrement.
        status = run("git status --porcelain", check=False).stdout.strip()
        staged = [l for l in status.splitlines() if not l.startswith("?")]
        if staged:
            run('git commit --amend --no-edit')
            ok("Historique reecrit (amend) — secret efface du dernier commit.")
        else:
            run('git reset HEAD^')
            ok("Commit vide supprime (git reset HEAD^) — secret efface de l'historique.")
    else:
        ok(".deploy_config.json absent du suivi Git — aucun nettoyage necessaire.")

    # 7. Commit initial + push
    h("Commit et push")

    # Supprimer index.lock si présent (vestige d'un processus Git interrompu)
    lock = os.path.join(SCRIPT_DIR, ".git", "index.lock")
    if os.path.exists(lock):
        os.remove(lock)
        warn("index.lock supprime (verrou Git residuel).")

    run("git add -A")
    if run("git status --porcelain", check=False).stdout.strip():
        run('git commit -m "Initial commit — dossier DeSavoie/Chicherit"')
        ok("Commit cree.")
    else:
        ok("Rien a committer.")

    pr = run("git push -u origin main", check=False)
    if pr.returncode != 0:
        # Force push nécessaire si l'historique a été réécrit par amend
        pr = run("git push -u origin main --force", check=False)
        if pr.returncode != 0:
            print(f"\n  {RED}Erreur push :{R}")
            print(f"  stdout : {pr.stdout.strip()}")
            print(f"  stderr : {pr.stderr.strip()}")
            fail("Push echoue. Verifiez ci-dessus.")
    ok("Push effectue.")

    # 7. GitHub Pages
    h("GitHub Pages")
    resp, s = api("POST", f"/repos/{username}/{REPO_NAME}/pages", token=token,
                  data={"source": {"branch": "main", "path": "/"}})
    if s in (200, 201):
        pages_url = resp.get("html_url", f"https://{username}.github.io/{REPO_NAME}/")
        ok(f"Pages active : {pages_url}")
        info("Premiere publication : 1-3 minutes.")
    elif s == 409:
        resp2, _ = api("GET", f"/repos/{username}/{REPO_NAME}/pages", token=token)
        pages_url = resp2.get("html_url", f"https://{username}.github.io/{REPO_NAME}/")
        ok(f"Pages deja actif : {pages_url}")
    else:
        pages_url = f"https://{username}.github.io/{REPO_NAME}/"
        warn(f"Pages non active ({s}) : {resp.get('error','')}")
        warn("Sur compte gratuit, Pages exige un depot public (REPO_PRIVATE = False).")
        warn("Alternative : python deploy.py --local")

    # 8. Config locale
    cfg = os.path.join(SCRIPT_DIR, ".deploy_config.json")
    with open(cfg, "w") as f:
        json.dump({"username": username, "repo": REPO_NAME,
                   "repo_url": repo_url, "pages_url": pages_url}, f, indent=2)
    info(".deploy_config.json sauvegarde (exclu du depot).")

    # 9. Résumé
    print(f"""
{B}{'─'*54}
  Installation terminee
{'─'*54}{R}
  Depot  : {CYN}{repo_url}{R}
  Pages  : {CYN}{pages_url}{R}

  Prochain deploiement :
    {B}python deploy.py{R}

  Serveur local (sans GitHub) :
    {B}python deploy.py --local{R}
{'─'*54}
""")

# ─────────────────────────────────────────────
# Point d'entrée
# ─────────────────────────────────────────────
if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        pass
    except Exception:
        print(f"\n  {RED}{B}Erreur inattendue :{R}")
        traceback.print_exc()
    finally:
        try:
            input("\n  Appuyez sur Entree pour fermer...")
        except Exception:
            pass
