# Heat Pump Performance

[English](README.md) | **Deutsch**

**Aktuelle Version: 0.2.0**

Repository: https://github.com/vandecook/home-assistant-heat-pump-performance

Issues: https://github.com/vandecook/home-assistant-heat-pump-performance/issues

Benutzerdefinierte Home-Assistant-Integration, die Arbeitszahlen (COP / JAZ) einer
Wärmepumpe aus kumulierten Energiezählern berechnet. Die Wärmeenergie kann von
einem **Wärmemengenzähler** kommen (Standard) oder **in der Integration selbst
berechnet** werden, aus Durchfluss und Vor-/Rücklauftemperatur. Beide Wege laufen
nebeneinander und werden gegeneinander geprüft.

Es sind keine Template-Sensoren, Helfer oder Utility-Meter nötig.

## Inhalt

- [Eingänge](#eingänge)
- [Wärmeenergie: Wärmemengenzähler oder Berechnung](#wärmeenergie-wärmemengenzähler-oder-berechnung)
- [Arbeitszahlen](#arbeitszahlen)
- [Abtauen](#abtauen)
- [Plausibilitätsprüfungen und Diagnose](#plausibilitätsprüfungen-und-diagnose)
- [Entitäten](#entitäten)
- [Einrichtung, Zeiträume und Speicherung](#einrichtung-zeiträume-und-speicherung)
- [Installation](#installation)
- [Wartungsaktionen](#wartungsaktionen)
- [Änderungsverlauf](#änderungsverlauf)

## Eingänge

Numerische Eingänge können `sensor`-, `input_number`- oder `number`-Entitäten sein.
Sie brauchen **keine** Home-Assistant-Energie-`device_class`, keine `state_class`
und nicht einmal eine `unit_of_measurement`; die Einheit wird im Konfigurationsdialog
ausdrücklich gewählt. Betriebsflags sind nicht eingeschränkt (`binary_sensor`,
`input_boolean`, `switch`, herstellerspezifische Entitäten). Alle Eingangs-Entitäten
und Einheiten lassen sich später in den Optionen ändern.

| Eingang | Pflicht | Zweck |
| --- | --- | --- |
| Wärmeenergie (kumuliert) | ja | Zählerstand des Wärmemengenzählers; auch Gegenprobe |
| Elektrische Energie, Gesamtsystem (kumuliert) | ja | Brutto-COP |
| Elektrische Energie, Steuerung und Lüftung (kumuliert) | nein | Netto-COP (wird vom Gesamtwert abgezogen) |
| Elektrische Leistung, Gesamtsystem / Steuerung | nein | für später gespeichert; die Arbeitszahl nutzt die Energiezähler |
| Heizen aktiv / Warmwasser aktiv | nein | Aufteilung in Heizen und Warmwasser |
| Abtauen aktiv | nein | optionale Gegenprüfung der erkannten Abtauvorgänge, siehe [docs/defrost.de.md](docs/defrost.de.md) |
| Durchfluss (m³/h, l/min, l/h) | nein* | berechnete Wärme |
| Vorlauftemperatur | nein* | berechnete Wärme |
| Rücklauftemperatur | nein* | berechnete Wärme |
| Außentemperatur | nein | nur für den Referenz-COP des Herstellers |

\* Durchfluss, Vorlauf- und Rücklauftemperatur müssen **gemeinsam** konfiguriert
werden. Ohne sie verhält sich die Integration genau wie 0.1.14.

Alle Energiewerte werden intern auf kWh normiert, Leistungen auf kW.

## Wärmeenergie: Wärmemengenzähler oder Berechnung

Der Zählerstand des Wärmemengenzählers wird immer geführt und immer gefiltert
(Spike-Schutz, siehe unten). Sobald Durchfluss, Vorlauf und Rücklauf konfiguriert
sind, wird die gelieferte Wärme zusätzlich berechnet:

`P [kW] = Durchfluss [m³/h] × 1,163 × (Vorlauf − Rücklauf) [K]`

Die Leistung ist vorzeichenbehaftet und wird über die Zeit integriert
(Trapezregel, exakte Aufteilung bei Nulldurchgängen). Du wählst, welche Energie in
die Arbeitszahl eingeht:

| Modus | Verwendete Wärmeenergie |
| --- | --- |
| `meter` (Standard) | Zählerstand des Wärmemengenzählers, wie in 0.1.14 |
| `meter_minus_defrost` | Wärmemengenzähler abzüglich des berechneten Abtauverlusts |
| `calculated` | vollständig berechnet, gelieferte Wärme abzüglich Abtauverlust |

Ohne die drei Hydraulik-Eingänge fällt der Modus auf `meter` zurück.

Warum überhaupt berechnen? Viele Wärmemengenzähler zählen nur in ganzen kWh,
ignorieren den negativen Durchfluss beim Abtauen und melden gelegentlich Ausreißer.
Die Berechnung hat eine deutlich feinere Auflösung und enthält den Abtauverlust.
Über längere Zeiträume ist sie deshalb meist der genauere Wert. Der Vergleich beider
Werte steht in der Diagnose, damit du auf Basis deiner eigenen Daten entscheiden
kannst. Empfehlung: mit `meter` starten, die Plausibilitäts-Entität einige Tage
beobachten und dann umstellen.

### Vergleich Wärmemengenzähler und Berechnung

Drei Sensoren zeigen, wie weit Wärmemengenzähler und Berechnung auseinanderliegen
(positiv = der Zähler zeigt mehr Wärme als die Berechnung):

| Sensor | Vergleicht |
| --- | --- |
| `…_meter_deviation_daily` | Zählerenergie von heute mit der berechneten Wärme von heute |
| `…_meter_deviation` | Zähler und Berechnung über die Fenster, in denen beide für *denselben* Zeitraum gebucht wurden (Gesamtzeit); das ist der Wert hinter der Plausibilitätswarnung |
| `…_calc_thermal_energy_daily` | die berechnete Wärme von heute, für eine Kachel neben `…_thermal_energy_daily` |

Gut zu wissen:

- `…_calc_thermal_energy` ist eine **Gesamtsumme** und wird nie zurückgesetzt.
  Vergleiche sie nicht mit dem Tageswert des Zählers, sondern nimm die
  `_daily`-Sensoren.
- Beide Abweichungs-Sensoren bleiben *unbekannt*, solange weniger als 10 kWh
  berechnete Wärme vorliegen, weil ein Zähler in ganzen kWh über kleinere Mengen
  wenig aussagt.
- Eine Abweichung über 10 % löst eine Warnung in `…_plausibility` aus.
- Über wenige Tage sind 1 bis 2 % Unterschied bei einem 1-kWh-Zähler normal.
  Beurteile den Verlauf über mehrere Tage, nicht einen einzelnen Wert.

## Arbeitszahlen

- **Netto**-Arbeitszahl: effektive Wärmeenergie geteilt durch die elektrische
  Energie *ohne* Steuerung und Lüftung (braucht den Eingang für die
  Hilfsenergie). Ohne ihn ist Netto gleich Brutto.
- **Brutto**-COP (`cop_gross…`): dieselbe Wärmeenergie geteilt durch die
  *gesamte* elektrische Energie. Das ist der Wert für den Vergleich mit
  Herstellerangaben (EN 14511).
- **Aufteilung Heizen / Warmwasser** (`cop_heating`, `cop_dhw`,
  `spf_heating_yearly`, `spf_dhw_yearly`): Jeder Energieschritt wird dem Zustand
  des Flags *Warmwasser aktiv* in diesem Moment zugeordnet. Nur mit konfiguriertem
  Flag.

Zeiträume: aktuell, Tag, Monat, Jahr (optional Heizperiode) und Gesamtzeit.
Vorheriger Tag und vorheriger Monat werden beim Wechsel festgehalten. Negative
Deltas der Quellzähler werden als Zählerreset behandelt und neu aufgesetzt.

### Außentemperatur und Referenz-COP

Die Außentemperatur wird nur für `cop_reference` verwendet und hat keinen Einfluss
auf berechnete Werte. Bevorzugt wird der Außenfühler der Wärmepumpe selbst, weil
sich die Herstellerwerte auf die Lufttemperatur am Gerät beziehen. Eine
Wetterstation nimmst du nur, wenn der Fühler der Anlage träge ist, in der Sonne
hängt oder von der Abluft beeinflusst wird. Stelle sie dann nah am Gerät in den
Schatten.

Die vorbelegten Referenzwerte (COP 2,7 bei -7 °C und 3,3 bei 2 °C, jeweils bei
35 °C Vorlauf) stammen aus den Daten des LWZ 303 Integral. Prüfe sie anhand des
Datenblatts deines Geräts und ändere sie in den Optionen, wenn sie abweichen. Sie
sind nur ein Vergleichswert: Gemessene Werte enthalten zusätzlich Abtauen,
Teillast und Warmwasser und sind mit einem Prüfstandswert nicht direkt
vergleichbar.

## Abtauen

Ausführliche Erklärung: [docs/defrost.de.md](docs/defrost.de.md).

Beim Abtauen entzieht die Wärmepumpe dem Heizwasser Wärme. Der Vorlauf fällt dann
unter den Rücklauf, und die Leistung wird **negativ**. Das ist echte Energie und
kein Sensorfehler. Sie wird als *Abtauverlust* getrennt gebucht und nicht auf null
begrenzt. Ein Abtauvorgang wird erkannt, wenn der Vorlauf mindestens 20 s lang
um mehr als 0,5 K unter dem Rücklauf liegt (einstellbar in den Optionen).

### Optionaler Eingang „Abtauen aktiv"

Meldet deine Wärmepumpe, wann sie abtaut, wähle diese Entität (`binary_sensor`,
`switch`, `input_boolean`, …) als **Abtauen aktiv** aus. Das ist nur eine
Gegenprüfung: Die Energie wird weiterhin nach dem Vorzeichen der berechneten Leistung
gebucht, das Signal ändert die Arbeitszahl also nie.

- Jeder gemeldete Vorgang wird mit den Wassertemperaturen verglichen (Vorlauf unter dem
  Rücklauf um mehr als die Abtau-Schwelle, solange das Signal an ist oder bis 2 Minuten danach).
- Vorgänge, die in den Temperaturen ohne Signal zu sehen sind, werden ebenfalls gezählt.
- Die Zähler stehen im Attribut `defrost.flag` von `…_plausibility`. Ab 5 Vorgängen
  warnt die Entität, wenn mehr als die Hälfte der gemeldeten Vorgänge in den
  Temperaturen nicht zu sehen ist oder mehr als die Hälfte aller Vorgänge ohne Signal
  beginnt.
- Ein Signal, das dauernd an ist (zum Beispiel eine „Eisüberwachung"), zeigt sich als
  Warnung. Prüfe also, was deine Entität wirklich bedeutet.

Details: [docs/defrost.de.md](docs/defrost.de.md).

## Plausibilitätsprüfungen und Diagnose

Jede Prüfung wird je Grund gezählt und in der Diagnose angezeigt.

| Prüfung | Verwirft / warnt, wenn |
| --- | --- |
| Fehlender Eingang | Durchfluss, Vorlauf oder Rücklauf nicht verfügbar |
| Veralteter Eingang | länger als das konfigurierte Alter nicht gemeldet (Standard 600 s, `0` schaltet ab) |
| Temperaturbereich | außerhalb von −30 … 100 °C |
| Durchfluss | negativ oder über dem maximal plausiblen Durchfluss (Standard 3 m³/h) |
| Spreizung | \|Vorlauf − Rücklauf\| über dem Grenzwert (Standard 25 K) |
| Leistung | \|Leistung\| über maximaler Wärmeleistung × Sicherheitsfaktor |
| Leerlauf-Durchfluss | Durchfluss unter dem Minimum (Standard 0,1 m³/h): wird als 0 kW gebucht, kein Fehler |
| Zählerstand | Rückgang oder Anstieg über dem, was die Hardware liefern kann (Spike-Filter) |
| Zähler gegen Berechnung | Ein Zählerschritt weit vor der berechneten Wärme wird verworfen (nur über Fenster ohne Datenlücken beurteilt) |
| Zähler-Abweichung | Warnung bei mehr als 10 % Abweichung zwischen Zähler und Berechnung (ab 10 kWh) |
| Abtauanteil | Warnung bei mehr als 25 % der gelieferten Wärme |
| COP-Bereich | Warnung, wenn der Gesamtzeit-Brutto-COP unter 0,8 oder über 8 liegt (ab 2 kWh elektrischer Energie) |
| Aufeinanderfolgende Ablehnungen | Fehler nach 20 verworfenen Messwerten in Folge |

Verworfene Messwerte werden nie gebucht. Ein verworfener Messwert oder eine Lücke
von mehr als 5 Minuten unterbricht die Integrationskette; das fehlende Intervall
wird übersprungen, nicht hochgerechnet. Durchfluss, Vorlauf und Rücklauf kommen
meist als getrennte Zustandsänderungen und werden deshalb gesammelt und einmal pro
Sekunde ausgewertet. Zusätzlich wird die Berechnung alle 30 s mit den letzten
bekannten Werten ausgewertet, damit ein konstanter Messwert weiter integriert.

Der Spike-Filter für den Wärmemengenzähler nutzt eine konfigurierbare maximale
Wärmeleistung (Standard 12 kW), einen Sicherheitsfaktor (1,2) und eine
Zählerauflösung (1 kWh). Stelle das Maximum auf die tatsächliche Nennleistung
deiner Wärmepumpe ein.

**Diagnose-Entitäten**

- `…_plausibility`: Zustand `inactive`, `ok`, `warning` oder `error`; alle Details
  (Zähler je Grund, Zähler gegen Berechnung, Abtauanteil, letzte Ablehnung) sind
  Attribute.
- `…_input_diagnostics`: jeder konfigurierte Eingang mit Rohzustand, Einheit,
  normiertem Wert, vorherigem Wert und Delta, Summen je Zeitraum, Backfill-Status
  und einem `calculation`-Block mit effektiver Wärmeenergie, berechneter Wärme und
  Abtauverlust je Zeitraum.

## Entitäten

Für den Systemnamen `LWZ303` und den Systemtyp `hp` (Home Assistant kann eine
bestehende Entity-ID nach einer Umbenennung behalten; die `unique_id` basiert auf
der Config-Entry-ID):

| Entität | Inhalt |
| --- | --- |
| `sensor.lwz303_hp_cop`, `_daily`, `_monthly` | Arbeitszahl (netto) |
| `sensor.lwz303_hp_spf_yearly`, `_heating_season`, `_lifetime` | Jahres-, Heizperioden- und Gesamtzeit-Arbeitszahl |
| `…_cop_previous_day`, `…_cop_previous_month` | Werte des vorherigen Zeitraums |
| `…_cop_gross`, `_daily`, `_monthly`, `_yearly` | COP auf die gesamte elektrische Energie |
| `…_cop_heating`, `…_cop_dhw`, `…_spf_heating_yearly`, `…_spf_dhw_yearly` | Aufteilung Heizen / Warmwasser |
| `…_thermal_energy`, `…_electrical_energy` und `_daily`-Varianten | aufsummierte Energie |
| `…_calc_thermal_power` | vorzeichenbehaftete Wärmeleistung in kW |
| `…_calc_thermal_energy` | berechnete gelieferte Wärme (Gesamtsumme, wird nie zurückgesetzt) |
| `…_calc_thermal_energy_daily` | berechnete gelieferte Wärme von heute |
| `…_meter_deviation` | Wärmemengenzähler gegen Berechnung in %, über die Fenster, in denen beide für denselben Zeitraum gebucht wurden (ab 10 kWh) |
| `…_meter_deviation_daily` | Zähler-Wärme von heute gegen berechnete Wärme von heute in % (ab 10 kWh) |
| `…_defrost_energy`, `…_defrost_count` | berechneter Abtauverlust und erkannte Vorgänge |
| `…_cop_reference` | Hersteller-COP bei der aktuellen Außentemperatur (gilt für 35 °C Vorlauf) |
| `…_spike_count` | verworfene Ausreißer des Wärmemengenzählers |
| `…_plausibility`, `…_input_diagnostics` | Diagnose (siehe oben) |

Die Arbeitszahl-Sensoren haben zusätzlich Attribute wie
`effective_thermal_energy_kwh`, `thermal_mode` und `gross_ratio`. `cop` in einer
Entity-ID ist ein kompakter technischer Bezeichner; die Sensoren zeigen
energiebasierte Arbeitszahlen, keinen momentanen COP.

## Einrichtung, Zeiträume und Speicherung

Der Start des Anfangsjahres wird nur beim Anlegen eines Config Entrys abgefragt:

- Ab jetzt starten
- Volles Jahr (1. Januar)
- Teiljahr ab einem gewählten Datum

Bei den letzten beiden liest die Integration die Zählerstände aus der
Recorder-Historie und setzt das Jahr sofort als *aktueller Zählerstand −
historischer Startzählerstand* auf. Ist die Historie nicht verfügbar, wird nichts
erfunden: Das Jahr beginnt mit der Installation und meldet `history_unavailable`.
Ein per Backfill gefülltes Anfangsjahr nutzt immer den Zählerstand des
Wärmemengenzählers, weil die Historie nur diese Werte enthält. Ab dem nächsten
Kalenderjahr beginnt das Jahr am 1. Januar.

Der Zustand wird je Config Entry im Home-Assistant-Speicher abgelegt, damit
Neustarts und Updates die Zeiträume nicht zurücksetzen. Ältere gespeicherte Daten
werden automatisch migriert. Mehrere Config Entries sind möglich. Wird eine
Energiequelle oder ihre Einheit in den Optionen geändert, wird die
Zähler-Basis auf den aktuellen Stand neu gesetzt; die aufgelaufenen Summen bleiben
erhalten.

## Installation

### Schnellinstallation (Marketplace / HACS)

1. Öffne das Repository in deinem Marketplace oder HACS:
   [![Öffnet deine Home-Assistant-Instanz und das Repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=vandecook&repository=home-assistant-heat-pump-performance&category=integration)
2. Klicke auf **Herunterladen** und wähle die neueste Version.
3. **Home Assistant neu starten.**
4. Gehe zu *Einstellungen > Geräte & Dienste > Integration hinzufügen*, suche
   **Heat Pump Performance** und folge dem Dialog.
5. Wähle deine Energiezähler (Wärme- und elektrische Energie sind Pflicht).
   Durchfluss, Vor- und Rücklauftemperatur sind optional und aktivieren die
   berechnete Wärme.

### Schritt für Schritt ohne Button

- **Home Assistant 2026.11 oder neuer:** *Einstellungen > Marketplace* öffnen
  (HACS ist eingebaut). Dieses Repository als benutzerdefiniertes Repository
  (Kategorie *Integration*) hinzufügen, falls es noch nicht gelistet ist:
  `https://github.com/vandecook/home-assistant-heat-pump-performance`.
- **Ältere Versionen mit HACS:** *HACS > ⋮ > Benutzerdefinierte Repositories*, die
  URL oben eintragen, Kategorie *Integration*, dann herunterladen.
- **Manuell:** den Ordner `custom_components/heat_pump_performance` aus diesem
  Repository in den Ordner `custom_components` deiner Home-Assistant-Konfiguration
  kopieren und neu starten.

Nach einem Update einmal Home Assistant neu starten; deine gespeicherten Summen
bleiben erhalten.

### Beta installieren

HACS bietet nur Releases (und den Standard-Branch) an, keine Feature-Branches. Um
eine Beta zu testen, veröffentlichst du auf GitHub ein Release, das als
*Pre-release* markiert ist, schaltest in HACS bei der Integration *Betaversionen
anzeigen* ein und lädst genau diese Version neu herunter. Mach vorher ein Backup
von Home Assistant und starte es danach neu.

## Wartungsaktionen

Beide Aktionen laufen in zwei Schritten: erst Vorschau, dann mit `confirm: true`
anwenden. Vor jeder Änderung wird unter `.storage` ein Backup des Zustands der
Integration mit Zeitstempel gespeichert. Recorder-Historie und Langzeitstatistiken
werden nie umgeschrieben. Mach vor dem Anwenden ein Backup von Home Assistant.

Die Config-Entry-ID findest du unter **Entwicklerwerkzeuge > Template**:

```jinja2
{{ config_entry_id('sensor.lwz303_hp_spf_lifetime') }}
```

Verwende die zurückgegebene ID, **nicht** eine Geräte-ID oder Entity-ID.

**Bekannten falschen Wärmezuwachs korrigieren** (nur mit geprüftem Wert, nie aus
einem Diagramm geschätzt; dieselbe Korrektur nicht zweimal anwenden):

```yaml
action: heat_pump_performance.repair_thermal_balance
data:
  entry_id: YOUR_CONFIG_ENTRY_ID
  affected_at: "2026-10-12T03:52:10+02:00"
  correction_kwh: 1000  # nur Beispiel
  confirm: false
```

Die Vorschau listet alle betroffenen Zeiträume mit Wärmeenergie und Arbeitszahl
vorher und nachher. Auch die effektive Wärmeenergie wird korrigiert, außer im
vollständig berechneten Modus, in dem der falsche Zählerschritt nie enthalten war.

**Berechnete Historie einer Instanz zurücksetzen** (interne Zeiträume, vorheriger
Tag/Monat, Backfill des Anfangsjahres; Zähler werden auf die aktuellen Stände neu
aufgesetzt):

```yaml
action: heat_pump_performance.reset_history
data:
  entry_id: YOUR_CONFIG_ENTRY_ID
  reset_spikes: false
  confirm: false
```

`reset_spikes: true` setzt zusätzlich den Ausreißerzähler, die
Plausibilitätsstatistik und den Abtauzähler zurück. Nach einem Reset ist das Jahr
ein Teiljahr, das zum Zeitpunkt des Resets beginnt.

## Änderungsverlauf

- **0.2.0**: Selbstberechnete Wärme aus Durchfluss und Temperaturen,
  vorzeichenbehaftete Leistung und Abtauerkennung, Wärme-Modi,
  Plausibilitätsprüfungen mit Diagnose-Entität, Brutto-COP, Aufteilung Heizen /
  Warmwasser, Hersteller-Referenz-COP, Außentemperatur, erweiterter Config- und
  Options-Flow, Migration des gespeicherten Zustands.
- **0.1.14**: Hassfest- und HACS-Validierung (`services.yaml`,
  `after_dependencies`, Brand-Icon, Reihenfolge in der Manifest-Datei). Keine
  Änderungen an der Berechnung.
- **0.1.13**: Vorheriger Tag/Monat, tägliche Energie-Sensoren, Ausreißerzähler,
  Aktion `reset_history`.
- **0.1.12**: Abgesicherte Aktion `repair_thermal_balance` (erst Vorschau, dann
  anwenden).
- **0.1.11**: Spike-Schutz für den Wärmemengenzähler.
- **0.1.9**: Elektrische Netto-Energie je Zeitraum; Migration älterer Zustände.
- **0.1.8**: Alle optionalen Eingänge lassen sich in den Optionen wieder entfernen.
- **0.1.6**: Optionale Auswahlfelder im Options-Flow repariert.
- **0.1.5**: `input_number` und `number` als numerische Eingänge möglich.
- **0.1.4**: Eingangsdiagnose zeigt vorherige Werte und Deltas; sie bleiben über
  Neustarts erhalten.
- **0.1.3**: Entität für die Eingangsdiagnose.
- **0.1.2**: Quell-Entitäten und Einheiten im Options-Flow änderbar.
