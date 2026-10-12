#!/usr/bin/env python3
"""
Klausīšanās asistents, 2. posms.

No PDF, DOCX, TXT vai MD faila izveido:
  <id>.m4a            raksta audio (Azure neironu balss)
  <id>.json           laika karte: katrs teikums ar sākuma un beigu laiku un saiti uz oriģinālu
  teksti/<id>_originals.md      oriģinālais teksts ar lappusēm un atsaucēm
  teksti/<id>_klausisanai.txt   teksts, kas tika nolasīts

Lietošana:
  python3 klausies.py raksts.pdf
  python3 klausies.py raksts.pdf --tikai-teksts      # bez audio, lai pārbaudītu tekstu
  python3 klausies.py raksts.pdf --balss everita --bez-claude

Atslēgas tiek lasītas no ~/.config/klausies/env:
  AZURE_SPEECH_KEY=...
  AZURE_SPEECH_REGION=westeurope
  ANTHROPIC_API_KEY=...      (nav obligāta)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import wave
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

VERSIJA = "0.2.0"
ENV_FILE = Path.home() / ".config" / "klausies" / "env"
ICLOUD = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
SAMPLE_RATE = 24000

BALSIS = {
    "lv": {"nils": "lv-LV-NilsNeural", "everita": "lv-LV-EveritaNeural"},
    "en": {
        "andrew": "en-US-AndrewNeural", "ava": "en-US-AvaNeural",
        "ryan": "en-GB-RyanNeural", "sonia": "en-GB-SoniaNeural",
    },
}
NOKLUSETA_BALSS = {"lv": "nils", "en": "andrew"}
CLAUDE_MODELIS = "claude-sonnet-5-5"

LV_UPPER = "A-ZĀČĒĢĪĶĻŅŠŪŽ"
LV_LOWER = "a-zāčēģīķļņšūž"
LV_DIAC = set("āčēģīķļņšūžĀČĒĢĪĶĻŅŠŪŽ")

REF_HEADINGS = re.compile(
    r"^\s*(\d+[.\s]*)?(references|reference list|bibliography|works cited|literature cited|"
    r"literatūra|izmantotā literatūra|literatūras saraksts|izmantotie avoti|avoti|atsauces)\s*:?\s*$",
    re.I,
)
LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", " ": " "}


# ---------------------------------------------------------------- datu struktūras

@dataclass
class Rindkopa:
    teksts: str
    lappuse: int | None = None
    virsraksts: bool = False


@dataclass
class Teikums:
    n: int
    rindkopa: int        # 1.. (oriģināla rindkopas numurs)
    teikums: int         # 1.. (teikuma numurs rindkopā)
    lappuse: int | None
    orig: str
    klausisanai: str = ""
    virsraksts: bool = False
    rindkopas_beigas: bool = False
    sakums_s: float = 0.0
    beigas_s: float = 0.0


@dataclass
class Dokuments:
    nosaukums: str
    rindkopas: list[Rindkopa]
    atsauces: list[Rindkopa] = field(default_factory=list)
    doi: str | None = None
    meta_nosaukums: str | None = None


# ---------------------------------------------------------------- palīgfunkcijas

def zinot(msg: str) -> None:
    print(msg, flush=True)


def kluda(msg: str) -> None:
    print("KĻŪDA: " + msg, file=sys.stderr, flush=True)
    sys.exit(1)


def lasit_env() -> dict[str, str]:
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION", "ANTHROPIC_API_KEY"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def tirit(t: str) -> str:
    for a, b in LIGATURES.items():
        t = t.replace(a, b)
    t = t.replace("­", "")
    return re.sub(r"\s+", " ", t).strip()


def slug(t: str, n: int = 40) -> str:
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-zA-Z0-9]+", "_", t).strip("_").lower()
    return t[:n].strip("_") or "teksts"


def savienot_rindas(lines: list[str]) -> str:
    out = ""
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if not out:
            out = ln
        elif out.endswith("-") and ln[:1].islower() and len(out) > 1 and out[-2].isalpha():
            out = out[:-1] + ln
        else:
            out += " " + ln
    return tirit(out)


def noteikt_valodu(teksts: str) -> str:
    burti = [c for c in teksts if c.isalpha()]
    if not burti:
        return "en"
    lv = sum(1 for c in burti if c in LV_DIAC)
    return "lv" if lv / len(burti) > 0.008 else "en"


# ---------------------------------------------------------------- izvilkšana

def izvilkt_pdf(cels: Path) -> Dokuments:
    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz
        except ImportError:
            fitz = None
    if fitz is None:
        kluda("Nav uzstādīts PyMuPDF. Palaid: pip install -r requirements.txt")
    doc = fitz.open(str(cels))
    meta = doc.metadata or {}
    bloki = []
    for pno, page in enumerate(doc, 1):
        h = page.rect.height
        for b in page.get_text("dict")["blocks"]:
            if b.get("type") != 0:
                continue
            lines, sizes, bold, total = [], [], 0, 0
            for ln in b["lines"]:
                t = "".join(s["text"] for s in ln["spans"])
                if t.strip():
                    lines.append(t)
                for s in ln["spans"]:
                    n = len(s["text"].strip())
                    if not n:
                        continue
                    sizes.append((s["size"], n))
                    total += n
                    if "bold" in s["font"].lower() or (s["flags"] & 16):
                        bold += n
            if not lines:
                continue
            size = sum(a * b for a, b in sizes) / max(1, sum(b for _, b in sizes))
            bloki.append({
                "lines": lines, "teksts": savienot_rindas(lines), "page": pno,
                "y0": b["bbox"][1] / h, "y1": b["bbox"][3] / h,
                "size": size, "bold": bold / max(total, 1), "chars": total,
            })
    lpp_skaits = len(doc)
    first_pages_text = " ".join(b["teksts"] for b in bloki if b["page"] <= 2)

    # galvenes, kājenes un lappušu numuri
    def atslega(t: str) -> str:
        return re.sub(r"\d+", "#", t.lower()).strip()
    malas = Counter(atslega(b["teksts"]) for b in bloki if (b["y0"] < 0.09 or b["y1"] > 0.91) and b["chars"] < 160)
    robeza = max(2, int(lpp_skaits * 0.3))
    tirie = []
    for b in bloki:
        t = b["teksts"]
        mala = b["y0"] < 0.09 or b["y1"] > 0.91
        if re.fullmatch(r"[\d\s\-–|/.]*(page|lpp\.?|p\.)?[\d\s\-–|/.]*(of|no)?[\d\s]*", t, re.I) and len(t) < 20:
            continue
        if mala and b["chars"] < 160 and malas[atslega(t)] >= robeza:
            continue
        tirie.append(b)

    pamata = []
    for b in tirie:
        pamata.extend([round(b["size"], 1)] * b["chars"])
    pamata_izm = statistics.median(pamata) if pamata else 10.0

    rindkopas: list[Rindkopa] = []
    for b in tirie:
        t = b["teksts"]
        ir_virsraksts = (
            b["chars"] < 140 and not re.search(r"[.,;:]$", t)
            and (b["size"] >= pamata_izm * 1.12 or b["bold"] > 0.6)
            and not t[:1].islower()
        )
        if rindkopas and not ir_virsraksts and not rindkopas[-1].virsraksts:
            prev = rindkopas[-1]
            if not re.search(r"[.!?:\"”’)\]]$", prev.teksts) and t[:1].islower():
                if prev.teksts.endswith("-") and prev.teksts[-2:-1].isalpha():
                    prev.teksts = prev.teksts[:-1] + t
                else:
                    prev.teksts += " " + t
                continue
        rindkopas.append(Rindkopa(t, b["page"], ir_virsraksts))

    doi = None
    m = re.search(r"\b(10\.\d{4,9}/[^\s\"<>,;]+[^\s\"<>,;.)\]])", first_pages_text)
    if m:
        doi = m.group(1)
    return Dokuments(nosaukums="", rindkopas=rindkopas, doi=doi, meta_nosaukums=(meta.get("title") or "").strip() or None)


def izvilkt_docx(cels: Path) -> Dokuments:
    try:
        import docx
    except ImportError:
        kluda("Nav uzstādīts python-docx. Palaid: pip install -r requirements.txt")
    d = docx.Document(str(cels))
    rk = []
    for p in d.paragraphs:
        t = tirit(p.text)
        if not t:
            continue
        stils = (p.style.name or "").lower() if p.style is not None else ""
        rk.append(Rindkopa(t, None, stils.startswith(("heading", "virsraksts", "title"))))
    titls = d.core_properties.title or None
    return Dokuments(nosaukums="", rindkopas=rk, meta_nosaukums=titls)


def izvilkt_txt(cels: Path) -> Dokuments:
    raw = cels.read_text(encoding="utf-8", errors="replace")
    rk = []
    for bloks in re.split(r"\n\s*\n", raw):
        lines = [l for l in bloks.splitlines() if l.strip()]
        if not lines:
            continue
        if len(lines) == 1 and lines[0].lstrip().startswith("#"):
            rk.append(Rindkopa(tirit(lines[0].lstrip("# ")), None, True))
            continue
        rk.append(Rindkopa(savienot_rindas(lines), None, False))
    return Dokuments(nosaukums="", rindkopas=rk)


def atdalit_atsauces(doc: Dokuments) -> None:
    for i, r in enumerate(doc.rindkopas):
        if len(r.teksts) < 60 and REF_HEADINGS.match(r.teksts):
            doc.atsauces = doc.rindkopas[i + 1:]
            doc.rindkopas = doc.rindkopas[: i + 1]
            doc.rindkopas[-1].virsraksts = True
            return


# ---------------------------------------------------------------- metadati

def crossref(doi: str) -> dict | None:
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi)
    req = urllib.request.Request(url, headers={"User-Agent": "klausies/0.2 (personal research tool)"})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            return json.load(r).get("message")
    except Exception as e:  # tīkls vai DOI nav atrasts
        zinot(f"  Crossref nav pieejams ({e.__class__.__name__}), atsauci jāpārbauda.")
        return None


def apa_no_crossref(m: dict, doi: str) -> tuple[str, str]:
    def autors(a: dict) -> str:
        fam = a.get("family") or a.get("name") or ""
        giv = a.get("given") or ""
        ini = " ".join(p[0] + "." for p in re.split(r"[\s\-]+", giv) if p)
        return f"{fam}, {ini}".strip(", ")
    au = [autors(a) for a in m.get("author", [])]
    if len(au) > 20:
        au_t = ", ".join(au[:19]) + ", ... " + au[-1]
    elif len(au) > 1:
        au_t = ", ".join(au[:-1]) + ", & " + au[-1]
    else:
        au_t = au[0] if au else ""
    gads = None
    for k in ("published-print", "published-online", "issued"):
        dp = (m.get(k) or {}).get("date-parts") or []
        if dp and dp[0] and dp[0][0]:
            gads = dp[0][0]
            break
    titls = (m.get("title") or [""])[0]
    zurn = (m.get("container-title") or [""])[0]
    vol, iss, lpp = m.get("volume"), m.get("issue"), m.get("page")
    daļas = [f"{au_t} ({gads or 'n.d.'}). {titls}."]
    if zurn:
        z = zurn
        if vol:
            z += f", {vol}"
            if iss:
                z += f"({iss})"
        if lpp:
            z += f", {lpp.replace('-', '–')}"
        daļas.append(z + ".")
    daļas.append(f"https://doi.org/{doi}")
    return titls, " ".join(daļas)


# ---------------------------------------------------------------- teikumi

SAISINAJUMI = [
    "et al.", "e.g.", "i.e.", "cf.", "vs.", "etc.", "Fig.", "fig.", "Figs.", "Tab.", "No.", "no.",
    "Vol.", "vol.", "pp.", "p.", "Dr.", "Prof.", "Mr.", "Ms.", "Mrs.", "St.", "Eq.", "Ch.", "approx.",
    "ca.", "resp.", "u. c.", "u.c.", "t. i.", "t.i.", "piem.", "utt.", "t. sk.", "u. tml.", "sk.",
    "att.", "tab.", "lpp.", "g.", "gs.", "nr.", "Nr.", "prof.", "doc.", "asoc.", "red.", "izd.", "sēj.",
]
_PROT = "⁣"
_SPLIT = re.compile(r"([.!?…][\"”’»)\]]*)\s+(?=[\"“„«(\[]?[" + LV_UPPER + r"0-9])")


def sadalit_teikumos(t: str) -> list[str]:
    p = t
    for a in sorted(SAISINAJUMI, key=len, reverse=True):
        p = re.sub(r"(?<![\w])" + re.escape(a), a.replace(".", _PROT), p)
    p = re.sub(r"\b([" + LV_UPPER + r"])\.(?=\s)", r"\1" + _PROT, p)  # iniciāļi
    out, start = [], 0
    for m in _SPLIT.finditer(p):
        end = m.end(1)
        out.append(p[start:end])
        start = m.end()
    out.append(p[start:])
    return [s.replace(_PROT, ".").strip() for s in out if s.strip()]


def veidot_teikumus(doc: Dokuments) -> list[Teikums]:
    res, n = [], 0
    for ri, r in enumerate(doc.rindkopas, 1):
        daļas = [r.teksts] if r.virsraksts else sadalit_teikumos(r.teksts)
        for ti, s in enumerate(daļas, 1):
            n += 1
            res.append(Teikums(n=n, rindkopa=ri, teikums=ti, lappuse=r.lappuse, orig=s,
                               virsraksts=r.virsraksts, rindkopas_beigas=(ti == len(daļas))))
    return res


# ---------------------------------------------------------------- klausīšanās teksts: noteikumi

_PAREN = re.compile(r"\s*\(([^()]{2,400})\)")
_GADS_BEIGAS = re.compile(
    r"((?:19|20)\d{2}[a-z]?|n\.\s?d\.|in press|forthcoming|iesniegts)"
    r"(\s*,\s*(?:p|pp|lpp)\.?\s*\d+(\s*[-–]\s*\d+)?)?\s*$", re.I)
_TIKAI_GADI = re.compile(r"^\s*(?:19|20)\d{2}[a-z]?(\s*[,;]\s*(?:19|20)\d{2}[a-z]?)*(\s*,\s*(?:p|pp|lpp)\.?\s*\d+(\s*[-–]\s*\d+)?)?\s*$")
_VARDS = re.compile(r"[" + LV_UPPER + r"][\w’'\-]+")
_NUM_ATS = re.compile(r"\s*\[\d+(?:\s*[-–,]\s*\d+)*\]")
_URL = re.compile(r"\s*(?:https?://\S+|www\.\S+|doi:\s*\S+|\b10\.\d{4,9}/\S+)")

IZVERST = {
    "lv": [("u. c.", "un citi"), ("u.c.", "un citi"), ("t. i.", "tas ir"), ("t.i.", "tas ir"),
           ("piem.", "piemēram"), ("utt.", "un tā tālāk"), ("t. sk.", "tai skaitā"),
           ("u. tml.", "un tamlīdzīgi"), ("sk.", "skatīt"), ("att.", "attēls"), ("tab.", "tabula"),
           ("lpp.", "lappuse"), ("et al.", "un kolēģi"), ("vs.", "pret"), (" & ", " un ")],
    "en": [("e.g.,", "for example,"), ("e.g.", "for example"), ("i.e.,", "that is,"), ("i.e.", "that is"),
           ("et al.", "and colleagues"), ("etc.", "et cetera"), ("vs.", "versus"), ("cf.", "compare"),
           ("Fig.", "Figure"), ("Figs.", "Figures"), ("Tab.", "Table"), ("Eq.", "Equation"),
           ("approx.", "approximately"), ("ca.", "circa"), ("No.", "number"), (" & ", " and ")],
}


def ir_atsauce(iekss: str) -> bool:
    if _TIKAI_GADI.match(iekss):
        return True
    iekss = re.sub(r"^(see|e\.g\.,?|cf\.|sk\.|piem\.,?|skat\.)\s+", "", iekss.strip(), flags=re.I)
    daļas = [d.strip() for d in iekss.split(";") if d.strip()]
    return bool(daļas) and all(_GADS_BEIGAS.search(d) and _VARDS.search(d) for d in daļas)


def klausisanai_noteikumi(s: str, valoda: str) -> str:
    t = _URL.sub("", s)
    t = _PAREN.sub(lambda m: "" if ir_atsauce(m.group(1)) else m.group(0), t)
    t = _NUM_ATS.sub("", t)
    for a, b in IZVERST[valoda]:
        t = t.replace(a, b)
    t = re.sub(r"\s+([,.;:!?])(?=\s|$)", r"\1", t)
    t = re.sub(r"\(\s*\)", "", t)
    t = re.sub(r"([,;])\1+", r"\1", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    if s.rstrip().endswith((".", "!", "?")) and t and not t.endswith((".", "!", "?", ":", "\"", "”")):
        t += "."
    return t


# ---------------------------------------------------------------- klausīšanās teksts: Claude

CLAUDE_INSTR = {
    "lv": """Tu sagatavo zinātniska teksta teikumus nolasīšanai ar latviešu runas sintezatoru.
