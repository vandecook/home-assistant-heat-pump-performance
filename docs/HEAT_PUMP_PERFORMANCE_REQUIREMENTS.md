# Heat Pump Performance – Anforderungsliste

**Projekt:** [home-assistant-heat-pump-performance](https://github.com/vandecook/home-assistant-heat-pump-performance)  
**Integrations-Domain:** `heat_pump_performance`  
**Letzter bearbeiteter Versionsstand:** `0.1.14`

> Diese Liste dokumentiert den bisher vereinbarten Sollumfang. Ob sämtliche Anforderungen in v0.1.14 vollständig umgesetzt und getestet sind, ist separat anhand des Quellcodes und der Tests zu prüfen.

## 1. Allgemeine Anforderungen

| ID | Anforderung | Priorität |
|---|---|---|
| GEN-01 | Native Home-Assistant-Custom-Integration | Muss |
| GEN-02 | Installation und Aktualisierung über HACS | Muss |
| GEN-03 | Konfiguration über die Home-Assistant-Oberfläche (Config Flow) | Muss |
| GEN-04 | Mehrere unabhängig konfigurierte Wärmepumpen unterstützen | Muss |
| GEN-05 | Frei definierbarer Anlagenname beziehungsweise Präfix | Muss |
| GEN-06 | Eigene Home-Assistant-Geräte und zugehörige Sensoren erzeugen | Muss |
| GEN-07 | Konfiguration bestehender Integrationsinstanzen ändern können | Muss |
| GEN-08 | Vorhandene Bilanzdaten bei Neustarts und Updates erhalten | Muss |
| GEN-09 | Versionsangabe in `manifest.json` und Dokumentation | Muss |
| GEN-10 | Anlagentyp Wärmepumpe (`hp`), perspektivisch auch Brauchwasserwärmepumpe (`dhw_hp`) | Soll |

## 2. Eingangssensoren

Die Integration muss unterschiedliche Home-Assistant-Entitäten als Datenquellen akzeptieren.

| Eingang | Pflicht | Beschreibung |
|---|---|---|
| Thermische Energie | Ja | Kumulativer Wärmemengenzähler |
| Elektrische Anlagenenergie | Ja | Kumulativer Stromverbrauch |
| Elektrische Steuerungsenergie | Nein | Separat zu berücksichtigender Energieverbrauch |
| Elektrische Wohnraumlüftungsenergie | Nein | Separat zu berücksichtigender Energieverbrauch |
| Thermische Leistung | Nein | Momentane Heizleistung |
| Elektrische Leistung | Nein | Momentane Leistungsaufnahme |
| Heizbetrieb aktiv | Nein | Unabhängiger Betriebsstatus |
| Warmwasserbereitung aktiv | Nein | Unabhängiger Betriebsstatus |

Weitere Anforderungen:

- Unterstützte Eingangsdomänen: `sensor`, `number` und `input_number`, sofern numerisch.
- Energieeinheiten `Wh`, `kWh` und `MWh`.
- Manuelle Einheitenauswahl, wenn die Quelle keine zuverlässige Einheit bereitstellt.
- Interne Normalisierung aller Energiemengen auf kWh.
- Verarbeitung unregelmäßiger Aktualisierungsintervalle.
- Heiz- und Warmwasserbetrieb dürfen gleichzeitig aktiv sein.
- Fehlende optionale Eingänge dürfen keine Berechnung verhindern.

## 3. Berechnung der Arbeitszahl

Die Arbeitszahl basiert auf gemessenen Energiemengen:

$$
AZ = \frac{Q_{\mathrm{thermisch}}}{E_{\mathrm{elektrisch,netto}}}
$$

Die elektrische Nettoenergie ergibt sich aus der Anlagenenergie abzüglich der entsprechend konfigurierten Nebenverbraucher.

| ID | Zeitraum | Anforderung |
|---|---|---|
| AZ-01 | Tag | Laufende Tagesarbeitszahl |
| AZ-02 | Vortag | Letzte abgeschlossene Tagesarbeitszahl |
| AZ-03 | Monat | Laufende Monatsarbeitszahl |
| AZ-04 | Vormonat | Letzte abgeschlossene Monatsarbeitszahl |
| AZ-05 | Jahr | Jahresarbeitszahl |
| AZ-06 | Heizperiode | Arbeitszahl einer frei definierten Heizperiode |
| AZ-07 | Lifetime | Arbeitszahl seit Inbetriebnahme beziehungsweise Initialisierung |

**Berechnungsregeln:**

- Grundlage sind Zählerstandsdifferenzen, keine Mittelwerte momentaner COP-Werte.
- Es darf nicht durch null oder negative elektrische Nettoenergie dividiert werden.
- Ohne ausreichende Messdaten muss die Arbeitszahl `unknown` bleiben.
- Ein fehlender neuer Wärmezählerimpuls kann bei geringer Zählerauflösung vorübergehend zu `unknown` führen.
- Abgeschlossene Periodenwerte werden separat bereitgestellt.

**Fachliche Grenze:** Erfasst der Wärmemengenzähler nur Heizenergie, darf die Integration daraus keine kombinierte Heiz- und Warmwasserarbeitszahl ableiten. Eine gesonderte Warmwasserbilanz setzt geeignete Messwerte voraus.

## 4. Bilanzzeiträume und Speicherung

| ID | Anforderung |
|---|---|
| HIST-01 | Tageswechsel um 00:00 Uhr |
| HIST-02 | Monatswechsel am ersten Kalendertag |
| HIST-03 | Jahreswechsel am 1. Januar |
| HIST-04 | Initialen Jahresbeginn wahlweise ab jetzt, mit Startdatum oder ab 1. Januar |
| HIST-05 | Nach dem ersten Jahreswechsel automatisch vollständige Kalenderjahre berechnen |
| HIST-06 | Frei definierbarer Beginn und Ende der Heizperiode |
| HIST-07 | Lifetime-Bilanz wird durch reguläre Periodenwechsel nicht zurückgesetzt |
| HIST-08 | Bilanzstände persistent speichern |
| HIST-09 | Nach Neustart korrekte Zählerreferenzen wiederherstellen |
| HIST-10 | Abgeschlossene Tages- und Monatswerte speichern |
| HIST-11 | Keine zwingende Abhängigkeit von langfristig vorhandenen Recorder-Rohdaten |

Eine dauerhafte, detaillierte Tagesbilanzhistorie mit späterer Rekonstruktion vergangener Monate und Jahre bleibt eine mögliche zukünftige Erweiterung.

## 5. Plausibilitätsprüfung und Spike-Erkennung

| ID | Anforderung | Priorität |
|---|---|---|
| SPIKE-01 | Unrealistische Sprünge des thermischen Energiezählers erkennen | Muss |
| SPIKE-02 | Plausibilitätsgrenze anhand maximaler thermischer Leistung und Zeitdifferenz bestimmen | Muss |
| SPIKE-03 | Toleranz für die Zählerauflösung berücksichtigen | Muss |
| SPIKE-04 | Ungültige Messwerte nicht in die Bilanz übernehmen | Muss |
| SPIKE-05 | Letzten gültigen Referenzstand beibehalten | Muss |
| SPIKE-06 | Aufeinanderfolgende fehlerhafte Werte behandeln | Muss |
| SPIKE-07 | Persistenten Spike-Zähler bereitstellen | Muss |
| SPIKE-08 | Zeitpunkt und Rohwert des letzten Spikes dokumentieren | Muss |
| SPIKE-09 | Verworfenes Delta und zulässigen Grenzwert als Attribute bereitstellen | Soll |
| SPIKE-10 | Spike-Zähler unabhängig von der Energiebilanz zurücksetzen | Soll |

Ein einzelner fehlerhafter Messwert darf weder die Lifetime-Bilanz noch Tages-, Monats-, Jahres- oder Heizperiodenwerte beschädigen.

## 6. Ausgangssensoren

Beispiel mit Präfix `LWZ303`:

| Sensor | Beispiel-Entity-ID |
|---|---|
| Thermische Gesamtenergie | `sensor.lwz303_hp_thermal_energy` |
| Elektrische Gesamtenergie | `sensor.lwz303_hp_electrical_energy` |
| Tagesarbeitszahl | `sensor.lwz303_hp_cop_daily` |
| Vortagesarbeitszahl | `sensor.lwz303_hp_cop_previous_day` |
| Monatsarbeitszahl | `sensor.lwz303_hp_cop_monthly` |
| Vormonatsarbeitszahl | `sensor.lwz303_hp_cop_previous_month` |
| Jahresarbeitszahl | `sensor.lwz303_hp_spf_yearly` |
| Heizperiodenarbeitszahl | Entsprechend der bestehenden Namenskonvention |
| Lifetime-Arbeitszahl | `sensor.lwz303_hp_spf_lifetime` |
| Thermische Tagesenergie | `sensor.lwz303_hp_thermal_energy_daily` |
| Elektrische Tagesenergie | `sensor.lwz303_hp_electrical_energy_daily` |
| Spike-Zähler | `sensor.lwz303_hp_spike_count` |
| Eingangsdiagnose | `sensor.lwz303_hp_input_diagnostics` |

### Zusätzliche Sensorattribute

Für Bilanzsensoren:

- Beginn und Ende des Bilanzzeitraums
- Thermische Energie in kWh
- Elektrische Brutto- und Nettoenergie in kWh
- Berücksichtigte Nebenverbraucher
- Zeitpunkt der letzten gültigen Aktualisierung

## 7. Datenkorrektur

Bereits fehlerhaft berechnete Bilanzwerte müssen korrigierbar sein.

**Home-Assistant-Aktion:** `heat_pump_performance.repair_thermal_balance`

Anforderungen:

- Auswahl der Integration über `entry_id`.
- Angabe des Zeitpunkts des Fehlers.
- Angabe des Korrekturbetrags in kWh.
- Ermittlung der betroffenen Bilanzzeiträume.
- Vorschau vor der Korrektur mittels `confirm: false`.
- Ausführung nur mit `confirm: true`.
- Negative Ergebnisse vermeiden.
- Korrektur persistent speichern.

### Config-Entry-ID über den Template-Editor ermitteln

Unter **Entwicklerwerkzeuge → Vorlagen** eingeben:

```jinja2
{{ config_entry_id('sensor.lwz303_hp_spf_lifetime') }}
```

Die angezeigte ID als `entry_id` verwenden. Die Geräte-ID aus einer Geräte-URL ist dafür nicht geeignet.

### Beispiel: Korrektur als Vorschau

```yaml
action: heat_pump_performance.repair_thermal_balance
data:
  entry_id: "DEINE_CONFIG_ENTRY_ID"
  affected_at: "2026-10-12T03:52:10+02:00"
  correction_kwh: 755673516
  confirm: false
```

Die Zahlen sind ein historisches Beispiel. Die Korrektur darf nicht ein zweites Mal auf bereits korrigierte Bilanzdaten angewendet werden. Erst nach Prüfung der Vorschau mit `confirm: true` ausführen.

## 8. Daten zurücksetzen

**Home-Assistant-Aktion:** `heat_pump_performance.reset_history`

| Anforderung | Verhalten |
|---|---|
| Vorschau | Zeigt betroffene Daten vor dem Reset |
| Bestätigung | Explizit erforderlich |
| Tages-, Monats-, Jahresbilanz | Zurücksetzen |
| Heizperiode | Zurücksetzen |
| Lifetime | Zurücksetzen |
| Vortags-/Vormonatswerte | Löschen |
| Referenzzählerstände | Aus aktuellen gültigen Eingängen neu initialisieren |
| Spike-Zähler | Optional zurücksetzen |
| Konfiguration | Unverändert erhalten |
| Entity-IDs | Unverändert erhalten |
| Recorder-Historie | Nicht automatisch löschen |

### Beispiel: Reset als Vorschau

```yaml
action: heat_pump_performance.reset_history
data:
  entry_id: "DEINE_CONFIG_ENTRY_ID"
  reset_spikes: false
  confirm: false
```

Nach Prüfung der Vorschau kann der Reset mit `confirm: true` bestätigt werden.

**Wichtige Einschränkung:** Sowohl die Korrekturaktion als auch der Reset ändern interne Bilanzen, nicht rückwirkend die bereits aufgezeichneten Home-Assistant-Recorder-Zustände.

## 9. Diagnose und Fehlerbehandlung

Die Integration soll bereitstellen:

- Rohzustände und Einheiten sämtlicher Eingangssensoren
- Normalisierte Energiewerte
- Letzte gültige Referenzzählerstände
- Berechnete Energiedifferenzen
- Letzte erfolgreiche Aktualisierung
- Zustand der Plausibilitätsprüfung
- Anzahl verworfener Spikes
- Gespeicherte interne Bilanzwerte
- Status einer eventuell durchgeführten historischen Initialisierung

Bei nicht verfügbaren Eingangssensoren dürfen keine falschen Energiesprünge erzeugt werden.

## 10. GitHub, HACS und Qualitätssicherung

| ID | Anforderung |
|---|---|
| DEV-01 | Öffentliches GitHub-Repository mit vollständigem Quellcode |
| DEV-02 | MIT-Lizenz |
| DEV-03 | HACS-konforme Repository-Struktur |
| DEV-04 | Gültige `manifest.json` mit korrekt sortierten Schlüsseln |
| DEV-05 | Dokumentierte Aktionen in `services.yaml` |
| DEV-06 | Korrekte Deklaration der Recorder-Abhängigkeit |
| DEV-07 | Brand-Icon für HACS |
| DEV-08 | GitHub Actions für Hassfest-Validierung |
| DEV-09 | GitHub Actions für HACS-Validierung |
| DEV-10 | Releases mit Versionstags wie `v0.1.14` |
| DEV-11 | README mit Installation, Konfiguration, Sensorübersicht und Wartungsfunktionen |
| DEV-12 | Changelog pro Version |
| DEV-13 | Keine Beschädigung vorhandener Konfigurationen oder gespeicherter Bilanzen bei Updates |

## 11. Mögliche zukünftige Erweiterungen

Diese Punkte sind bisher keine verbindlichen Anforderungen der aktuellen Version:

- Vollständige historische Tagesbilanzen dauerhaft speichern.
- Historische Daten nach Korrekturen automatisch neu berechnen.
- Gezielt Recorder-Historien korrigieren.
- Mehrere Heizperioden miteinander vergleichen.
- Jahresarbeitszahl des Vorjahres separat bereitstellen.
- Berechnung bei Bedarf teilweise oder vollständig nach ESPHome verlagern.
- Zusätzlich eine eigenständige Brauchwasserwärmepumpe unterstützen.

---

## Zusammenfassung

Die wesentlichen Ziele sind eine belastbare Energiebilanz, nachvollziehbare Arbeitszahlen über mehrere Zeiträume, Schutz vor fehlerhaften Zählerwerten sowie sichere Korrektur- und Reset-Möglichkeiten. GitHub-/HACS-Integration und Dokumentation gehören zum Funktionsumfang.
