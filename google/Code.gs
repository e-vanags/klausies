/**
 * Klausīšanās asistents: Google dokumentu daļa.
 *
 * Saņem datus no Mac skripta (klausies piezimes) un raksta tos Google Drive mapē "Klausisanas":
 *   Raksti/<raksts>          pilns raksta teksts, piezīmes pie teikumiem, kopsavilkums sākumā
 *   Projekti/Projekts: <..>  piezīmes ar citātiem un atsaucēm konkrētai publikācijai
 *   Ideju iesūtne            brīvās idejas
 *   Lasāmo rinda             raksti, ko lūdzi atrast
 *
 * Uzstādīšana: Project Settings → Script properties → SECRET = tava slepenā frāze.
 * Publicēšana: Deploy → New deployment → Web app, Execute as: Me, Who has access: Anyone.
 */

var SAKNE = "Klausisanas";
var TUKSS = "Vēl nav piezīmju.";
var KRASA_TEIKUMS = "#FFF0A0";
var KRASA_PIEZIME = "#DDEBFF";

function doPost(e) {
  try {
    var d = JSON.parse(e.postData.contents);
    var slepens = PropertiesService.getScriptProperties().getProperty("SECRET");
    if (!slepens || !d || d.secret !== slepens) return atbilde({ ok: false, kluda: "nepareiza slepenā frāze" });
    var lock = LockService.getScriptLock();
    lock.waitLock(30000);
    try {
      if (d.action === "ping") return atbilde({ ok: true, mape: mape_(SAKNE).getUrl() });
      if (d.action === "raksts") return atbilde({ ok: true, url: rakstaDokuments_(d).getUrl() });
      if (d.action === "piezime") return atbilde({ ok: true, saites: piezime_(d) });
      return atbilde({ ok: false, kluda: "nezināma darbība " + d.action });
    } finally {
      lock.releaseLock();
    }
  } catch (err) {
    return atbilde({ ok: false, kluda: String((err && err.message) || err) });
  }
}

function doGet() {
  return atbilde({ ok: true, info: "Klausīšanās asistents darbojas. Dati tiek sūtīti ar POST." });
}

function atbilde(o) {
  return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
}

/* ---------- mapes un dokumenti ---------- */

function mape_(nosaukums, vecaks) {
  var v = vecaks || DriveApp.getRootFolder();
  var it = v.getFoldersByName(nosaukums);
  return it.hasNext() ? it.next() : v.createFolder(nosaukums);
}

function dokuments_(nosaukums, folder, atslega, sagatavot) {
  var props = PropertiesService.getScriptProperties();
  var id = props.getProperty(atslega);
  if (id) {
    try {
      var esoss = DocumentApp.openById(id);
      if (!DriveApp.getFileById(id).isTrashed()) return esoss;
    } catch (e) { /* dokuments dzēsts, veidosim jaunu */ }
  }
  var doc = DocumentApp.create(nosaukums);
  DriveApp.getFileById(doc.getId()).moveTo(folder);
  props.setProperty(atslega, doc.getId());
  var b = doc.getBody();
  b.clear();
  sagatavot(b);
  nonemtTuksoSakumu_(b);
  doc.saveAndClose();
  return DocumentApp.openById(doc.getId());
}

function nonemtTuksoSakumu_(b) {
  if (b.getNumChildren() > 1) {
    var c = b.getChild(0);
    if (c.getType() === DocumentApp.ElementType.PARAGRAPH && c.asParagraph().getText() === "") c.removeFromParent();
  }
}

/* ---------- formatēšana ---------- */

function stils_(el, o) {
  o = o || {};
  if (el.setHeading && !o.virsraksts) el.setHeading(DocumentApp.ParagraphHeading.NORMAL);
  if (o.virsraksts && el.setHeading) el.setHeading(o.virsraksts);
  var t = el.editAsText();
  if (t.getText().length === 0) return el;
  t.setBold(!!o.trekns).setItalic(!!o.slipi).setFontSize(o.izmers || 11)
    .setForegroundColor(o.krasa || "#000000").setBackgroundColor(o.fons || null)
    .setFontFamily(o.fonts || "Arial").setLinkUrl(null);
  if (o.saite) t.setLinkUrl(o.saite);
  return el;
}

function rindkopa_(b, teksts, o) { return stils_(b.appendParagraph(teksts), o); }
function ievietot_(b, idx, teksts, o) { return stils_(b.insertParagraph(idx, teksts), o); }
function virsraksts_(b, teksts, limenis) { return rindkopa_(b, teksts, { virsraksts: limenis }); }

