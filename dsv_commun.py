#!/usr/bin/env python3
"""
dsv_commun.py — Socle commun des scripts du depot dossier-desavoie
Affaire DeSavoie / Chicherit

Regroupe ce que deploy.py, setup.py et init_git.py dupliquaient : affichage
console, detection de Git, execution de commandes, empreinte SHA-256, selection
de la meilleure version d'un fichier, garde anti-regression avant ecrasement,
capture de diagnostic, depot dans la boite aux lettres.

Ce module ne s'execute pas seul : il est charge par l'amorce `_amorce()` que
chaque script embarque (seul code duplique restant), ou par dsv.py.

Ordre de selection d'une version (fonction `cle_version`) :
  1. `__version__` declare dans le corps du fichier ;
  2. suffixe `_vN` dans le nom du fichier ;
  3. numero ` (N)` ajoute par Windows aux telechargements en double ;
  4. horodatage de modification.
Toute modification d'un module doit donc incrementer son `__version__`.
"""
__version__ = "1.0"

import os, sys, re, json, hashlib, shutil, subprocess, platform, traceback
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

MARQUE    = "dossier-desavoie"          # empreinte presente dans chaque script du depot
RACINE    = Path(__file__).resolve().parent
DOWNLOADS = Path.home() / "Downloads"
DSV_DIR   = RACINE / "_dsv"             # prefixe "_" : non publie par Jekyll (GitHub Pages)

# Contenu de reference du .gitignore (le depot local est le dossier Downloads :
# tout est exclu, sauf les fichiers du projet).
GITIGNORE = """\
# Downloads — exclure tout par defaut
*

# Reintegrer uniquement les fichiers du projet DeSavoie/Chicherit
!index.html
!setup.py
!deploy.py
!manifest.json
!init_git.py
!dsv.py
!dsv_commun.py
!dsv_bal.py
!dsv_audit_poste.py
!.gitignore
!.gitattributes
!data/
!data/*.jsonl
!_dsv/
!_dsv/bal/
!_dsv/bal/*.json
!_dsv/suivi/
!_dsv/suivi/*
!.claude/
!.claude/CLAUDE.md
!.claude/settings.json

# .deploy_config.json : JAMAIS versionne (peut contenir des identifiants).
# Reste local ; regenere par setup.py.
.deploy_config.json
"""

# Fichiers du projet autorises a la racine du depot (controle de deploy.py).
FICHIERS_RACINE = {
    "index.html", "manifest.json", "deploy.py", "setup.py", "init_git.py",
    "dsv.py", "dsv_commun.py", "dsv_bal.py", "dsv_audit_poste.py",
    ".gitignore", ".gitattributes", ".deploy_config.json", "README.md", "readme.md",
}

# ─────────────────────────────────────────────
# Console
# ─────────────────────────────────────────────
def _couleurs_actives():
    if not sys.stdout.isatty():
        return False            # journal redirige : pas de sequences d'echappement
    if os.name == "nt":
        os.system("")           # active le traitement ANSI de la console Windows
    return True

if _couleurs_actives():
    R="\033[0m"; B="\033[1m"; RED="\033[31m"; GRN="\033[32m"
    YLW="\033[33m"; CYN="\033[36m"; DIM="\033[2m"
else:
    R = B = RED = GRN = YLW = CYN = DIM = ""

# Console Windows en cp850/cp1252 : un caractere non representable ne doit jamais
# interrompre un script (il est remplace par « ? »).
for _flux in (sys.stdout, sys.stderr):
    try:
        _flux.reconfigure(errors="replace")
    except Exception:
        pass

def h(t):    print(f"\n{B}{CYN}> {t}{R}")
def ok(t):   print(f"  {GRN}ok{R}  {t}")
def warn(t): print(f"  {YLW}!{R}   {t}")
def info(t): print(f"  {DIM}{t}{R}")
def fail(t): print(f"\n  {RED}{B}ERREUR :{R} {t}"); sys.exit(1)

