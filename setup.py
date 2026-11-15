#!/usr/bin/env python3
"""
setup.py — Creation du depot GitHub Pages et push initial
Affaire DeSavoie / Chicherit — Frise d'investigation

Usage : double-clic (lance par init_git.py) ou python setup.py
        python setup.py --forcer-push   ecrase la branche main distante si le push est refuse
Prérequis : Git installe, token GitHub Personnel (scope: repo)
"""
__version__ = "2.0"

import os, sys, re, json, base64, urllib.request, urllib.error, getpass, traceback
from pathlib import Path

def _amorce(nom="dsv_commun"):
    """Charge le socle commun (seul code duplique entre scripts). Ordre : `__version__`
    du corps du fichier, puis numero Windows " (N)", puis horodatage."""
    import importlib.util
    if nom in sys.modules:
        return sys.modules[nom]
    vus, c = set(), []
    for d in (Path(__file__).resolve().parent, Path.home() / "Downloads"):
        for p in (d.glob(nom + "*.py") if d.is_dir() else ()):
            m = re.fullmatch(re.escape(nom) + r"(?: \((\d+)\))?\.py", p.name)
            if not m or p.resolve() in vus:
                continue
            vus.add(p.resolve())
            tete = p.read_text(encoding="utf-8", errors="replace")[:8192]
            v = re.search(r"^__version__\s*=\s*[\"']([\d.]+)[\"']", tete, re.M)
            c.append((tuple(int(x) for x in v.group(1).split(".") if x) if v else (),
                      int(m.group(1) or 0), p.stat().st_mtime, p))
    if not c:
        sys.exit(f"ERREUR : {nom}.py introuvable a cote de {Path(__file__).name} "
                 f"ni dans {Path.home() / 'Downloads'}.")
    spec = importlib.util.spec_from_file_location(nom, max(c)[3])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nom] = mod
    spec.loader.exec_module(mod)
    return mod


commun = _amorce()
from dsv_commun import R, B, RED, GRN, YLW, CYN, DIM, h, ok, warn, info, fail

# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────
REPO_NAME        = "dossier-desavoie"
REPO_DESCRIPTION = "Affaire DeSavoie/Chicherit — Frise d'investigation (confidentiel)"
REPO_PRIVATE     = True   # False = public (Pages gratuit); True = necessite GitHub Pro pour Pages

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def run(cmd, cwd=None, check=True, env=None):
    """Commande executee dans le dossier de setup.py (voir dsv_commun.run)."""
    return commun.run(cmd, cwd=cwd or SCRIPT_DIR, check=check, env=env)

def git_auth_env(username, token):
    """Environnement transmettant le token a git via GIT_CONFIG_* (en-tete HTTP
    ephemere). Le token n'est ni ecrit dans .git/config, ni passe en argument."""
    basic = base64.b64encode(f"{username}:{token}".encode()).decode()
    return {
        **os.environ,
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.extraheader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
    }

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
        raw = e.read().decode(errors="replace")
        try:    msg = json.loads(raw).get("message", raw)
        except ValueError: msg = raw
        commun.diagnostic("api-github", methode=method, endpoint=path, statut=e.code,
                          reponse=raw, type_erreur="HTTPError")
        return {"error": msg}, e.code
    except (urllib.error.URLError, OSError, ValueError) as e:
        # Reseau coupe, proxy, DNS, reponse non JSON : meme forme de retour (statut 0).
        commun.diagnostic("api-github", exc=e, methode=method, endpoint=path,
                          type_erreur=type(e).__name__)
        return {"error": f"{type(e).__name__}: {e}"}, 0

# ─────────────────────────────────────────────
# Corps principal
# ─────────────────────────────────────────────
def main():
    # 1. Prérequis
    h("Verification des prerequis")
    if sys.version_info < (3, 8):
        fail("Python 3.8+ requis.")
    ok(f"Python {sys.version_info.major}.{sys.version_info.minor}")

    if commun.find_git() is None:
        fail("Git non trouve. Installez Git : https://git-scm.com/downloads")
    ok(f"{run('git --version').stdout.strip()}  (chemin : {commun.git()})")

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

    # 4. Depot Git local
    h("Depot Git local")
    if os.path.exists(os.path.join(SCRIPT_DIR, ".git")):
        warn(".git existant — reutilisation.")
    else:
        run("git init -b main")
        ok("git init")

    # URL propre (sans token) : l'authentification se fait a la volee au push
    # via git_auth_env, jamais persistee dans .git/config.
    remotes = run("git remote -v", check=False).stdout
    if "origin" in remotes:
        run(["git", "remote", "set-url", "origin", clone_url])
        ok("Remote origin mis a jour (URL propre, sans token).")
    else:
        run(["git", "remote", "add", "origin", clone_url])
        ok("Remote origin ajoute (URL propre, sans token).")

    run('git config credential.helper ""')
    ok("Credential Manager desactive.")

    if not run("git config user.email", check=False).stdout.strip():
        run('git config user.email "deploy@dossier-desavoie"')
        run('git config user.name "Dossier DeSavoie"')
        ok("git config user defini.")

    # 5. .gitignore
    gi = os.path.join(SCRIPT_DIR, ".gitignore")
    if not os.path.exists(gi):
        with open(gi, "w", encoding="utf-8") as f:
            f.write(commun.GITIGNORE)
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
    historique_reecrit = False
    if tracked:
        historique_reecrit = True
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

    push_env = git_auth_env(username, token)
    pr = run(["git", "push", "-u", "origin", "main"], check=False, env=push_env)
    if pr.returncode != 0:
        # Le push force ecrase la branche main distante. Il n'est tente que dans les
        # deux cas ou c'est l'intention : l'historique vient d'etre reecrit ci-dessus
        # pour effacer un secret, ou l'utilisateur l'a demande (--forcer-push).
        # [2026-11-15] Auparavant inconditionnel : lance depuis un dossier sans
        # historique, setup.py remplacait alors tout le depot distant par un commit
        # initial, sans avertissement.
        if historique_reecrit or "--forcer-push" in sys.argv:
            warn("Push refuse — nouvelle tentative avec --force (historique reecrit ou --forcer-push).")
            pr = run(["git", "push", "-u", "origin", "main", "--force"], check=False, env=push_env)
        if pr.returncode != 0:
            commun.diagnostic("git-push", code=pr.returncode, stdout=pr.stdout.strip(),
                              stderr=pr.stderr.strip(),
                              historique=run("git log --oneline -5", check=False).stdout)
            fail("Push echoue : la branche main distante contient des commits absents d'ici. "
                 "Recuperez-les (git pull origin main) ; --forcer-push ECRASE le depot distant.")
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
    except Exception as exc:
        print(f"\n  {RED}{B}Erreur inattendue :{R}", flush=True)
        traceback.print_exc(file=sys.stdout)
        commun.diagnostic("setup-exception", exc=exc)
    finally:
        commun.pause_finale()