var H = DocumentApp.ParagraphHeading;

/* ---------- raksta dokuments ---------- */

function rakstaDokuments_(d) {
  var raksti = mape_("Raksti", mape_(SAKNE));
  var nos = (d.nosaukums || d.id).slice(0, 120);
  return dokuments_(nos, raksti, "raksts_" + d.id, function (b) {
    virsraksts_(b, nos, H.TITLE);
    if (d.apa) rindkopa_(b, d.apa);
    else if (d.doi) rindkopa_(b, "https://doi.org/" + d.doi, { saite: "https://doi.org/" + d.doi });
    rindkopa_(b, "Izveidots " + Utilities.formatDate(new Date(), "Europe/Riga", "yyyy-MM-dd") + " ar klausīšanās asistentu", { izmers: 9, krasa: "#777777" });
    virsraksts_(b, "Manas piezīmes", H.HEADING1);
    rindkopa_(b, TUKSS, { slipi: true, krasa: "#777777" });
    virsraksts_(b, "Teksts", H.HEADING1);
    var lpp = null;
    (d.rindkopas || []).forEach(function (r) {
      if (!r.t) return;
      if (r.lpp && r.lpp !== lpp) {
        lpp = r.lpp;
        rindkopa_(b, "[lpp. " + lpp + "]", { izmers: 8, krasa: "#999999" });
      }
      if (r.izl === "kods") {
        r.t.split("\n").forEach(function (l) { rindkopa_(b, l || " ", { fonts: "Courier New", izmers: 9, krasa: "#444444" }); });
      } else if (r.v) {
        virsraksts_(b, r.t, H.HEADING2);
      } else if (r.izl) {
        rindkopa_(b, r.t, { izmers: 9, krasa: "#888888" });
      } else {
        rindkopa_(b, r.t);
      }
    });
    if (d.atsauces && d.atsauces.length) {
      virsraksts_(b, "Atsauces", H.HEADING1);
      d.atsauces.forEach(function (a) { rindkopa_(b, a, { izmers: 10 }); });
    }
  });
}

function atrastVirsrakstu_(b, teksts) {
  for (var i = 0; i < b.getNumChildren(); i++) {
    var c = b.getChild(i);
    if (c.getType() === DocumentApp.ElementType.PARAGRAPH && c.asParagraph().getText() === teksts &&
        c.asParagraph().getHeading() === H.HEADING1) return i;
  }
  return -1;
}