def horodatage():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def pause_finale(texte="Appuyez sur Entree pour fermer..."):
    """Pause de fin (fenetre ouverte par double-clic). Ignoree si le script est
    pilote (PIPELINE_NO_PAUSE=1) ou si l'entree standard n'est pas un terminal."""
    if os.environ.get("PIPELINE_NO_PAUSE") or not sys.stdin.isatty():
        return
    try:
        input(f"\n  {texte}")
    except Exception:
        pass

# ─────────────────────────────────────────────
# Diagnostic (capture en repli de chaque action ; une impression par type)
# ─────────────────────────────────────────────
_SECRETS = [
    (re.compile(r"(https?://)[^/\s:@]+:[^@\s]+@"), r"\1***@"),
    (re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_\w{20,})"), "***"),
    (re.compile(r"(Authorization:\s*\w+\s+)\S+", re.I), r"\1***"),
]
_DIAG_IMPRIMES = set()
DERNIER_DIAG = None

def masquer(texte):
    """Retire jetons et identifiants d'un texte destine a un journal ou a la BaL."""
    texte = str(texte)
    for motif, remplacement in _SECRETS:
        texte = motif.sub(remplacement, texte)
    return texte

def diagnostic(action, exc=None, **contexte):
    """Construit le diagnostic d'une action non nominale, l'imprime une seule fois
    par type d'action et le conserve dans DERNIER_DIAG. Retourne le dictionnaire."""
    global DERNIER_DIAG
    d = {
        "action": action,
        "horodatage": horodatage(),
        "plateforme": platform.platform(),
        "python": platform.python_version(),
        "repertoire_courant": os.getcwd(),
        "racine": str(RACINE),
        "git": _GIT or "(non resolu)",
    }
    for cle, valeur in contexte.items():
        d[cle] = masquer(valeur)[-2000:] if isinstance(valeur, (str, list, tuple)) else valeur
    if exc is not None:
        d["exception"] = masquer("".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)))[-4000:]
    DERNIER_DIAG = d
    if action not in _DIAG_IMPRIMES:
        _DIAG_IMPRIMES.add(action)
        print(f"  {DIM}diagnostic [{action}] : "
              f"{json.dumps(d, ensure_ascii=False, indent=2)}{R}", flush=True)
    return d

# ─────────────────────────────────────────────
# Git et execution de commandes
# ─────────────────────────────────────────────
_GIT = None