Katram teikumam atgriez klausīšanās versiju. Noteikumi:
1. Saturu nemaini, nesaīsini, nepārfrāzē un nepievieno komentārus. Saglabā teikuma nozīmi un vārdu secību, cik vien iespējams.
2. Izņem atsauces iekavās (piemēram, "(Kotov u. c., 2017)"), numurētas atsauces ([12]), saites un DOI.
3. Atšifrē saīsinājumus ("u. c." uz "un citi", "t. i." uz "tas ir", "piem." uz "piemēram").
4. Svešus personvārdus un vietvārdus raksti latviskā izrunas formā ("Lakens", "Kahnemans"), oriģinālrakstību iekavās izņem.
5. Angliskos terminus un saīsinājumus, ja tos nolasītu nepareizi, aizstāj ar latvisku izrunājamu formu ("PISA" paliek "PISA", "OECD" kļūst par "O E C D", "R software" kļūst par "programma R").
6. Skaitļus, procentus, statistiskos simbolus un formulas raksti tā, lai tos varētu skaidri izrunāt ("p < 0,05" kļūst par "p mazāks par nulle komats nulle pieci"; "M = 3,2" kļūst par "vidējais trīs komats divi"; "95 % TI" kļūst par "deviņdesmit piecu procentu ticamības intervāls").
7. Ja teikums ir tikai atsauce, tabulas fragments vai cits nenolasāms teksts, atgriez tukšu virkni.""",
    "en": """You prepare sentences of a scientific text for an English text to speech voice.