function regex_(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

function atrastTeikumu_(b, teikums) {
  if (!teikums) return null;
  var garumi = [120, 70, 40];
  for (var i = 0; i < garumi.length; i++) {
    var gab = teikums.slice(0, garumi[i]);
    if (gab.length < 15) break;
    var r = b.findText(regex_(gab));
    if (r) return { r: r, garums: Math.min(teikums.length, r.getElement().asText().getText().length - r.getStartOffset()) };
  }
  return null;
}

function ievietotRaksta_(doc, d) {
  var b = doc.getBody();
  var atr = atrastTeikumu_(b, d.teikums);
  var piezimesTeksts = "Piezīme · " + d.veids + " · " + d.laiks + ": " + d.piezime;
  if (atr) {
    var t = atr.r.getElement().asText();
    var s = atr.r.getStartOffset();
    t.setBackgroundColor(s, s + atr.garums - 1, KRASA_TEIKUMS);
    var p = t.getParent();
    while (p && p.getType() !== DocumentApp.ElementType.PARAGRAPH && p.getType() !== DocumentApp.ElementType.LIST_ITEM) p = p.getParent();
    var idx = b.getChildIndex(p);
    // ja aiz rindkopas jau ir piezīmes, jaunā nāk aiz tām
    while (idx + 1 < b.getNumChildren() && b.getChild(idx + 1).getType() === DocumentApp.ElementType.PARAGRAPH &&
           /^(Piezīme · |Ierunāts: )/.test(b.getChild(idx + 1).asParagraph().getText())) idx++;
    ievietot_(b, idx + 1, piezimesTeksts, { fons: KRASA_PIEZIME, trekns: false });
    ievietot_(b, idx + 2, "Ierunāts: " + d.transkripcija, { izmers: 8, krasa: "#666666", fons: KRASA_PIEZIME });
  }
  var v = atrastVirsrakstu_(b, "Manas piezīmes");
  if (v >= 0) {
    var nak = b.getChild(v + 1);
    if (nak && nak.getType() === DocumentApp.ElementType.PARAGRAPH && nak.asParagraph().getText() === TUKSS) nak.removeFromParent();
    var teksts = (d.lappuse ? "lpp. " + d.lappuse + " · " : "") + d.veids + (d.projekts ? " · " + d.projekts : "") +
      ": " + d.piezime + (atr ? "" : " (vieta tekstā nav atrasta)");
    var li = b.insertListItem(v + 1, teksts);
    li.setGlyphType(DocumentApp.GlyphType.BULLET);
    stils_(li, {});
  }
  return !!atr;
}

/* ---------- piezīme ---------- */

function piezime_(d) {
  var sakne = mape_(SAKNE);
  var props = PropertiesService.getScriptProperties();
  var saites = {};
  var rakstaUrl = null;

  if (d.teksta_id) {
    var id = props.getProperty("raksts_" + d.teksta_id);
    if (id) {
      var doc = DocumentApp.openById(id);
      ievietotRaksta_(doc, d);
      doc.saveAndClose();
      rakstaUrl = "https://docs.google.com/document/d/" + id + "/edit";
      saites["Raksta dokuments"] = rakstaUrl;
    }
  }

  if (d.projekts) {
    var proj = mape_("Projekti", sakne);
    var pdoc = dokuments_("Projekts: " + d.projekts, proj, "projekts_" + d.projekts.toLowerCase().trim(), function (b) {
      virsraksts_(b, "Projekts: " + d.projekts, H.TITLE);
      rindkopa_(b, "Piezīmes no klausītajiem tekstiem. Jaunākās apakšā.", { izmers: 9, krasa: "#777777" });
    });
    var pb = pdoc.getBody();
    virsraksts_(pb, d.datums + " · " + d.veids + " · " + (d.nosaukums || "bez teksta"), H.HEADING3);
    if (d.teikums) rindkopa_(pb, "„" + d.teikums + "” " + (d.isa_atsauce || ""), { slipi: true, krasa: "#333333" });
    rindkopa_(pb, d.piezime);
    if (d.apa) rindkopa_(pb, d.apa, { izmers: 9, krasa: "#666666" });
    if (rakstaUrl) rindkopa_(pb, "Atvērt raksta dokumentu", { izmers: 9, saite: rakstaUrl });
    pdoc.saveAndClose();
    saites["Projekts " + d.projekts] = pdoc.getUrl();
  }

  if (d.ideja) {
    var idoc = dokuments_("Ideju iesūtne", sakne, "idejas", function (b) {
      virsraksts_(b, "Ideju iesūtne", H.TITLE);
    });
    var ib = idoc.getBody();
    virsraksts_(ib, d.datums, H.HEADING3);
    rindkopa_(ib, d.piezime);
    if (d.nosaukums) rindkopa_(ib, "Klausoties: " + d.nosaukums + " (" + d.laiks + ")", { izmers: 9, krasa: "#666666", saite: rakstaUrl });
    idoc.saveAndClose();
    saites["Ideju iesūtne"] = idoc.getUrl();
  }

  if (d.lasamais && d.lasamais.atsauce) {
    var ldoc = dokuments_("Lasāmo rinda", sakne, "lasamie", function (b) {
      virsraksts_(b, "Lasāmo rinda", H.TITLE);
    });
    var lb = ldoc.getBody();
    virsraksts_(lb, d.datums + " · jāiegūst", H.HEADING3);
    rindkopa_(lb, d.lasamais.atsauce);
    if (d.lasamais.pamatojums) rindkopa_(lb, "Kāpēc: " + d.lasamais.pamatojums, { slipi: true });
    if (d.nosaukums) rindkopa_(lb, "Atrasts rakstā: " + d.nosaukums, { izmers: 9, krasa: "#666666", saite: rakstaUrl });
    ldoc.saveAndClose();
    saites["Lasāmo rinda"] = ldoc.getUrl();
  }
  return saites;
}

/** Palaid šo funkciju vienu reizi redaktorā, lai piešķirtu atļaujas un pārbaudītu, ka viss strādā. */
function parbaude() {
  var m = mape_(SAKNE);
  Logger.log("Mape: " + m.getUrl());
  Logger.log("SECRET iestatīts: " + !!PropertiesService.getScriptProperties().getProperty("SECRET"));
}
