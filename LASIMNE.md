# Klausīšanās asistents

Personīgs rīks zinātnisku rakstu klausīšanai ar balss piezīmēm. Specifikācija: `klausisanas_riks_specifikacija_v0.2.md`.

| Daļa | Fails | Statuss |
| --- | --- | --- |
| Lietotne iPhone un datorā | `index.html` | 1. posms darbojas, 2. posmā pievienota laika karte |
| Teksta sagatavošana un audio | `klausies.py` | 2. posms |
| Piezīmes Google dokumentā | | 3. posms |

Lietotne: https://e-vanags.github.io/klausies/

## Skripta uzstādīšana Mac (vienu reizi)

```bash
cd ~/Projects/klausies
git pull
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
echo "alias klausies='~/Projects/klausies/.venv/bin/python ~/Projects/klausies/klausies.py'" >> ~/.zshrc
source ~/.zshrc
```

Atslēgas glabājas failā `~/.config/klausies/env` ārpus repozitorija:

```
AZURE_SPEECH_KEY=...
AZURE_SPEECH_REGION=westeurope
ANTHROPIC_API_KEY=...
```

## Lietošana

```bash
klausies ~/Downloads/raksts.pdf --tikai-teksts   # tikai teksts, bez maksas, lai pārbaudītu
klausies ~/Downloads/raksts.pdf                  # teksts un audio
klausies raksts.pdf --balss everita              # cita balss
klausies raksts.pdf --bez-claude                 # teksta sagatavošana tikai ar noteikumiem
```

Rezultāti nonāk iCloud Drive mapē `Klausisanas`:

| Fails | Nozīme |
| --- | --- |
| `<id>.m4a` | Audio |
| `<id>.json` | Laika karte: katrs teikums ar laiku, oriģinālo tekstu, rindkopu un lappusi |
| `teksti/<id>_originals.md` | Oriģinālais teksts ar lappusēm un atsauču sarakstu |
| `teksti/<id>_klausisanai.txt` | Teksts, kas tika nolasīts |

Lietotnē nospied "Izvēlēties audio un karti", atver mapi Klausisanas un atzīmē **abus** failus (`.m4a` un `.json`).

## Teksta sagatavošana

1. Teksts tiek izvilkts no PDF; galvenes, kājenes un lappušu numuri tiek izņemti, rindkopas savienotas pāri lappusēm.
2. Atsauču saraksts tiek atdalīts un saglabāts oriģinālā, bet netiek nolasīts.
3. Noteikumi izņem atsauces iekavās, numurētas atsauces, saites un atšifrē saīsinājumus.
4. Claude sagatavo katru teikumu izrunai (personvārdi, skaitļi, statistika). Teikumu numerācija saglabājas, tāpēc katrs nolasītais teikums ir sasaistīts ar oriģinālo. Ja Claude versija ir aizdomīgi īsa, tiek izmantota noteikumu versija.
5. Azure sintezē audio daļās, un laika atzīmes katra teikuma sākumā dod precīzu laika karti.

## Pogas lietošana lietotnē

- Īss spiediens: atskaņošana apstājas, signāls, ieraksts sākas.
- Vēl viens īss spiediens: ieraksts beidzas, divi signāli, atskaņošana turpinās no vietas 5 sekundes agrāk.
- Ilgs spiediens ieraksta laikā: piezīme tiek atcelta.
- Ja pogu aizmirst nospiest: ieraksts beidzas pats pēc 30 sekunžu klusuma vai 5 minūtēm.
- Datorā: atstarpe sāk vai beidz piezīmi, P atskaņo vai aptur, Esc atceļ.

Ja ir ielādēta laika karte, piezīme tiek piesaistīta teikumam, kas skanēja 3 sekundes pirms pogas spiediena.

## Drošība

Braucot piezīmei pietiek ar vienu spiedienu uz lielo pogu. Failu izvēli un iestatījumus maini tikai stāvot.

## Piezīmju apstrāde (3. posms)

1. iPhone: sadaļā Piezīmes nospied **Sūtīt jaunās piezīmes**, izvēlies **Saglabāt failos** un mapi `Klausisanas/piezimes`.
2. Mac: `klausies piezimes`
3. Rezultāts Google Drive mapē `Klausisanas`: raksta dokuments ar piezīmēm pie teikumiem, projektu dokumenti, Ideju iesūtne, Lasāmo rinda. Rezerves kopija: `Klausisanas/piezimes/*_piezimes.md`.

Google daļa ir `google/Code.gs` (Google Apps Script tīmekļa lietotne). Mac atslēgu failā vajag:

```
KLAUSIES_GAS_URL=https://script.google.com/macros/s/.../exec
KLAUSIES_GAS_SECRET=...
```

Pārbaude: `klausies piezimes --tests`