def find_git():
    """Cherche git dans le PATH puis aux emplacements standard de Windows (un
    double-clic lance Python avec un PATH restreint qui peut exclure Git)."""
    candidats = [
        "git",
        r"C:\Program Files\Git\cmd\git.exe",
        r"C:\Program Files (x86)\Git\cmd\git.exe",
        str(Path.home() / "AppData" / "Local" / "Programs" / "Git" / "cmd" / "git.exe"),
        str(Path.home() / "scoop" / "apps" / "git" / "current" / "cmd" / "git.exe"),
    ]
    for c in candidats:
        try:
            r = subprocess.run([c, "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError:
            continue
        if r.returncode == 0:
            return c
    return None

def git():
    """Chemin de git, resolu une fois ; "git" en dernier recours."""
    global _GIT
    if _GIT is None:
        _GIT = find_git() or "git"
    return _GIT

class Resultat:
    def __init__(self, rc, out, err):
        self.returncode = rc; self.stdout = out; self.stderr = err

def run(cmd, cwd=None, check=True, env=None):
    """Execute une commande. Liste d'arguments -> sans shell (aucune interpolation
    de valeur variable dans une ligne de shell) ; chaine -> shell, reserve aux
    commandes statiques. Un "git" initial est remplace par le chemin resolu."""
    if isinstance(cmd, (list, tuple)):
        argv = [git() if (i == 0 and a == "git") else str(a) for i, a in enumerate(cmd)]
        shell = False
    else:
        argv = cmd.replace("git ", f'"{git()}" ', 1) if cmd.startswith("git ") else cmd
        shell = True
    try:
        proc = subprocess.Popen(argv, shell=shell, cwd=str(cwd or RACINE),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        brut_out, brut_err = proc.communicate()
        r = Resultat(proc.returncode,
                     brut_out.decode("utf-8", errors="replace"),
                     brut_err.decode("utf-8", errors="replace"))
    except OSError as exc:
        r = Resultat(127, "", f"{type(exc).__name__}: {exc}")
    if check and r.returncode != 0:
        lisible = " ".join(cmd) if isinstance(cmd, (list, tuple)) else cmd
        diagnostic("commande", commande=lisible, code=r.returncode,
                   stdout=r.stdout.strip(), stderr=r.stderr.strip(),
                   repertoire=str(cwd or RACINE))
        fail(f"Commande echouee : {masquer(lisible)}")
    return r

# ─────────────────────────────────────────────
# Empreintes et comptage d'entites
# ─────────────────────────────────────────────
def sha256(path):
    h_ = hashlib.sha256()
    with open(path, "rb") as f:
        for bloc in iter(lambda: f.read(65536), b""):
            h_.update(bloc)
    return h_.hexdigest()

def contient(path, empreinte, octets=8192):
    """Vrai si le debut du fichier contient l'empreinte (None = toujours vrai)."""
    if empreinte is None:
        return True
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return empreinte in f.read(octets)
    except OSError:
        return False

def compter_entites(path):
    """Compte les entites FtM valides d'un .ftm.jsonl : lignes JSON decodant en
    objet portant un `id` non vide. Retourne (n_valides, n_invalides) ; ne leve
    jamais (renvoie (0, 0) si le fichier est illisible)."""
    n_ok = n_bad = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for ligne in f:
                ligne = ligne.strip()
                if not ligne:
                    continue
                try:
                    obj = json.loads(ligne)
                except (json.JSONDecodeError, ValueError):
                    n_bad += 1
                    continue
                if isinstance(obj, dict) and obj.get("id"):
                    n_ok += 1
                else:
                    n_bad += 1
    except OSError:
        return 0, 0
    return n_ok, n_bad

# ─────────────────────────────────────────────
# Selection de version
# ─────────────────────────────────────────────
_RE_VERSION_CORPS = re.compile(r"^__version__\s*=\s*[\"']([\d.]+)[\"']", re.M)

def _tuple_version(texte):
    return tuple(int(x) for x in re.findall(r"\d+", texte or ""))

def version_corps(path):
    """`__version__` declare dans les 8 premiers Ko du fichier, en tuple ; () sinon."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            m = _RE_VERSION_CORPS.search(f.read(8192))
    except OSError:
        return ()
    return _tuple_version(m.group(1)) if m else ()

def cle_version(path, v_nom=(), n_windows=0):
    """Cle de tri : corps du fichier > `_vN` du nom > ` (N)` Windows > horodatage."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (version_corps(path), v_nom, n_windows, mtime)

def candidats(nom, dossiers, empreinte=None):
    """Toutes les versions de `nom` presentes dans `dossiers` : nom canonique,
    variantes `_vN` et copies Windows ` (N)`. Retourne [(chemin, cle_version)],
    limite aux fichiers portant l'empreinte."""
    p = Path(nom)
    motif = re.compile(re.escape(p.stem) + r"(?:_v(\d+(?:[._]\d+)*))?(?: \((\d+)\))?"
                       + re.escape(p.suffix))
    vus, trouves = set(), []
    for dossier in dossiers:
        try:
            entrees = list(Path(dossier).iterdir())
        except OSError:
            continue
        for entree in entrees:
            m = motif.fullmatch(entree.name)
            if not m or not entree.is_file():
                continue
            reel = entree.resolve()
            if reel in vus or not contient(entree, empreinte):
                continue
            vus.add(reel)
            trouves.append((entree, cle_version(entree, _tuple_version(m.group(1)),
                                                int(m.group(2) or 0))))
    return trouves

def meilleure_version(nom, dossiers, empreinte=None):
    """Meilleure version de `nom` (voir cle_version). Retourne (chemin, cle) ou (None, None)."""
    c = candidats(nom, dossiers, empreinte)
    return max(c, key=lambda x: x[1]) if c else (None, None)

def charger_module(nom_fichier, dossiers=None, empreinte=MARQUE):
    """Charge la meilleure version d'un module du depot (ex. "dsv_bal.py") sous son
    nom canonique. Retourne le module, ou None s'il est introuvable."""
    nom_module = Path(nom_fichier).stem
    if nom_module in sys.modules:
        return sys.modules[nom_module]
    chemin, _ = meilleure_version(nom_fichier, dossiers or [RACINE, DOWNLOADS], empreinte)
    if chemin is None:
        return None
    spec = importlib.util.spec_from_file_location(nom_module, chemin)
    module = importlib.util.module_from_spec(spec)
    sys.modules[nom_module] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(nom_module, None)
        raise
    return module

# ─────────────────────────────────────────────
# Garde anti-regression avant ecrasement
# ─────────────────────────────────────────────
SEUIL_REGRESSION = 0.20     # chute relative au-dela de laquelle on refuse / avertit

def garde_ecrasement(source, cible, autoriser_reduction=False):
    """Verifie qu'ecraser `cible` par `source` ne corrompt ni ne regresse les donnees.
    Retourne (accepte, motif). Refus : fichier vide ; .jsonl sans entite valide ou
    perdant plus de SEUIL_REGRESSION de ses entites ; .json invalide ; .py qui ne
    compile pas. Une simple baisse de taille (.html, .py) donne un avertissement."""
    source, cible = Path(source), Path(cible)
    try:
        taille = source.stat().st_size
    except OSError as exc:
        return False, f"source illisible ({exc})"
    if taille == 0:
        return False, "fichier source vide (0 o)"
    suffixe = source.suffix.lower()
    if suffixe == ".jsonl":
        n_new, n_bad = compter_entites(source)
        if n_new == 0:
            return False, f"aucune entite valide ({taille:,} o, {n_bad} ligne(s) invalide(s))"
        if cible.is_file():
            n_old, _ = compter_entites(cible)
            if n_old and (n_old - n_new) / n_old > SEUIL_REGRESSION and not autoriser_reduction:
                return False, (f"chute d'entites de {(n_old - n_new) / n_old:.0%} "
                               f"({n_old:,} -> {n_new:,})")
        return True, (f"{n_bad} ligne(s) JSONL invalide(s)" if n_bad else "")
    if suffixe == ".json":
        try:
            json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return False, f"JSON invalide ({exc})"
        return True, ""
    if suffixe == ".py":
        try:
            compile(source.read_text(encoding="utf-8"), str(source), "exec")
        except (OSError, SyntaxError, ValueError) as exc:
            return False, f"script Python invalide ({exc})"
    if cible.is_file():
        ancienne = cible.stat().st_size
        if ancienne and (ancienne - taille) / ancienne > SEUIL_REGRESSION:
            return True, (f"taille en baisse de {(ancienne - taille) / ancienne:.0%} "
                          f"({ancienne:,} o -> {taille:,} o) — verifiez le contenu")
    return True, ""

def copier_garde(source, cible, autoriser_reduction=False):
    """Copie `source` sur `cible` si la garde l'accepte. Retourne (copie, motif)."""
    accepte, motif = garde_ecrasement(source, cible, autoriser_reduction)
    if accepte:
        Path(cible).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, cible)
    return accepte, motif

# ─────────────────────────────────────────────
# Boite aux lettres (depot ; ne leve jamais)
# ─────────────────────────────────────────────
def signaler(destinataire, sujet, corps="", urgence="info", cle=None, diag=None):
    """Depose un message dans la boite aux lettres (_dsv/bal) via dsv_bal.py.
    N'interrompt jamais le script appelant : un echec de depot est un avertissement."""
    try:
        bal = charger_module("dsv_bal.py")
        if bal is None:
            warn(f"Boite aux lettres indisponible (dsv_bal.py introuvable) — "
                 f"message non depose : {sujet}")
            return None
        expediteur = "script:" + Path(sys.argv[0] or "inconnu").name
        chemin = bal.deposer(destinataire, sujet, corps, urgence=urgence,
                             expediteur=expediteur, cle=cle, diagnostic=diag)
        info(f"Boite aux lettres : message depose pour « {destinataire} » ({chemin.name}).")
        return chemin
    except Exception as exc:
        warn(f"Depot en boite aux lettres impossible ({type(exc).__name__}: {exc}).")
        return None
