# Datenformate

## XML-Datensatz

Alle Daten eines Benutzers stehen in einem einzigen XML-Dokument. In allen Speicherarten ist es dasselbe Format: In der Datenbank liegt es in `daten.xml`, im Modus `xml` in einer Datei.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<koerperdaten version="1" gespeichert="2026-11-02T06:12:00.000Z">
  <einstellungen>
    <koerpergroesse_cm>182</koerpergroesse_cm>
    <geschlecht>m</geschlecht>                  <!-- m, f oder leer -->
    <geburtsdatum>1980-04-12</geburtsdatum>
    <csv_trennzeichen>;</csv_trennzeichen>
    <csv_berechnete_spalten>nein</csv_berechnete_spalten>
    <ausgeblendet><messgroesse>bmi</messgroesse>…</ausgeblendet>
  </einstellungen>
  <messgroessen>
    <messgroesse schluessel="weight" name="Gewicht" einheit="kg" nachkommastellen="1" csv="gewicht_kg"/>
    <messgroesse schluessel="blutdruck" name="Blutdruck sys." einheit="mmHg" nachkommastellen="0" csv="blutdruck" eigene="ja"/>
  </messgroessen>
  <ansicht><![CDATA[{"fv":4,"design":"modern","mdash":{…},"charts":[…],"dash":{…},"view":{…},
                     "goals":[…],"formulas":{…},"derivedCustom":[…]}]]></ansicht>
  <eintraege anzahl="412">
    <eintrag datum="2026-10-05" weight="91.4" waist="101"/>
    <eintrag datum="2026-10-06" weight="91.1"><anmerkung>nach dem Lauf</anmerkung></eintrag>
  </eintraege>
</koerperdaten>
```

**Regeln:**
- Je Tag gibt es höchstens einen Eintrag. Die Werte stehen als Attribute, benannt nach dem `key` der Messgröße, mit Dezimalpunkt.
- Gespeichert werden nur eingegebene Werte. Berechnete Werte entstehen beim Laden neu.
- Strukturierte Einstellungen liegen als JSON im CDATA-Block `<ansicht>`. So lassen sich neue Einstellungen ergänzen, ohne das XML-Schema zu ändern.
- Der Server prüft nur, ob das Dokument wohlgeformt ist und `<koerperdaten>` als Wurzel hat (`validate_xml`). Das Format selbst gehört allein dem Tracker.

### Einstellungen (`S.settings`)

| Feld | Bedeutung |
|---|---|
| `fv` | Stand des Einstellungsformats (`SETTINGS_FORMAT`, derzeit 4) |
| `height`, `sex`, `birth` | Körpergröße in cm, Geschlecht, Geburtsdatum |
| `delim`, `exportDerived` | CSV-Trennzeichen, berechnete Spalten exportieren |
| `custom` | eigene gespeicherte Messgrößen |
| `hidden` | Messgrößen, die im Erfassen-Formular unter „Weitere Werte“ stehen |
| `derivedCustom` | eigene berechnete Werte mit Formel |
| `formulas` | geänderte Standardformeln, Schlüssel → Formel |
| `goals` | Ziele: `{key, target, start:{v, d}, …}` |
| `design` | `modern` oder `classic` |
| `mdash` | moderne Übersicht: `{hide, trio, week}` |
| `charts` | Diagramme: `{id, title, metrics, type: line\|energy, band, smooth, goals, colors}` |
| `dash` | klassische Übersicht: `{tiles, trend}` |
| `view` | Zeitraum der Diagramme: `{preset, from, to, gran}` |

### Umstellung älterer Stände (`migrate`)

Beim Laden bringt der Tracker ältere Stände auf das aktuelle Format und speichert sie danach einmal. Ältere Datensätze bleiben damit immer lesbar.

| Von `fv` | Umstellung |
|---|---|
| < 3 (bis 2.2) | `burnMode=active`: `kcalOut` wird zu `kcalActive`; Kalorien-Diagramme werden zum Typ `energy`; die Kacheln `kcalActive` und `kcalBurn` kommen dazu; ist `hidden` leer, gilt `DEFAULT_HIDDEN` |
| < 4 (bis 2.7) | `heightCm` kommt zu `hidden` |

Neue Umstellungen bekommen eine höhere `SETTINGS_FORMAT`-Nummer und einen eigenen `if (fv0 < N)`-Block. Bestehende Blöcke bleiben unverändert.

## Geräte-Einstellungen (`cfg`)

Diese Einstellungen liegen im `localStorage` unter `koerperdaten-config` und gelten nur für das jeweilige Gerät: Speicherart, Server-Adresse, Token, letzter Benutzer, `theme`, `detailPreset` und Ähnliches.

## Offline-Kopie

Schlüssel im `localStorage`: `koerperdaten-offline|<Server-Adresse>|<Benutzer>`

```json
{ "v": 1, "user": "…", "admin": false, "at": 1764000000000, "etag": "\"…\"", "info": {…},
  "from": "2026-10-26",
  "rows": { "2026-11-24": {"weight": 88.2}, … },
  "base": { "2026-11-24": "{\"weight\":88.4}", … },
  "settings": {…}, "baseSettings": "…" }