Return a listening version of every sentence. Rules:
1. Do not change, shorten, paraphrase or comment on the content. Keep meaning and word order as far as possible.
2. Remove parenthetical citations (e.g. "(Smith et al., 2017)"), numbered citations ([12]), URLs and DOIs.
3. Expand abbreviations ("e.g." to "for example", "i.e." to "that is", "et al." to "and colleagues").
4. Write numbers, percentages, statistical symbols and formulas so they can be spoken clearly ("p < .05" becomes "p less than point zero five"; "M = 3.2, SD = 1.1" becomes "mean three point two, standard deviation one point one"; "95% CI" becomes "ninety five percent confidence interval").
5. If a sentence is only a citation, a table fragment or otherwise unreadable, return an empty string.""",
}


def claude_sagatavot(teikumi: list[Teikums], valoda: str, atslega: str, modelis: str) -> int:
    """Aizstāj t.klausisanai ar Claude versiju. Atgriež apstrādāto teikumu skaitu."""
    mainiti, partija = 0, []
    def apstradat(p: list[Teikums]) -> int:
        dati = [{"n": t.n, "teksts": t.orig} for t in p]
        uzd = (CLAUDE_INSTR[valoda] + "\n\nAtbildi tikai ar JSON masīvu formā "
               '[{"n": 1, "teksts": "..."}], ar tiem pašiem n un tādā pašā secībā.\n\n'
               + json.dumps(dati, ensure_ascii=False))
        body = json.dumps({"model": modelis, "max_tokens": 8000,
                           "messages": [{"role": "user", "content": uzd}]}).encode()
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, headers={
            "x-api-key": atslega, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        for meginajums in range(4):
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    resp = json.load(r)
                break
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 529) and meginajums < 3:
                    time.sleep(10 * (meginajums + 1))
                    continue
                zinot(f"  Claude kļūda {e.code}: {e.read()[:300]!r}. Šai daļai izmanto noteikumus.")
                return 0
            except Exception as e:
                zinot(f"  Claude nav pieejams ({e.__class__.__name__}). Šai daļai izmanto noteikumus.")
                return 0
        teksts = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
        m = re.search(r"\[.*\]", teksts, re.S)
        try:
            atb = {int(x["n"]): str(x.get("teksts", "")) for x in json.loads(m.group(0))} if m else {}
        except Exception:
            atb = {}
        sk = 0
        for t in p:
            if t.n not in atb:
                continue
            c = tirit(atb[t.n])
            # drošības pārbaude: Claude versija nedrīkst būt stipri īsāka par noteikumu versiju
            if c and len(t.klausisanai) > 40 and len(c) < 0.6 * len(t.klausisanai):
                continue
            if not c and len(t.klausisanai) > 80:
                continue
            t.klausisanai = c
            sk += 1
        return sk

    simboli = 0
    for t in teikumi:
        if t.virsraksts:
            continue
        partija.append(t)
        simboli += len(t.orig)
        if simboli > 6000 or len(partija) >= 60:
            mainiti += apstradat(partija)
            zinot(f"  Claude: sagatavoti {mainiti} teikumi")
            partija, simboli = [], 0
    if partija:
        mainiti += apstradat(partija)
        zinot(f"  Claude: sagatavoti {mainiti} teikumi")
    return mainiti


# ---------------------------------------------------------------- runas sintēze

class AzureBalss:
    def __init__(self, atslega: str, regions: str, balss: str, valoda: str):
        try:
            import azure.cognitiveservices.speech as sdk
        except ImportError:
            kluda("Nav uzstādīts Azure Speech SDK. Palaid: pip install -r requirements.txt")
        self.sdk, self.atslega, self.regions, self.balss = sdk, atslega, regions, balss
        self.xml_lang = {"lv": "lv-LV", "en": balss[:5]}[valoda]

    def sintezet(self, ssml_kermenis: str) -> tuple[bytes, dict[str, float]]:
        sdk = self.sdk
        ssml = (f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="{self.xml_lang}">'
                f'<voice name="{self.balss}">{ssml_kermenis}</voice></speak>')
        for meginajums in range(6):
            cfg = sdk.SpeechConfig(subscription=self.atslega, region=self.regions)
            cfg.set_speech_synthesis_output_format(sdk.SpeechSynthesisOutputFormat.Raw24Khz16BitMonoPcm)
            synth = sdk.SpeechSynthesizer(speech_config=cfg, audio_config=None)
            atzimes: dict[str, float] = {}
            synth.bookmark_reached.connect(lambda e: atzimes.__setitem__(e.text, e.audio_offset / 1e7))
            r = synth.speak_ssml_async(ssml).get()
            if r.reason == sdk.ResultReason.SynthesizingAudioCompleted:
                return bytes(r.audio_data), atzimes
            det = r.cancellation_details
            info = f"{det.reason} {det.error_details or ''}"
            if any(x in info for x in ("429", "Too many", "throttl", "TooManyRequests")) and meginajums < 5:
                gaidit = 20 * (meginajums + 1)
                zinot(f"  Azure ierobežo pieprasījumus, gaidu {gaidit} s")
                time.sleep(gaidit)
                continue
            if "401" in info or "Authentication" in info:
                kluda("Azure nepieņem atslēgu vai reģionu. Pārbaudi ~/.config/klausies/env")
            kluda("Azure runas sintēze neizdevās: " + info)
        kluda("Azure runas sintēze neizdevās pēc vairākiem mēģinājumiem")


class TestaBalss:
    """Bez interneta: klusums atbilstoši teksta garumam. Izmanto skripta pārbaudei."""
    def sintezet(self, ssml_kermenis: str) -> tuple[bytes, dict[str, float]]:
        atzimes, laiks = {}, 0.0
        for m in re.finditer(r'<bookmark mark="([^"]+)"/>|<break time="(\d+)ms"/>|([^<]+)', ssml_kermenis):
            if m.group(1):
                atzimes[m.group(1)] = laiks
            elif m.group(2):
                laiks += int(m.group(2)) / 1000
            elif m.group(3):
                laiks += len(m.group(3)) * 0.065
        return b"\x00\x00" * int(laiks * SAMPLE_RATE), atzimes


def veidot_ssml(daļa: list[Teikums]) -> str:
    out = []
    for t in daļa:
        txt = xml_escape(t.klausisanai)
        if t.virsraksts:
            out.append(f'<break time="700ms"/><bookmark mark="s{t.n}"/>{txt}<break time="600ms"/>')
        else:
            out.append(f'<bookmark mark="s{t.n}"/>{txt}' + ('<break time="550ms"/>' if t.rindkopas_beigas else " "))
    return "".join(out)


def sintezet_visu(teikumi: list[Teikums], balss, max_simboli: int = 2500) -> bytes:
    lasami = [t for t in teikumi if t.klausisanai]
    dalas, cur, sk = [], [], 0
    for t in lasami:
        if cur and sk + len(t.klausisanai) > max_simboli:
            dalas.append(cur)
            cur, sk = [], 0
        cur.append(t)
        sk += len(t.klausisanai)
    if cur:
        dalas.append(cur)

    pcm = bytearray()
    pauze = b"\x00\x00" * int(0.25 * SAMPLE_RATE)
    for i, d in enumerate(dalas, 1):
        nobide = len(pcm) / (2 * SAMPLE_RATE)
        audio, atzimes = balss.sintezet(veidot_ssml(d))
        ilgums = len(audio) / (2 * SAMPLE_RATE)
        for j, t in enumerate(d):
            sak = atzimes.get(f"s{t.n}")
            if sak is None:  # ja atzīme pazudusi, ņem iepriekšējā teikuma beigas
                sak = d[j - 1].beigas_s - nobide if j else 0.0
            t.sakums_s = round(nobide + sak, 3)
        for j, t in enumerate(d):
            nakamais = d[j + 1].sakums_s if j + 1 < len(d) else nobide + ilgums
            t.beigas_s = round(max(t.sakums_s, nakamais), 3)
        pcm += audio + pauze
        zinot(f"  Audio: {i}/{len(dalas)} daļas, {fmt_laiks(len(pcm) / (2 * SAMPLE_RATE))}")
    return bytes(pcm)


def fmt_laiks(s: float) -> str:
    s = int(s)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def saglabat_audio(pcm: bytes, merkis_bez_ext: Path) -> Path:
    wav = merkis_bez_ext.with_suffix(".wav")
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    if shutil.which("afconvert"):
        m4a = merkis_bez_ext.with_suffix(".m4a")
        subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", "-b", "64000", str(wav), str(m4a)], check=True)
        wav.unlink()
        return m4a
    try:
        import lameenc
        enc = lameenc.Encoder()
        enc.set_bit_rate(64)
        enc.set_in_sample_rate(SAMPLE_RATE)
        enc.set_channels(1)
        enc.set_quality(2)
        mp3 = merkis_bez_ext.with_suffix(".mp3")
        mp3.write_bytes(enc.encode(pcm) + enc.flush())
        wav.unlink()
        return mp3
    except ImportError:
        return wav


# ---------------------------------------------------------------- izvade

def originals_md(doc: Dokuments, apa: str | None, teikumi: list[Teikums]) -> str:
    out = [f"# {doc.nosaukums}", ""]
    if apa:
        out += [f"**Atsauce:** {apa}", ""]
    if doc.doi:
        out += [f"**DOI:** {doc.doi}", ""]
    out += [f"*Sagatavots {datetime.now():%Y-%m-%d %H:%M} ar klausies.py {VERSIJA}*", ""]
    lpp = None
    for i, r in enumerate(doc.rindkopas, 1):
        if r.lappuse and r.lappuse != lpp:
            out += [f"[lpp. {r.lappuse}]", ""]
            lpp = r.lappuse
        out += [(f"## {r.teksts}" if r.virsraksts else r.teksts) + f" <!-- r{i} -->", ""]
    if doc.atsauces:
        out += ["## Atsauču saraksts (oriģināls)", ""]
        out += [r.teksts for r in doc.atsauces]
    return "\n".join(out).strip() + "\n"


def noklusejuma_izvade() -> Path:
    return (ICLOUD / "Klausisanas") if ICLOUD.exists() else (Path.home() / "Klausisanas")


def atjaunot_katalogu(mape: Path, ieraksts: dict) -> None:
    kat = mape / "katalogs.json"
    dati = []
    if kat.exists():
        try:
            dati = json.loads(kat.read_text(encoding="utf-8"))
        except Exception:
            dati = []
    dati = [d for d in dati if d.get("id") != ieraksts["id"]] + [ieraksts]
    kat.write_text(json.dumps(dati, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- galvenā daļa

def main() -> None:
    ap = argparse.ArgumentParser(description="Teksts uz audio ar laika karti klausīšanās asistentam.")
    ap.add_argument("fails", type=Path)
    ap.add_argument("--valoda", choices=["lv", "en"], help="noklusējumā nosaka automātiski")
    ap.add_argument("--balss", help="lv: nils, everita; en: andrew, ava, ryan, sonia; vai pilns Azure nosaukums")
    ap.add_argument("--izvade", type=Path, help="mape rezultātiem (noklusējumā iCloud Drive/Klausisanas)")
    ap.add_argument("--nosaukums", help="teksta nosaukums, ja automātiskais neder")
    ap.add_argument("--tikai-teksts", action="store_true", help="izveidot tikai teksta failus, bez audio")
    ap.add_argument("--bez-claude", action="store_true", help="sagatavot tekstu tikai ar noteikumiem")
    ap.add_argument("--modelis", default=CLAUDE_MODELIS)
    ap.add_argument("--testa-balss", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()

    if not a.fails.exists():
        kluda(f"Fails nav atrasts: {a.fails}")
    env = lasit_env()
    ext = a.fails.suffix.lower()
    zinot(f"Lasu {a.fails.name}")
    if ext == ".pdf":
        doc = izvilkt_pdf(a.fails)
    elif ext == ".docx":
        doc = izvilkt_docx(a.fails)
    elif ext in (".txt", ".md", ".markdown"):
        doc = izvilkt_txt(a.fails)
    else:
        kluda("Atbalstītie formāti: PDF, DOCX, TXT, MD")
    if not doc.rindkopas:
        kluda("Failā neatradu tekstu. Ja tas ir skenēts PDF, tam vispirms vajag teksta atpazīšanu.")
    atdalit_atsauces(doc)

    valoda = a.valoda or noteikt_valodu(" ".join(r.teksts for r in doc.rindkopas[:200]))
    apa = None
    if doc.doi:
        zinot(f"  DOI: {doc.doi}")
        m = crossref(doc.doi)
        if m:
            titls, apa = apa_no_crossref(m, doc.doi)
            doc.nosaukums = titls
    if a.nosaukums:
        doc.nosaukums = a.nosaukums
    if not doc.nosaukums:
        mt = doc.meta_nosaukums
        if mt and len(mt) > 8 and not re.search(r"\.(docx?|pdf|tex)$|^microsoft word", mt, re.I):
            doc.nosaukums = mt
        else:
            virs = next((r.teksts for r in doc.rindkopas[:15] if r.virsraksts and len(r.teksts) > 8), None)
            doc.nosaukums = virs or a.fails.stem.replace("_", " ")
    tid = f"{date.today().isoformat()}_{slug(doc.nosaukums)}"

    teikumi = veidot_teikumus(doc)
    for t in teikumi:
        if t.virsraksts:
            t.klausisanai = "" if REF_HEADINGS.match(t.orig) else t.orig
        else:
            t.klausisanai = klausisanai_noteikumi(t.orig, valoda)
    zinot(f"  Valoda: {valoda}; rindkopas: {len(doc.rindkopas)}; teikumi: {len(teikumi)}; "
          f"atsauču saraksts: {len(doc.atsauces)} ieraksti")

    claude_lietots = False
    if not a.bez_claude and env.get("ANTHROPIC_API_KEY"):
        zinot(f"Sagatavoju tekstu klausīšanai ar Claude ({a.modelis})")
        claude_lietots = claude_sagatavot(teikumi, valoda, env["ANTHROPIC_API_KEY"], a.modelis) > 0
    elif not a.bez_claude:
        zinot("  Claude atslēga nav atrasta, tekstu sagatavoju tikai ar noteikumiem.")

    orig_garums = sum(len(t.orig) for t in teikumi)
    klaus_garums = sum(len(t.klausisanai) for t in teikumi)
    attieciba = klaus_garums / max(1, orig_garums)

    mape = a.izvade or noklusejuma_izvade()
    (mape / "teksti").mkdir(parents=True, exist_ok=True)
    (mape / "teksti" / f"{tid}_originals.md").write_text(originals_md(doc, apa, teikumi), encoding="utf-8")
    (mape / "teksti" / f"{tid}_klausisanai.txt").write_text(
        "\n".join(("\n" if t.virsraksts else "") + t.klausisanai + ("\n" if t.rindkopas_beigas else "")
                  for t in teikumi if t.klausisanai), encoding="utf-8")
    zinot(f"  Klausīšanās teksts ir {attieciba:.0%} no oriģināla garuma ({klaus_garums:,} rakstzīmes)".replace(",", " "))
    if attieciba < 0.75:
        zinot("  UZMANĪBU: klausīšanās teksts ir daudz īsāks par oriģinālu. Pārbaudi teksta failu.")

    if a.tikai_teksts:
        zinot(f"\nGatavs (tikai teksts): {mape / 'teksti'}")
        return

    if valoda not in BALSIS:
        kluda("Neatbalstīta valoda")
    vards = (a.balss or NOKLUSETA_BALSS[valoda]).lower()
    balss_nos = BALSIS[valoda].get(vards, a.balss if a.balss and "Neural" in a.balss else None)
    if not balss_nos:
        kluda(f"Nezināma balss '{a.balss}'. Iespējas: {', '.join(BALSIS[valoda])}")
    if a.testa_balss:
        balss = TestaBalss()
    else:
        if not env.get("AZURE_SPEECH_KEY") or not env.get("AZURE_SPEECH_REGION"):
            kluda(f"Azure atslēga nav atrasta failā {ENV_FILE}")
        balss = AzureBalss(env["AZURE_SPEECH_KEY"], env["AZURE_SPEECH_REGION"], balss_nos, valoda)

    zinot(f"Veidoju audio ar balsi {balss_nos}")
    pcm = sintezet_visu(teikumi, balss)
    audio_cels = saglabat_audio(pcm, mape / tid)
    ilgums = len(pcm) / (2 * SAMPLE_RATE)

    sadalas = [{"virsraksts": t.orig, "sakums_s": t.sakums_s, "n": t.n} for t in teikumi if t.virsraksts and t.klausisanai]
    karte = {
        "id": tid, "versija": VERSIJA, "izveidots": datetime.now().isoformat(timespec="seconds"),
        "nosaukums": doc.nosaukums, "apa": apa, "doi": doc.doi, "avota_fails": a.fails.name,
        "valoda": valoda, "balss": balss_nos, "claude": claude_lietots,
        "audio": audio_cels.name, "ilgums_s": round(ilgums, 2),
        "sadalas": sadalas,
        "teikumi": [{
            "n": t.n, "sakums_s": t.sakums_s, "beigas_s": t.beigas_s, "teksts": t.klausisanai,
            "orig": t.orig, "orig_rindkopa": t.rindkopa, "orig_teikums": t.teikums,
            "lappuse": t.lappuse, "virsraksts": t.virsraksts,
        } for t in teikumi if t.klausisanai],
    }
    (mape / f"{tid}.json").write_text(json.dumps(karte, ensure_ascii=False, indent=1), encoding="utf-8")
    atjaunot_katalogu(mape, {"id": tid, "nosaukums": doc.nosaukums, "audio": audio_cels.name,
                             "karte": f"{tid}.json", "ilgums_s": round(ilgums, 1), "valoda": valoda})

    zinot(f"\nGatavs. Ilgums {fmt_laiks(ilgums)}.")
    zinot(f"  Audio:  {audio_cels}")
    zinot(f"  Karte:  {mape / (tid + '.json')}")
    zinot(f"  Teksti: {mape / 'teksti'}")
    if str(mape).startswith(str(ICLOUD)):
        zinot("  Faili ir iCloud Drive mapē Klausisanas, iPhone tie būs redzami lietotnē Faili.")


if __name__ == "__main__":
    main()
