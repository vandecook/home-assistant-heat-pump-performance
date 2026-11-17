# Abtau-Behandlung

[English](defrost.md) | [Deutsch](defrost.de.md) | [Zurück zur README](../README.de.md)

Diese Seite erklärt, warum Abtauvorgänge in die Arbeitszahl einbezogen werden und wie
die Integration sie erkennt. Sie gilt, wenn Durchfluss, Vorlauf- und Rücklauftemperatur
konfiguriert sind (siehe *Eingänge* in der README).

## Was beim Abtauen passiert

Bei niedriger Außentemperatur vereist der Verdampfer. Um das Eis zu schmelzen, kehrt die
Wärmepumpe den Kreislauf kurz um und entzieht dem **Heizwasser Wärme**. Das Gerät läuft
weiter und verbraucht Strom, aber es kommt keine Wärme im Haus an. Im Gegenteil: Das
Heizsystem verliert Wärme.

An den Wassertemperaturen sieht man das: Die **Vorlauftemperatur (VL) fällt unter die
Rücklauftemperatur (RL)**. Die Differenz Vorlauf − Rücklauf wird **negativ**.

## Wie die negative Differenz verwendet wird

Die Wärmeleistung wird aus dem Durchfluss und der Temperaturdifferenz berechnet:

`P [kW] = Durchfluss [m³/h] × 1,163 × (Vorlauf − Rücklauf) [K]`

Weil die Differenz beim Abtauen negativ ist, ist auch die Leistung negativ. Die
Integration behält dieses Vorzeichen bewusst und setzt es **nicht** auf null:

- Positive Leistung wird als gelieferte Wärme gebucht (`…_calc_thermal_energy`).
- Negative Leistung wird als **Abtauverlust** gebucht (`…_defrost_energy`).

Die Energie wird über die Zeit mit der Trapezregel integriert. Wechselt die Leistung
zwischen zwei Messwerten das Vorzeichen, wird das Intervall genau am Nulldurchgang
geteilt, damit Wärme und Verlust nicht vermischt werden.

## Abtauvorgang erkennen

Ein Vorgang wird gezählt (`…_defrost_count`), wenn der **Vorlauf mindestens 60 Sekunden
lang um mehr als 0,5 K unter dem Rücklauf liegt**. Er endet, sobald der Vorlauf wieder auf
oder über dem Rücklauf liegt oder der Durchfluss stoppt. Kurze Einbrüche durch
Sensorrauschen oder einen halb aktualisierten Messwert zählen nicht als Vorgang. Die
Gesamtdauer aller Vorgänge steht im Attribut `duration_s_total`.

Beide Werte lassen sich in den Optionen einstellen (*Zeiträume, Grenzwerte und Berechnung*):

| Einstellung | Standard | Bedeutung |
| --- | --- | --- |
| Abtauen: Vorlauf unter Rücklauf um mehr als (K) | 0,5 | Temperatur-Toleranz |
| Abtauen: Mindestdauer (s) | 60 | Mindestdauer |

Die Erkennung braucht einen gültigen Durchfluss (über dem eingestellten Mindestdurchfluss);
ohne Durchfluss gilt „kein Abtauen". Sie nutzt nur die Temperaturdifferenz und hängt damit
nicht davon ab, wie viel Wasser gerade fließt.

Buchung und Erkennung sind getrennt: Jeder negative Leistungswert wird als Verlust
gebucht, der Zähler reagiert aber nur auf anhaltende Einbrüche.

## Warum das für die Arbeitszahl wichtig ist

Der Strom beim Abtauen wird vom Stromzähler immer mitgezählt. Würde man die negative
Wärme ignorieren, zählte die Arbeitszahl den Strom, aber nicht die Wärme, die das System
verloren hat. Sie fiele **zu hoch** aus. Viele Wärmemengenzähler verhalten sich genau so,
weil sie negativen Wärmefluss nicht mitzählen.

Mit dem Wärme-Modus (Optionen) legst du fest, was in die Arbeitszahl eingeht:

| Modus | Wirkung des Abtauens |
| --- | --- |
| `meter` (Standard) | nicht berücksichtigt, wie bei einem normalen Wärmemengenzähler |
| `meter_minus_defrost` | Wärmemengenzähler abzüglich des berechneten Abtauverlusts |
| `calculated` | berechnete gelieferte Wärme abzüglich des Abtauverlusts |

## Optionaler Eingang „Abtauen aktiv"

