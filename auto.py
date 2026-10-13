"""
Klausīšanās asistents: automātika Mac.

  klausies savienojums     parāda kodu, ko ielīmēt iPhone lietotnē (un nokopē to)
  klausies auto            vienreiz apstrādā jaunos rakstus mapē Ienakosie un jaunās piezīmes
  klausies auto-ieslegt    uzstāda fona darbu: ik pēc 10 minūtēm un uzreiz, kad mapē Ienakosie parādās fails
  klausies auto-izslegt    noņem fona darbu
  klausies statuss         parāda, vai fona darbs ir ieslēgts, un pedejas žurnāla rindas
"""
from __future__ import annotations

import fcntl
import os
import plistlib
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import klausies as K

ETIKETE = "lv.vanags.klausies"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{ETIKETE}.plist"
ZURNALS = Path.home() / "Library" / "Logs" / "klausies.log"
SKRIPTS = Path(__file__).resolve().parent / "klausies.py"
FORMATI = {".pdf", ".docx", ".txt", ".md"}


def pazinot(virsraksts: str, teksts: str) -> None:
    if not shutil.which("osascript"):
        return
    t = teksts.replace('"', "'")[:200]
    v = virsraksts.replace('"', "'")
    subprocess.run(["osascript", "-e", f'display notification "{t}" with title "Klausies" subtitle "{v}"'],
                   capture_output=True)


def ienakosie() -> Path:
    m = K.noklusejuma_izvade() / "Ienakosie"
    (m / "apstradatie").mkdir(parents=True, exist_ok=True)
    (m / "neizdevas").mkdir(parents=True, exist_ok=True)
    return m


def palaist(args: list[str]) -> tuple[int, str]:
    r = subprocess.run([sys.executable, str(SKRIPTS), *args], capture_output=True, text=True)
    izvade = (r.stdout or "") + (r.stderr or "")
    print(izvade, flush=True)
    return r.returncode, izvade


def apstradat_rakstus() -> None:
    mape = ienakosie()
    for f in sorted(mape.iterdir()):
        # iCloud vēl nav lejupielādējis failu: palūdzam to izdarīt un mēģinām nākamreiz
        if f.name.startswith(".") and f.name.endswith(".icloud"):
            ists = mape / f.name[1:-len(".icloud")]
            if shutil.which("brctl"):
                subprocess.run(["brctl", "download", str(ists)], capture_output=True)
            print(f"Gaidu, kamēr iCloud lejupielādēs {ists.name}", flush=True)
            continue
        if not f.is_file() or f.suffix.lower() not in FORMATI or f.name.startswith("."):
            continue
        # pagaidām, līdz fails vairs nemainās (vēl tiek kopēts)
        izm = f.stat().st_size
        time.sleep(3)
        if f.stat().st_size != izm or izm == 0:
            continue
        print(f"\n=== {datetime.now():%Y-%m-%d %H:%M} Raksts: {f.name}", flush=True)
        kods, izvade = palaist([str(f)])
        if kods == 0 and "Gatavs" in izvade:
            shutil.move(str(f), str(mape / "apstradatie" / f.name))
            ilg = next((l.split("Ilgums")[-1].strip(" .") for l in izvade.splitlines() if "Ilgums" in l), "")
            pazinot("Raksts gatavs klausīšanai", f"{f.stem} ({ilg})" if ilg else f.stem)
        else:
            shutil.move(str(f), str(mape / "neizdevas" / f.name))
            pedeja = next((l for l in reversed(izvade.splitlines()) if l.strip()), "nezināma kļūda")
            pazinot("Rakstu neizdevās sagatavot", f"{f.name}: {pedeja}")


def apstradat_piezimes() -> None:
    print(f"\n=== {datetime.now():%Y-%m-%d %H:%M} Piezīmes", flush=True)
    kods, izvade = palaist(["piezimes"])
    for l in izvade.splitlines():
        if l.startswith("Apstrādātas "):
            try:
                n = int(l.split()[1])
            except ValueError:
                n = 0
            if n:
                pazinot("Piezīmes ievietotas dokumentos", f"Jaunas piezīmes: {n}")
    if "NEIZDEVĀS" in izvade:
        pazinot("Dažas piezīmes neizdevās apstrādāt", "Tās tiks mēģinātas vēlreiz. Skaties: klausies statuss")


def auto() -> None:
    K.KESA.mkdir(parents=True, exist_ok=True)
    with open(K.KESA / "auto.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Iepriekšējā palaišana vēl darbojas.", flush=True)
            return
        apstradat_rakstus()
        apstradat_piezimes()


def ieslegt() -> None:
    mape = ienakosie()
    ZURNALS.parent.mkdir(parents=True, exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    dati = {
        "Label": ETIKETE,
        "ProgramArguments": [sys.executable, str(SKRIPTS), "auto"],
        "StartInterval": 600,
        "WatchPaths": [str(mape)],
        "RunAtLoad": True,
        "ThrottleInterval": 30,
        "StandardOutPath": str(ZURNALS),
        "StandardErrorPath": str(ZURNALS),
        "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin",
                                 "PYTHONUNBUFFERED": "1", "LANG": "lv_LV.UTF-8"},
    }
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(PLIST)], capture_output=True)
    with PLIST.open("wb") as h:
        plistlib.dump(dati, h)
    r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(PLIST)], capture_output=True, text=True)
    if r.returncode != 0:
        K.kluda("Fona darbu neizdevās ieslēgt: " + (r.stderr or r.stdout).strip())
    K.zinot("Fona darbs ieslēgts.")
    K.zinot(f"  Ievieto PDF mapē: {mape}")
    K.zinot("  Piezīmes no iPhone tiks apstrādātas ik pēc 10 minūtēm, kamēr Mac ir ieslēgts.")
    K.zinot(f"  Žurnāls: {ZURNALS}")


def izslegt() -> None:
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(PLIST)], capture_output=True)
    if PLIST.exists():
        PLIST.unlink()
    K.zinot("Fona darbs izslēgts.")


def statuss() -> None:
    r = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{ETIKETE}"], capture_output=True, text=True)
    K.zinot("Fona darbs: " + ("ieslēgts" if r.returncode == 0 else "izslēgts"))
    if ZURNALS.exists():
        rindas = ZURNALS.read_text(encoding="utf-8", errors="replace").splitlines()[-25:]
        K.zinot("Pēdējās žurnāla rindas:\n" + "\n".join(rindas))


def savienojums() -> None:
    env = K.lasit_env()
    url, sl = env.get("KLAUSIES_GAS_URL"), env.get("KLAUSIES_GAS_SECRET")
    if not url or not sl:
        K.kluda("Google savienojums vēl nav iestatīts atslēgu failā.")
    kods = f"KLAUSIES1|{url}|{sl}"
    if shutil.which("pbcopy"):
        subprocess.run(["pbcopy"], input=kods.encode())
        K.zinot("Savienojuma kods ir nokopēts.")
        K.zinot("Ja iPhone un Mac izmanto vienu Apple ID, kodu var uzreiz ielīmēt iPhone (universālā starpliktuve).")
    K.zinot("\n" + kods + "\n")


def main(komanda: str, argv: list[str]) -> None:
    {"auto": auto, "auto-ieslegt": ieslegt, "auto-izslegt": izslegt,
     "statuss": statuss, "savienojums": savienojums}[komanda]()
