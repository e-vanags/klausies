"""
Klausīšanās asistents, 3. posms: balss piezīmju apstrāde.

  klausies piezimes            apstrādā jaunās piezīmes mapē Klausisanas/piezimes
  klausies piezimes --tests    pārbauda savienojumu ar Google (Apps Script)

Katrai piezīmei:
  1. iPhone ieraksts (m4a) tiek pārvērsts tekstā ar Azure runas atpazīšanu (latviešu vai angļu valoda);
  2. Claude sakārto piezīmi, nosaka veidu, projektu un precizē teikumu, uz kuru tā attiecas;
  3. piezīme tiek nosūtīta uz Google Apps Script, kas to ievieto raksta dokumentā, projekta dokumentā,
     ideju iesūtnē vai lasāmo rindā;
  4. rezerves kopija tiek pierakstīta failā piezimes/<raksts>_piezimes.md.

Apstrādātie faili tiek pārvietoti uz piezimes/apstradatas. Ieraksti netiek dzēsti.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import klausies as K

VEIDI = ["tēze", "metode", "rezultāts", "citāts", "kritika", "ideja", "jautājums", "saite"]


# ---------------------------------------------------------------- palīgfunkcijas

def mm_ss(s: float) -> str:
    s = int(s or 0)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def isa_atsauce(apa: str | None, lappuse) -> str:
    """APA atsauce tekstā: (Perugini et al., 2025, lpp. 3)."""
    if not apa:
        return f"(lpp. {lappuse})" if lappuse else ""
    m = re.search(r"\((\d{4}|n\.d\.)\)", apa)
    gads = m.group(1) if m else "n.d."
    autori_dala = apa[: m.start()] if m else apa
    uzv = re.findall(r"([A-ZĀČĒĢĪĶĻŅŠŪŽ][\w’'\-]+(?: [A-ZĀČĒĢĪĶĻŅŠŪŽ][\w’'\-]+)*), (?:[A-ZĀČĒĢĪĶĻŅŠŪŽ]\.\s?-?)+", autori_dala)
    if not uzv:
        a = autori_dala.strip().rstrip(".") or "Autors"
    elif len(uzv) == 1:
        a = uzv[0]
    elif len(uzv) == 2:
        a = f"{uzv[0]} & {uzv[1]}"
    else:
        a = f"{uzv[0]} et al."
    return f"({a}, {gads}" + (f", lpp. {lappuse})" if lappuse else ")")


def lasit_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def rakstit_json(p: Path, dati) -> None:
    p.write_text(json.dumps(dati, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- raksta dati

def atrast_rakstu(mape: Path, teksta_id: str | None) -> dict | None:
    if not teksta_id:
        return None
    dati = mape / "teksti" / f"{teksta_id}_dati.json"
    if dati.exists():
        return lasit_json(dati, None)
    # vecāki raksti: meklē apvienotajā failā pēc "id" faila sākumā
    for f in mape.glob("*.json"):
        if f.name == "katalogs.json":
            continue
        try:
            with f.open("r", encoding="utf-8") as h:
                galva = h.read(4000)
        except Exception:
            continue
        if f'"id": "{teksta_id}"' in galva:
            d = lasit_json(f, None)
            if d:
                d.pop("audio_base64", None)
                return d
    return None


def rindkopas_no_teikumiem(raksts: dict) -> list[dict]:
    """Ja raksta failā nav pilnu rindkopu (vecāka versija), saliek tās no teikumiem."""
    grupas: dict[int, dict] = {}
    for t in raksts.get("teikumi", []):
        g = grupas.setdefault(t["orig_rindkopa"], {"t": [], "v": t.get("virsraksts", False), "lpp": t.get("lappuse"), "izl": None})
        g["t"].append(t["orig"])
    return [{"t": " ".join(g["t"]), "v": g["v"], "lpp": g["lpp"], "izl": None} for _, g in sorted(grupas.items())]


def konteksts(raksts: dict | None, anchor: float, n_atz: int | None) -> list[dict]:
    if not raksts:
        return []
    out = []
    for t in raksts.get("teikumi", []):
        if anchor - 60 <= t["sakums_s"] <= anchor + 8 or t["n"] == n_atz:
            out.append({"n": t["n"], "teksts": t["orig"], "lappuse": t.get("lappuse")})
    return out


# ---------------------------------------------------------------- runas atpazīšana

def uz_wav(audio: Path, merkis: Path) -> Path:
    if audio.suffix.lower() == ".wav":
        return audio
    if shutil.which("afconvert"):
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(audio), str(merkis)],
                       check=True, capture_output=True)
        return merkis
    if shutil.which("ffmpeg"):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(audio), "-ac", "1", "-ar", "16000", str(merkis)], check=True)
        return merkis
    K.kluda("Nevaru pārveidot audio: nav ne afconvert (Mac), ne ffmpeg.")


def atpazit_runu(wav: Path, env: dict) -> tuple[str, str]:
    """Atgriež (teksts, valoda). Vispirms valodu nosaka automātiski, kļūdas gadījumā lieto latviešu valodu."""
    valoda = env.get("KLAUSIES_PIEZIMJU_VALODA")
    if valoda:
        return _atpazit(wav, env, valoda)
    try:
        teksts, val = _atpazit(wav, env, None)
        if teksts:
            return teksts, val
    except RuntimeError as e:
        K.zinot(f"  Automātiskā valodas noteikšana neizdevās ({str(e)[:80]}), mēģinu latviski.")
    return _atpazit(wav, env, "lv-LV")


def _atpazit(wav: Path, env: dict, valoda: str | None) -> tuple[str, str]:
    try:
        import azure.cognitiveservices.speech as sdk
    except ImportError:
        K.kluda("Nav uzstādīts Azure Speech SDK. Palaid: ~/Projects/klausies/.venv/bin/pip install -r requirements.txt")
    cfg = sdk.SpeechConfig(subscription=env["AZURE_SPEECH_KEY"], region=env["AZURE_SPEECH_REGION"])
    cfg.set_property(sdk.PropertyId.SpeechServiceConnection_InitialSilenceTimeoutMs, "15000")
    cfg.set_property(sdk.PropertyId.Speech_SegmentationSilenceTimeoutMs, "1500")
    audio_cfg = sdk.audio.AudioConfig(filename=str(wav))
    if valoda:
        rec = sdk.SpeechRecognizer(speech_config=cfg, language=valoda, audio_config=audio_cfg)
    else:
        auto = sdk.languageconfig.AutoDetectSourceLanguageConfig(languages=["lv-LV", "en-US"])
        rec = sdk.SpeechRecognizer(speech_config=cfg, auto_detect_source_language_config=auto, audio_config=audio_cfg)
    dalas, valodas, kludas = [], [], []
    beigas = threading.Event()

    def atpazits(e):
        if e.result.reason == sdk.ResultReason.RecognizedSpeech and e.result.text:
            dalas.append(e.result.text)
            if valoda:
                valodas.append(valoda)
            else:
                try:
                    valodas.append(sdk.AutoDetectSourceLanguageResult(e.result).language)
                except Exception:
                    pass

    def atcelts(e):
        if e.cancellation_details.reason == sdk.CancellationReason.Error:
            kludas.append(e.cancellation_details.error_details)
        beigas.set()

    rec.recognized.connect(atpazits)
    rec.canceled.connect(atcelts)
    rec.session_stopped.connect(lambda e: beigas.set())
    rec.start_continuous_recognition()
    beigas.wait(timeout=900)
    rec.stop_continuous_recognition()
    if kludas and not dalas:
        info = kludas[0] or ""
        if "401" in info or "Authentication" in info:
            K.kluda("Azure nepieņem atslēgu. Pārbaudi ~/.config/klausies/env")
        raise RuntimeError("Runas atpazīšana neizdevās: " + info)
    val = max(set(valodas), key=valodas.count) if valodas else (valoda or "lv-LV")
    return " ".join(dalas).strip(), val


# ---------------------------------------------------------------- Claude

INSTR = """Tu esi pētnieka asistents. Pētnieks klausījās tekstu (visbiežāk zinātnisku rakstu) un ierunāja balss piezīmi.
Piezīmes transkripcija ir iegūta ar automātisku runas atpazīšanu, tāpēc tajā var būt kļūdas.