Meldet deine Wärmepumpe, wann sie abtaut (ein `binary_sensor`, `switch`,
`input_boolean` oder Ähnliches, das beim Abtauen `on` ist), kannst du das in der
Konfiguration oder den Optionen als **Abtauen aktiv** auswählen.

Das Signal dient **nur zur Gegenprüfung**. Die Energie wird weiterhin nach dem Vorzeichen
der berechneten Leistung gebucht, ein falsches oder fehlendes Signal verändert die
Arbeitszahl also nie. Die Integration macht damit Folgendes:

- Jeder gemeldete Vorgang wird geprüft: Haben die Wassertemperaturen das Abtauen gezeigt
  (Vorlauf unter Rücklauf um mehr als die Abtau-Schwelle, solange das Signal an war oder bis 2 Minuten danach)? Das
  Ergebnis wird als `confirmed_by_temperatures` oder `not_seen_in_temperatures` gezählt.
- Aus den Temperaturen erkannte Vorgänge, die ohne das Signal beginnen, werden als
  `detected_without_flag` gezählt. Der Status der WP kommt oft etwas später als die
  Temperaturen reagieren: Ein Signal, das bis zu 2 Minuten nach der Erkennung
  einschaltet, wird ihr zugeordnet und nicht als Abweichung gezählt.
- Alles steht im Attribut `defrost.flag` von `…_plausibility` und bleibt über Neustarts
  erhalten. `reset_history` mit `reset_spikes: true` setzt es zurück.
- Ab 5 Vorgängen warnt die Plausibilitäts-Entität, wenn mehr als die Hälfte der
  gemeldeten Vorgänge in den Temperaturen nicht zu sehen ist
  (`defrost_flag_without_negative_power`) oder wenn mehr als die Hälfte aller Vorgänge
  ohne das Signal beginnt (`negative_power_without_defrost_flag`).

Ein Signal, das dauernd `on` ist (zum Beispiel eine „Eisüberwachung", die kein echtes
Abtausignal ist), erzeugt viele unbestätigte Vorgänge und zeigt sich als Warnung. Prüfe,
was deine Entität wirklich bedeutet, bevor du dich darauf verlässt.

### Quelle für Anzahl und Dauer wählen

Mit der Einstellung **Abtauen: Quelle für Anzahl und Dauer** legst du fest, woher
`…_defrost_count` und seine Attribute kommen:

- **Berechnet** (Standard): aus der Temperaturerkennung (Schwelle und Mindestdauer
  oben).
- **Signal der Wärmepumpe**: Jedes Einschalten des Eingangs „Abtauen aktiv“ zählt als
  ein Vorgang, die Dauer ist die Zeit, in der das Signal an war, und `active` folgt dem
  Signal. Dafür ist der Eingang „Abtauen aktiv“ nötig; ohne ihn lehnt das
  Optionsformular die Auswahl ab.

Der jeweils andere Wert bleibt sichtbar (`calculated_cycles`, `calculated_active`),
die Gegenprüfung läuft weiter. Die Energiebuchung ist **nicht** betroffen: Der
Abtauverlust ist immer der negative Teil der berechneten Leistung, denn das ist die
Wärme, die wirklich zurückgeflossen ist. Mit dem WP-Signal kann die Anzahl daher von
den Vorgängen abweichen, in denen Verlustenergie gebucht wurde.

## Was du in Home Assistant siehst

| Entität | Inhalt |
| --- | --- |
| `…_defrost_count` | erkannte Abtauvorgänge (Attribute: `active`, `duration_s_total`) |
| `…_defrost_energy` | aufsummierter Abtauverlust in kWh |
| `…_plausibility` | Attribut `defrost` mit Vorgängen, Dauer, Verlust und Anteil an der Wärme |

Die Plausibilitäts-Entität warnt, wenn der Abtauverlust mehr als **25 %** der gelieferten
Wärme beträgt. Das ist ein Hinweis auf einen falschen Sensor oder ungewöhnlich häufiges
Abtauen.

## Grenzen

- Der Verlust ist nur so gut wie die Durchfluss- und Temperatursensoren. Ein Vorlauf-
  fühler, der aus anderen Gründen (falscher Einbauort, Sensor-Offset) niedriger als der
  Rücklauf misst, sieht aus wie ein Abtauvorgang.
- In den ersten Minuten nach dem Start ist der Wert klein. Der Anteil wird erst ab 10 kWh
  gelieferter Wärme beurteilt.
- Ob Herstellerwerte (COP im Datenblatt) Abtauverluste enthalten, hängt vom Prüfverfahren
  ab. Prüfe das Datenblatt deines Geräts, bevor du vergleichst.
