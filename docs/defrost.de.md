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

Ein Vorgang wird gezählt (`…_defrost_count`), wenn die Leistung mindestens **20 Sekunden
lang unter −0,3 kW** liegt. Er endet, sobald die Leistung wieder 0 kW oder mehr beträgt.
Kurze Einbrüche durch Sensorrauschen oder einen halb aktualisierten Messwert zählen nicht
als Vorgang. Die Gesamtdauer aller Vorgänge steht im Attribut `duration_s_total`.

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