```

`rows` und `settings` sind der aktuelle Stand auf dem Gerät. `base` und `baseSettings` sind der zuletzt vom Server bestätigte Stand, als stabiles JSON (`stable()`). Wie beides beim Abgleich zusammenwirkt, steht unter [Offline und Synchronisierung](offline.md).

## CSV

**Export:**
- erste Spalte `datum` (JJJJ-MM-TT), dann je Messgröße mit Werten die Spalte `csv`, optional die berechneten Werte, am Ende `notiz`
- Trennzeichen `;` mit Dezimalkomma oder `,` mit Dezimalpunkt
- UTF-8 mit BOM (für Excel), Zeilenende CRLF

**Import:**
- Erkannte Datumsformate: `JJJJ-MM-TT` (auch mit Uhrzeit, z. B. `2026-10-05T05:09:42+02:00` aus WeightFit), `TT.MM.JJJJ`, `TT.MM.JJ`, `JJJJ/MM/TT`.
- Spaltennamen werden normalisiert: Kleinbuchstaben, Umlaute werden zu ae, oe, ue, ss, Einheiten in `(…)` oder `[…]` werden entfernt. Danach werden sie mit `csv`, `key`, `label` und `alias` verglichen. `Weight (kg)` wird so zu `weight`.
- Spalten mit Namen wie `notiz`, `note`, `kommentar` oder `tags` werden zur Anmerkung.
- Spalten berechneter Werte werden beim Import übersprungen.
- **Vorzeichen der Kalorienbilanz:** Seit 2.3.0 bedeutet negativ ein Defizit. Ältere Exporte haben das umgekehrte Vorzeichen, die Spalte wird aber ohnehin neu berechnet.

## Messgrößen (Standard)

| key | Formelname | CSV-Spalte | Einheit |
|---|---|---|---|
| weight | gewicht | gewicht_kg | kg |
| waist | taille | taille_cm | cm |
| belly | bauch | bauch_cm | cm |
| hip | huefte | huefte_cm | cm |
| bodyfat | koerperfett | koerperfett_prozent | % |
| bmi | bmi | bmi | kg/m² |
| muscle | muskelmasse | muskelmasse_kg | kg |
| water | wasser | koerperwasser_prozent | % |
| visceral | viszeralfett | viszeralfett | Stufe |
| chest | brust | brust_cm | cm |
| arm | oberarm | oberarm_cm | cm |
| heightCm | groesse_gemessen | koerpergroesse_cm | cm |
| thigh | oberschenkel | oberschenkel_cm | cm |
| rhr | ruhepuls | ruhepuls_bpm | bpm |
| steps | schritte | schritte | Schritte |
| sleep | schlaf | schlaf_h | h |
| kcalIn | aufnahme | kalorien_aufnahme_kcal | kcal |
| kcalActive | aktivitaet | aktivitaetskalorien_kcal | kcal |
| kcalOut | verbrauch_gemessen | kalorien_verbrauch_kcal | kcal |

**Berechnet:**

| key | Formelname | CSV-Spalte |
|---|---|---|
| whr | whr | whr |
| whtr | whtr | whtr |
| bmr | grundumsatz | grundumsatz_kcal |
| kcalBurn | verbrauch | kalorienverbrauch_gesamt_kcal |
| balance | bilanz | kalorienbilanz_kcal |
| fatloss | fettverlust | fettverlust_theor_g |

Die Schlüssel (`key`) sind Teil des gespeicherten Formats. Sie werden nie umbenannt.