Uzdevumi:
1. "piezime": uzraksti piezīmi skaidrā rakstu valodā, pirmajā personā, tajā pašā valodā, kurā viņš runāja.
   Izlabo atpazīšanas kļūdas un vārdu atkārtojumus, bet saglabā viņa domu. Nepievieno neko, ko viņš neteica.
   Izņem norādes, kas domātas sistēmai (piemēram, "lūdzu piefiksē", "šis noderēs PISA rakstam"), ja tās jau atspoguļotas citos laukos.
2. "veids": viens no: tēze, metode, rezultāts, citāts, kritika, ideja, jautājums, saite.
3. "projekts": ja viņš nosauca publikāciju vai projektu, kurai piezīme noderēs, atgriez tā īsu nosaukumu.
   Ja tas atbilst kādam no esošajiem projektiem, atgriez tieši esošo nosaukumu. Citādi null.
4. "teikums_n": teikuma numurs no konteksta, uz kuru piezīme attiecas. Noklusējumā tas ir atzīmētais teikums.
   Izvēlies citu tikai tad, ja piezīme skaidri runā par citu teikumu.
5. "ideja": true, ja tā ir brīva doma, kas nav saistīta ar konkrēto teksta vietu, vai ja viņš to nosauc par ideju.
6. "citats": true, ja viņš lūdz saglabāt precīzu citātu.
7. "lasamais": ja viņš lūdz atrast vai izlasīt rakstu, uz kuru atsaucas teksts, atgriez
   {"atsauce": pilna atsauce no atsauču saraksta, ja to var atrast, citādi atsauce no teikuma, "pamatojums": kāpēc viņš to grib izlasīt}.
   Citādi null.
