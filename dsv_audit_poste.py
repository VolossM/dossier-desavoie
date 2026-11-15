#!/usr/bin/env python3
"""
dsv_audit_poste.py — Audit des ressources du poste de travail (Windows ou Linux)
Depot : dossier-desavoie

Mesure ce qui peut ralentir le travail : memoire, processeur, disque, processus
les plus gourmands, programmes lances au demarrage, encombrement du dossier
Downloads (qui heberge le depot Git) et temps de reponse de Git.

Le rapport complet (noms de processus et de fichiers) est ecrit a cote du script
sous un nom horodate — jamais ecrase, jamais versionne (exclu par .gitignore).
Seuls les constats chiffres, sans nom de fichier, sont deposes dans la boite aux
lettres pour la session Claude suivante.

Usage :
  python dsv_audit_poste.py                 menu
  python dsv_audit_poste.py --executer      audit complet
  python dsv_audit_poste.py --executer --sans-bal --dossier CHEMIN

Bibliotheque standard uniquement : aucune dependance a installer.
"""
__version__ = "1.0"

import os, sys, re, json, time, shutil, argparse, platform, subprocess, ctypes
from collections import Counter
from datetime import datetime
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
from dsv_commun import B, R, YLW, DIM, h, ok, warn, info

WINDOWS = os.name == "nt"
GO = 1024 ** 3

# Seuils des constats : reperes heuristiques choisis pour ce script, non des normes.
SEUIL_RAM_LIBRE    = 0.15     # part de memoire disponible en dessous de laquelle on alerte
SEUIL_DISQUE_LIBRE = 0.10     # part de disque libre en dessous de laquelle on alerte
SEUIL_DOUBLONS_GO  = 1.0      # volume de copies Windows " (N)" au-dela duquel on alerte
SEUIL_GIT_STATUS_S = 5.0      # duree de `git status` au-dela de laquelle on alerte
SEUIL_ANCIEN_JOURS = 90
LIMITE_ENTREES     = 300_000  # plafond du parcours de Downloads

def _commande(argv, delai=60):
    r = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=delai)
    if r.returncode != 0:
        raise RuntimeError(f"code {r.returncode} : {r.stderr.decode('utf-8', 'replace')[:500]}")
    for enc in ("utf-8", "cp850", "cp1252"):
        try:
            return r.stdout.decode(enc)
        except UnicodeDecodeError:
            continue
    return r.stdout.decode("utf-8", "replace")

def _powershell(script):
    return _commande(["powershell", "-NoProfile", "-NonInteractive", "-Command", script])

# ─────────────────────────────────────────────
# Sondes (chacune retourne un dictionnaire ; les echecs sont captures par sonder())
# ─────────────────────────────────────────────
def sonde_systeme(_):
    return {"systeme": platform.platform(), "machine": platform.machine(),
            "python": platform.python_version(), "processeurs_logiques": os.cpu_count()}

def sonde_memoire(_):
    if WINDOWS:
        class Etat(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong)] + [
                (n, ctypes.c_ulonglong) for n in (
                    "ullTotalPhys", "ullAvailPhys", "ullTotalPageFile", "ullAvailPageFile",
                    "ullTotalVirtual", "ullAvailVirtual", "ullAvailExtendedVirtual")]
        e = Etat(); e.dwLength = ctypes.sizeof(Etat)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(e)):
            raise OSError("GlobalMemoryStatusEx a echoue")
        total, dispo = e.ullTotalPhys, e.ullAvailPhys
    else:
        champs = dict(l.split(":", 1) for l in Path("/proc/meminfo").read_text().splitlines())
        total = int(champs["MemTotal"].split()[0]) * 1024
        dispo = int(champs["MemAvailable"].split()[0]) * 1024
    return {"total_go": round(total / GO, 2), "disponible_go": round(dispo / GO, 2),
            "part_disponible": round(dispo / total, 3)}

def sonde_processeur(_):
    if WINDOWS:
        t = _powershell("(Get-CimInstance Win32_Processor | Measure-Object LoadPercentage -Average).Average")
        return {"charge_pct": float(t.strip().replace(",", "."))}
    c1, c5, c15 = os.getloadavg()
    return {"charge_moyenne_1_5_15_min": [round(c1, 2), round(c5, 2), round(c15, 2)]}

def sonde_disque(dossier):
    u = shutil.disk_usage(dossier)
    return {"volume_de": str(dossier), "total_go": round(u.total / GO, 1),
            "libre_go": round(u.free / GO, 1), "part_libre": round(u.free / u.total, 3)}

