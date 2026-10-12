# Klausīšanās asistents · 1. posma prototips

Šis prototips pārbauda lielāko risku: vai iPhone tīmekļa lietotne automašīnā spēj atskaņot audio, ar lielu pogu ierakstīt balss piezīmi un pēc tam pati atsākt atskaņošanu.

Prototipā vēl nav runas sintēzes, Google dokumentu un runas atpazīšanas. Tās nāks 2. un 3. posmā.

## Kas ir mapē

| Fails | Nozīme |
| --- | --- |
| `index.html` | Visa lietotne vienā failā |
| `manifest.webmanifest` | Ļauj pievienot lietotni sākuma ekrānam |
| `icon-180.png`, `icon-512.png` | Ikonas |

## Kā to palaist iPhone (apmēram 10 minūtes)

Mikrofons darbojas tikai drošā (HTTPS) adresē, tāpēc failus vajag mitināt. Bez maksas to var izdarīt GitHub Pages.

1. Piesakies github.com (ja konta nav, izveido).
2. Izveido jaunu repozitoriju, piemēram, `klausies`. Tam jābūt publiskam (Public), jo bezmaksas GitHub Pages darbojas publiskiem repozitorijiem. Lietotnē nav neviena tava teksta vai ieraksta, tie glabājas tikai telefonā.
3. Repozitorijā izvēlies "Add file", tad "Upload files" un augšupielādē četrus failus: `index.html`, `manifest.webmanifest`, `icon-180.png`, `icon-512.png`.
4. Atver "Settings", tad "Pages". Sadaļā "Branch" izvēlies `main` un mapi `/ (root)`, saglabā.
5. Pēc 1 līdz 2 minūtēm lapa būs pieejama adresē `https://<tavs-lietotājvārds>.github.io/klausies/`.
6. Atver šo adresi iPhone Safari, nospied "Kopīgot", tad "Pievienot sākuma ekrānam".
7. Atver lietotni no sākuma ekrāna ikonas un izvēlies audio failu. Der jebkurš MP3, piemēram, podkāsta epizode vai Natural Reader eksports. Fails saglabājas telefonā, tāpēc automašīnā tas vairs nav jāizvēlas.
8. Pirmajā piezīmē iPhone jautās atļauju izmantot mikrofonu. Atļauj.

## Kā pārbaudīt

Lietotnē ir sadaļa "Pārbaudes saraksts" ar astoņām pārbaudēm. Pēc brauciena katrai izvēlies "darbojas", "nedarbojas" vai "daļēji". Tad nospied "Kopēt pārbaudes atskaiti" un ielīmē to sarunā ar Claude. Atskaitē ir arī žurnāls, kurā redzams, cik ātri atveras mikrofons, vai atskaņošana atsākās un vai mainījās audio ierīces.

Ieteicamā pārbaude:

1. Mājās: divas piezīmes ar noklusējuma iestatījumiem.
2. Automašīnā, telefons turētājā, savienots ar automašīnu: 3 līdz 5 piezīmes, katra 10 līdz 40 sekundes.
3. Otrajā braucienā iestatījumos ieslēdz "Turēt mikrofonu atvērtu starp piezīmēm" un salīdzini skaņas kvalitāti.
4. Piezīmes noklausies pēc brauciena. Tās var nosūtīt uz datoru ar "Kopīgot", lai vēlāk izmēģinātu runas atpazīšanu.

## Pogas lietošana

- Īss spiediens: atskaņošana apstājas, signāls, ieraksts sākas.
- Vēl viens īss spiediens: ieraksts beidzas, divi signāli, atskaņošana turpinās no vietas 5 sekundes agrāk.
- Ilgs spiediens ieraksta laikā: piezīme tiek atcelta (divi zemi signāli).
- Ja pogu aizmirst nospiest: ieraksts beidzas pats pēc 30 sekunžu klusuma vai 5 minūtēm.
- Datorā: atstarpe sāk vai beidz piezīmi, P atskaņo vai aptur, Esc atceļ.

## Drošība

Braucot piezīmei pietiek ar vienu spiedienu uz lielo pogu, kas aizņem pusi ekrāna. Faila izvēli, iestatījumus un pārbaudes saraksta aizpildīšanu dari tikai stāvot.