8. "atcelt": true, ja piezīme ir tukša, nesaprotama vai viņš to atsauc.

Atbildi tikai ar vienu JSON objektu ar šiem laukiem."""


def claude_apstradat(env: dict, modelis: str, transkripcija: str, raksts: dict | None, kont: list[dict],
                     n_atz: int | None, projekti: list[str]) -> dict:
    dati = {
        "transkripcija": transkripcija,
        "teksts": (raksts or {}).get("nosaukums"),
        "atzimetais_teikums_n": n_atz,
        "konteksts": kont,
        "esosie_projekti": projekti,
        "atsauces": ((raksts or {}).get("atsauces") or [])[:120],
    }
    body = json.dumps({"model": modelis, "max_tokens": 2000, "messages": [
        {"role": "user", "content": INSTR + "\n\nDati:\n" + json.dumps(dati, ensure_ascii=False)}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, headers={
        "x-api-key": env["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01", "content-type": "application/json"})
    for meg in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                resp = json.load(r)
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 529) and meg < 3:
                time.sleep(10 * (meg + 1))
                continue
            raise RuntimeError(f"Claude kļūda {e.code}")
    teksts = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
    m = re.search(r"\{.*\}", teksts, re.S)
    if not m:
        raise RuntimeError("Claude neatgrieza JSON")
    return json.loads(m.group(0))


def bez_claude(transkripcija: str, n_atz: int | None) -> dict:
    t = transkripcija.lower()
    return {"piezime": transkripcija, "veids": "ideja" if t.startswith("ideja") else "tēze",
            "projekts": None, "teikums_n": n_atz, "ideja": t.startswith("ideja"), "citats": False,
            "lasamais": None, "atcelt": not transkripcija.strip()}


# ---------------------------------------------------------------- Google Apps Script

def gas(env: dict, darbiba: str, dati: dict) -> dict:
    url, slepens = env.get("KLAUSIES_GAS_URL"), env.get("KLAUSIES_GAS_SECRET")
    if not url or not slepens:
        raise RuntimeError("Google savienojums nav iestatīts (KLAUSIES_GAS_URL)")
    body = json.dumps({"secret": slepens, "action": darbiba, **dati}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    for meg in range(3):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                txt = r.read().decode("utf-8", "replace")
            break
        except urllib.error.HTTPError as e:
            if meg < 2 and e.code >= 500:
                time.sleep(5)
                continue
            raise RuntimeError(f"Google atbildēja ar kļūdu {e.code}")
        except urllib.error.URLError as e:
            if meg < 2:
                time.sleep(5)
                continue
            raise RuntimeError(f"Google nav sasniedzams ({e.reason})")
    try:
        atb = json.loads(txt)
    except Exception:
        raise RuntimeError("Google atbilde nav saprotama. Vai Apps Script ir publicēts kā tīmekļa lietotne ar piekļuvi 'Anyone'?")
    if not atb.get("ok"):
        raise RuntimeError("Google: " + str(atb.get("kluda")))
    return atb


def lejupieladet_no_google(env: dict, mape: Path) -> int:
    """Paņem iPhone nosūtītās piezīmes no Google un saglabā tās mapē piezimes."""
    import base64
    if not env.get("KLAUSIES_GAS_URL"):
        return 0
    kopa, kartas = 0, 0
    while kartas < 20:
        kartas += 1
        atb = gas(env, "sanemt", {"max": 10})
        lietas = atb.get("piezimes") or []
        if not lietas:
            break
        metas, ids = [], []
        for it in lietas:
            n = it.get("piezime") or {}
            if not n.get("id") or not it.get("audio_base64"):
                continue
            ext = "m4a" if "mp4" in (it.get("audio_mime") or "") else (it.get("audio_mime") or "audio/m4a").split("/")[-1].split(";")[0]
            n["fails"] = f"piezime_{n['id']}.{ext}"
            (mape / n["fails"]).write_bytes(base64.b64decode(it["audio_base64"]))
            metas.append(n)
            ids.append(n["id"])
        if metas:
            rakstit_json(mape / f"piezimes_google_{datetime.now():%Y%m%d_%H%M%S}_{kartas}.json", {"piezimes": metas})
        gas(env, "apstiprinat", {"ids": ids})
        kopa += len(metas)
        if not atb.get("vel"):
            break
    return kopa


# ---------------------------------------------------------------- galvenā daļa

def main(argv: list[str]) -> None:
    env = K.lasit_env()
    sakne = K.noklusejuma_izvade()
    mape = sakne / "piezimes"
    apstr = mape / "apstradatas"
    apstr.mkdir(parents=True, exist_ok=True)

    if "--tests" in argv:
        atb = gas(env, "ping", {})
        K.zinot("Savienojums ar Google darbojas." if atb.get("ok") else "Savienojums neizdevās.")
        return

    for k in ("AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION"):
        if not env.get(k):
            K.kluda(f"Trūkst {k} failā {K.ENV_FILE}")
    google = bool(env.get("KLAUSIES_GAS_URL"))
    if not google:
        K.zinot("Google savienojums vēl nav iestatīts, piezīmes saglabāšu tikai failos.")

    if google:
        try:
            sk = lejupieladet_no_google(env, mape)
            if sk:
                K.zinot(f"No Google saņemtas {sk} piezīmes")
        except Exception as e:
            K.zinot(f"Piezīmes no Google neizdevās saņemt: {e}")

    registrs_p = mape / "registrs.json"
    registrs = lasit_json(registrs_p, {"apstradatas": {}, "projekti": [], "raksti_google": {}})
    meta_faili = sorted(mape.glob("piezimes_*.json"))
    if not meta_faili:
        K.zinot(f"Nav jaunu piezīmju mapē {mape}")
        return

    rakstu_kesh: dict[str, dict | None] = {}
    kopa = jaunas = 0
    for mf in meta_faili:
        dati = lasit_json(mf, {})
        visas_gatavas = True
        for n in dati.get("piezimes", []):
            kopa += 1
            nid = str(n.get("id"))
            if nid in registrs["apstradatas"]:
                continue
            audio = mape / n.get("fails", "")
            if not audio.exists():
                K.zinot(f"  Nav atrasts ieraksts {n.get('fails')}, izlaižu")
                visas_gatavas = False
                continue
            datums = n.get("created", "")[:16].replace("T", " ")
            K.zinot(f"Piezīme {datums} · {n.get('nosaukums') or 'bez teksta'} · {mm_ss(n.get('pos'))}")
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    wav = uz_wav(audio, Path(tmp) / "p.wav")
                    transkr, valoda = atpazit_runu(wav, env)
                K.zinot(f"  Ierunāts ({valoda}): {transkr[:120]}{'…' if len(transkr) > 120 else ''}")

                tid = n.get("source")
                if tid not in rakstu_kesh:
                    rakstu_kesh[tid] = atrast_rakstu(sakne, tid)
                raksts = rakstu_kesh[tid]
                atz = (n.get("teikums") or {})
                n_atz = atz.get("n")
                kont = konteksts(raksts, n.get("anchor") or 0, n_atz)

                if env.get("ANTHROPIC_API_KEY"):
                    try:
                        r = claude_apstradat(env, K.CLAUDE_MODELIS, transkr, raksts, kont, n_atz, registrs["projekti"])
                    except Exception as e:
                        K.zinot(f"  Claude nav pieejams ({e}), saglabāju neapstrādātu tekstu.")
                        r = bez_claude(transkr, n_atz)
                else:
                    r = bez_claude(transkr, n_atz)

                if r.get("atcelt") or not (r.get("piezime") or "").strip():
                    K.zinot("  Piezīme tukša vai atsaukta, izlaižu.")
                    registrs["apstradatas"][nid] = {"atcelta": True}
                    rakstit_json(registrs_p, registrs)
                    continue

                # teikums, uz kuru attiecas piezīme
                tn = r.get("teikums_n") or n_atz
                teik = None
                if raksts and tn:
                    teik = next((t for t in raksts.get("teikumi", []) if t["n"] == tn), None)
                if teik is None and atz:
                    teik = {"orig": atz.get("orig"), "lappuse": atz.get("lappuse"), "sakums_s": atz.get("sakums_s")}
                projekts = (r.get("projekts") or "").strip() or None
                if projekts and projekts not in registrs["projekti"]:
                    registrs["projekti"].append(projekts)
                veids = r.get("veids") if r.get("veids") in VEIDI else ("tēze" if tid else "ideja")
                apa = (raksts or {}).get("apa") or n.get("apa")
                ieraksts = {
                    "id": nid, "datums": datums, "teksta_id": tid if raksts else None,
                    "nosaukums": (raksts or {}).get("nosaukums") or n.get("nosaukums"),
                    "apa": apa, "isa_atsauce": isa_atsauce(apa, (teik or {}).get("lappuse")) if teik else "",
                    "teikums": (teik or {}).get("orig"), "lappuse": (teik or {}).get("lappuse"),
                    "laiks": mm_ss(n.get("pos")), "piezime": r["piezime"].strip(), "veids": veids,
                    "projekts": projekts, "ideja": bool(r.get("ideja")) or not tid, "citats": bool(r.get("citats")),
                    "lasamais": r.get("lasamais") or None, "transkripcija": transkr, "valoda": valoda,
                    "audio": audio.name,
                }

                # rezerves kopija failā (vienmēr)
                fails_md = mape / f"{(tid or 'idejas')}_piezimes.md"
                jauns = not fails_md.exists()
                with fails_md.open("a", encoding="utf-8") as h:
                    if jauns:
                        h.write(f"# Piezīmes: {ieraksts['nosaukums'] or 'Ideju iesūtne'}\n\n")
                        if apa:
                            h.write(f"{apa}\n\n")
                    h.write(f"## {datums} · {veids} · {ieraksts['laiks']}"
                            + (f" · projekts: {projekts}" if projekts else "") + "\n\n")
                    if ieraksts["teikums"]:
                        h.write(f"> {ieraksts['teikums']} {ieraksts['isa_atsauce']}\n\n")
                    h.write(ieraksts["piezime"] + "\n\n")
                    if ieraksts["lasamais"]:
                        h.write(f"Lasāmo rindai: {ieraksts['lasamais'].get('atsauce')}\n\n")
                    h.write(f"*Ierunāts: {transkr}*\n\n")

                # Google dokumenti
                if google:
                    if raksts and tid not in registrs["raksti_google"]:
                        rk = raksts.get("rindkopas") or rindkopas_no_teikumiem(raksts)
                        atb = gas(env, "raksts", {"id": tid, "nosaukums": raksts.get("nosaukums"), "apa": apa,
                                                  "doi": raksts.get("doi"), "rindkopas": rk,
                                                  "atsauces": raksts.get("atsauces") or []})
                        registrs["raksti_google"][tid] = atb.get("url")
                        K.zinot(f"  Raksta dokuments: {atb.get('url')}")
                    atb = gas(env, "piezime", ieraksts)
                    for k, v in (atb.get("saites") or {}).items():
                        K.zinot(f"  {k}: {v}")
                registrs["apstradatas"][nid] = {"datums": datetime.now().isoformat(timespec="seconds"),
                                                "veids": veids, "projekts": projekts}
                rakstit_json(registrs_p, registrs)
                jaunas += 1
                K.zinot(f"  Gatavs: {veids}" + (f", projekts {projekts}" if projekts else ""))
            except Exception as e:
                visas_gatavas = False
                K.zinot(f"  NEIZDEVĀS: {e}. Ieraksts paliek mapē, mēģināšu nākamreiz.")

        # pārvieto apstrādātos failus
        if visas_gatavas:
            for n in dati.get("piezimes", []):
                a = mape / n.get("fails", "")
                if a.exists():
                    shutil.move(str(a), str(apstr / a.name))
            shutil.move(str(mf), str(apstr / mf.name))

    K.zinot(f"\nApstrādātas {jaunas} jaunas piezīmes (kopā failos {kopa}).")
    if not google:
        K.zinot(f"Piezīmes ir failos mapē {mape}")