def sonde_processus(_):
    """Memoire residente cumulee par nom de processus (15 premiers)."""
    cumul, nombre = Counter(), Counter()
    if WINDOWS:
        import csv, io
        for ligne in csv.reader(io.StringIO(_commande(["tasklist", "/fo", "csv", "/nh"]))):
            if len(ligne) >= 5:
                ko = int(re.sub(r"\D", "", ligne[4]) or 0)
                cumul[ligne[0]] += ko; nombre[ligne[0]] += 1
    else:
        for ligne in _commande(["ps", "-eo", "rss=,comm="]).splitlines():
            rss, _, nom = ligne.strip().partition(" ")
            if rss.isdigit():
                cumul[nom.strip()] += int(rss); nombre[nom.strip()] += 1
    return {"total_processus": sum(nombre.values()),
            "plus_gourmands": [{"nom": n, "instances": nombre[n], "memoire_mo": round(ko / 1024)}
                               for n, ko in cumul.most_common(15)]}

def sonde_demarrage(_):
    if not WINDOWS:
        return {"non_applicable": "sonde reservee a Windows"}
    brut = _powershell("Get-CimInstance Win32_StartupCommand | Select-Object Name,Command,Location "
                       "| ConvertTo-Json -Compress")
    liste = json.loads(brut) if brut.strip() else []
    liste = liste if isinstance(liste, list) else [liste]
    return {"nombre": len(liste),
            "programmes": [{"nom": x.get("Name"), "commande": x.get("Command"),
                            "origine": x.get("Location")} for x in liste]}

def sonde_downloads(dossier):
    """Encombrement du dossier : volume, extensions, copies Windows " (N)", fichiers
    anciens, plus gros fichiers. Parcours recursif plafonne a LIMITE_ENTREES."""
    re_copie = re.compile(r" \(\d+\)(\.[^.]+)*$")
    limite_age = time.time() - SEUIL_ANCIEN_JOURS * 86400
    n = total = n_copies = t_copies = n_anciens = t_anciens = n_racine = 0
    par_ext, gros, tronque = Counter(), [], False
    pile = [(Path(dossier), 0)]
    while pile:
        courant, profondeur = pile.pop()
        try:
            entrees = list(os.scandir(courant))
        except OSError:
            continue
        for e in entrees:
            try:
                if e.is_dir(follow_symlinks=False):
                    if e.name != ".git":
                        pile.append((Path(e.path), profondeur + 1))
                    continue
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            n += 1; total += st.st_size
            n_racine += profondeur == 0
            par_ext[Path(e.name).suffix.lower() or "(sans)"] += st.st_size
            if re_copie.search(e.name):
                n_copies += 1; t_copies += st.st_size
            if st.st_mtime < limite_age:
                n_anciens += 1; t_anciens += st.st_size
            gros.append((st.st_size, e.path))
            if len(gros) > 400:
                gros = sorted(gros, reverse=True)[:15]
            if n >= LIMITE_ENTREES:
                tronque, pile = True, []
                break
    return {
        "dossier": str(dossier), "parcours_tronque": tronque,
        "fichiers": n, "fichiers_a_la_racine": n_racine, "volume_go": round(total / GO, 2),
        "copies_windows": {"nombre": n_copies, "volume_go": round(t_copies / GO, 2)},
        f"plus_de_{SEUIL_ANCIEN_JOURS}_jours": {"nombre": n_anciens, "volume_go": round(t_anciens / GO, 2)},
        "volume_par_extension_go": {x: round(v / GO, 2) for x, v in par_ext.most_common(10)},
        "plus_gros_fichiers": [{"chemin": c, "mo": round(t / 1024 ** 2)}
                               for t, c in sorted(gros, reverse=True)[:15]],
    }

def sonde_git(dossier):
    if not (Path(dossier) / ".git").exists():
        return {"non_applicable": f"pas de depot Git dans {dossier}"}
    debut = time.time()
    statut = commun.run("git status --porcelain", cwd=dossier, check=False)
    duree = time.time() - debut
    objets = commun.run("git count-objects -vH", cwd=dossier, check=False).stdout
    return {"git_status_secondes": round(duree, 2), "code": statut.returncode,
            "fichiers_modifies": len(statut.stdout.splitlines()),
            "objets": dict(l.split(": ", 1) for l in objets.splitlines() if ": " in l)}

SONDES = [("systeme", sonde_systeme), ("memoire", sonde_memoire), ("processeur", sonde_processeur),
          ("disque", sonde_disque), ("processus", sonde_processus), ("demarrage", sonde_demarrage),
          ("downloads", sonde_downloads), ("git", sonde_git)]

def sonder(dossier):
    rapport = {"genere": commun.horodatage(), "version_script": __version__}
    for nom, sonde in SONDES:
        try:
            rapport[nom] = sonde(dossier)
        except Exception as exc:            # une sonde en echec n'arrete pas l'audit
            rapport[nom] = {"echec": commun.diagnostic(f"sonde-{nom}", exc=exc, dossier=str(dossier))}
            warn(f"Sonde « {nom} » en echec — diagnostic consigne dans le rapport.")
    return rapport

def constats(r):
    """Constats chiffres, sans nom de fichier ni de processus (publiables en BaL)."""
    c = []
    m, d, dl, g = r.get("memoire", {}), r.get("disque", {}), r.get("downloads", {}), r.get("git", {})
    if "part_disponible" in m:
        c.append(("ALERTE" if m["part_disponible"] < SEUIL_RAM_LIBRE else "ok")
                 + f" memoire : {m['disponible_go']} Go disponibles sur {m['total_go']} Go "
                   f"({m['part_disponible']:.0%})")
    if "part_libre" in d:
        c.append(("ALERTE" if d["part_libre"] < SEUIL_DISQUE_LIBRE else "ok")
                 + f" disque : {d['libre_go']} Go libres sur {d['total_go']} Go ({d['part_libre']:.0%})")
    if "copies_windows" in dl:
        cw = dl["copies_windows"]
        c.append(("ALERTE" if cw["volume_go"] > SEUIL_DOUBLONS_GO else "ok")
                 + f" Downloads : {dl['fichiers']} fichiers, {dl['volume_go']} Go, dont "
                   f"{cw['nombre']} copies Windows « (N) » pour {cw['volume_go']} Go"
                 + (" (parcours tronque)" if dl.get("parcours_tronque") else ""))
    if "git_status_secondes" in g:
        c.append(("ALERTE" if g["git_status_secondes"] > SEUIL_GIT_STATUS_S else "ok")
                 + f" git status : {g['git_status_secondes']} s")
    echecs = [n for n, _ in SONDES if isinstance(r.get(n), dict) and "echec" in r[n]]
    if echecs:
        c.append("ALERTE sondes en echec : " + ", ".join(echecs))
    return c

def executer(dossier=None, sans_bal=False):
    dossier = Path(dossier) if dossier else commun.RACINE
    h(f"Audit du poste — {dossier}")
    rapport = sonder(dossier)
    rapport["constats"] = constats(rapport)
    sortie = commun.RACINE / f"dsv_audit_poste_{datetime.now():%Y%m%d-%H%M%S}.json"
    sortie.write_text(json.dumps(rapport, ensure_ascii=False, indent=2), encoding="utf-8")
    for ligne in rapport["constats"]:
        (warn if ligne.startswith("ALERTE") else ok)(ligne.split(" ", 1)[1])
    print(f"\n  Rapport complet : {B}{sortie}{R}")
    info("Fichier local, non versionne : a joindre a une conversation Claude pour un diagnostic.")
    if not sans_bal:
        commun.signaler("claude", "audit du poste disponible",
                        "\n".join(rapport["constats"] + [f"Rapport complet local : {sortie.name}"]),
                        urgence="action" if any(l.startswith("ALERTE") for l in rapport["constats"]) else "info",
                        cle="audit-poste")
    return rapport

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        h("Audit du poste — dossier DeSavoie")
        print(f"  1. Audit complet de {commun.RACINE}\n  2. Audit sans depot en boite aux lettres\n  0. Quitter")
        choix = input("  Choix : ").strip()
        if choix not in ("1", "2"):
            return 0
        argv = ["--executer"] + (["--sans-bal"] if choix == "2" else [])
    p = argparse.ArgumentParser(description="Audit des ressources du poste")
    p.add_argument("--executer", action="store_true", required=True)
    p.add_argument("--sans-bal", action="store_true")
    p.add_argument("--dossier", help="Dossier a mesurer (defaut : dossier du script)")
    args = p.parse_args(argv)
    executer(args.dossier, args.sans_bal)
    return 0

if __name__ == "__main__":
    try:
        code = main()
    except Exception as exc:
        commun.diagnostic("audit-exception", exc=exc)
        code = 1
    commun.pause_finale()
    sys.exit(code)
