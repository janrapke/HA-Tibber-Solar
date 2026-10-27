# Klima-Steuerung in der Integration – Bauplan Stufe 1

Dieser Plan wird von einer stündlichen Aufgabe abgearbeitet („Klima-Umsetzung“). Jeder Durchlauf
liest diese Datei, erledigt den **nächsten offenen Schritt** der Checkliste, hakt ihn ab und schreibt
eine Zeile ins Protokoll unten. Alle Entscheidungen hier stammen aus der Abstimmung mit dem User
am 2026-10-27. **Nicht neu verhandeln, sondern umsetzen.**

## Ziel
Die Klima-Logik aus `ha_packages/klima_wohnzimmer.yaml` (19 Automationen) wird als Modul in die Integration
`custom_components/smart_battery_optimizer` übernommen und erweitert:
- Regelung nach dem Raumsensor statt nach dem Gerätesensor, Ziel: Durchlaufen auf kleiner Stufe statt Takten.
- „Heizung ersetzen“ entscheidet über Kosten (Strom ÷ Effizienz gegen Öl ÷ Kesselwirkungsgrad) statt über feste Preisgrenzen.
- Überschuss-Modus: Wenn der Akku laut Plan voll läuft oder über einer Schwelle steht, wird mit Solarstrom geheizt bzw. gekühlt.
- Lernen ab Tag 1: Sensorversatz, Auskühlrate, Effizienz und Leistung je Drehzahl werden aus Messungen gelernt.
- **Start im Modus „nur beobachten“.** Die App zeigt, was sie tun würde, steuert aber nichts. Die YAML-Steuerung bleibt aktiv,
  bis der User selbst umschaltet. Kein Durchlauf darf die YAML-Automationen abschalten oder die Steuerung scharf schalten.

## Fakten aus Messungen und Tests (2026-10-27)
| Was | Wert |
|---|---|
| Klima | Midea Solstice EZ-18RD6-I, `climate.152832118046533_climate`, Integration `midea_ac_lan`, device_id `152832118046533` |
| Datenblatt | Heizen 5,4 kW nominal (1,4–6,8 kW), SCOP 5,7, Kühlen 5,0 kW |
| Stromverbrauch | **Attribut `compressor_power` (W) ist richtig**, `realtime_power` ist ~3× zu niedrig – nie verwenden |
| Drehzahl | Attribut `compressor_frequency` (Hz); 0 = Kompressor aus |
| Kleinste Dauerstufe | ~30 Hz ≈ 810 W bei ~15 °C außen (Sollwert 26 °C, Lüfter silent). Anlauf 5–10 min mit 1,2–1,6 kW |
| Gerätesensor | Attribut `indoor_temperature`, misst beim Heizen 5–7 K mehr als der Raum |
| Piepen | `midea_ac_lan.set_attribute` `prompt_tone=false` wirkt, ist aber nur im HA-Cache und nach jedem HA-Neustart wieder `true` → beim Start und vor Befehlen setzen. `sound` wirkt nicht |
| Luftrichtung | `indirect_wind=true` lenkt die Luft beim Heizen nach oben (nicht aufs Sofa) und heizt weiter. Wird bei jedem Aus/Ein zurückgesetzt → nach jedem Start neu setzen. `wind_ud_angle` hilft nicht |
| Lüfter | `fan_mode` silent/low/medium/high/full/auto. Normalbetrieb **silent**, stärker nur als Boost |
| Leistungsstufe | `rate_select` nicht verwenden (User-Entscheidung), muss auf `"100"` bleiben |
| `out_silent` | wird nicht angenommen, nicht verwenden |
| Raumsensor | **`sensor.wohnzimmer_temperatur`** (BleBox-Sonde, WLAN, meldet alle 15–60 s). Ersatz: `sensor.jans_echo_dot_temperatur` (grob, ~0,5 K niedriger, Ausreißer wie -280 → Plausibilitätsfilter 5–40 °C) |
| Außentemperatur | `sensor.openweathermap_temperature` (Prognose: Wetterdaten der Integration, `_weather` hat `temp`) |
| Strompreis | Tibber-Preise der Integration (`_price_at`, `_current_price`) |
| Raum kühlt aus | ~0,4 K/h bei ~10 K Differenz drinnen/draußen (Zeitkonstante ~28 h, Heizkörper evtl. mitgelaufen) |
| Wärmebedarf | 25.10.: ~2,8 kWh Strom ≈ 10 kWh Wärme bei 10–12 °C außen |
| Wechselrichter | ~1,25–1,6 kW. Haus ~550 W Grundlast + Klima 810 W ≈ 1,39 kW passt |
| Heizkörper | Better Thermostat: `climate.bt_wohnzimmer_bt_wohnzimmer`, `climate.bt_esszimmer_bt_esszimmer`, `climate.bt_kuche_bt_kuche`, `climate.bt_flur_bt_flur`; Heizungsautomationen `automation.heizung_wohnzimmer`, `automation.heizung_esszimmer`, `automation.heizung_kuche`, `automation.heizung_auf_heat_stellen` |
| Anwesenheit | `input_boolean.anwesnedheit_irgendjemand` (Tippfehler ist echt), `input_boolean.anwesenheit_wohnbereich` |
| Akku | `sensor.smartshunt_hq2305uvtaz_battery` (ist in der Integration schon als SoC konfiguriert) |
| Öl | 1,00 €/L (User-Vorgabe, nicht Tagespreis), Kesselwirkungsgrad 85 % → 11,8 ct pro kWh Wärme |

### Effizienz-Startkurve (vor dem Lernen), realistisch inkl. Abtauen
| außen °C | 16 | 12 | 10 | 7 | 5 | 2 | 0 | -2 | -5 | -7 | -10 | -15 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Effizienz | 5,1 | 4,6 | 4,3 | 3,9 | 3,6 | 3,1 | 2,9 | 2,7 | 2,4 | 2,2 | 2,0 | 1,7 |

Lineare Interpolation, außerhalb festhalten. Grenzpreis = Ölkosten pro kWh Wärme × Effizienz × (1 − Sicherheitsabschlag 10 %).

## Verhalten (Prioritäten, die wichtigste zuerst)
1. **Sicherheit.** Raumsensor und Ersatz ungültig, Raum < 17 °C, App deaktiviert oder Ausnahme → Heizkörper bekommen das Heizen zurück
   (BT-Thermostate `heat`, Heizungsautomationen an und einmal auslösen, wie YAML-Automation 18). Die Heizkörper dürfen nie aus bleiben, wenn die App nicht arbeitet.
2. **Manuell.** Betriebsmodus-Select `auto` / `manuell_an` / `manuell_aus` wie im YAML. Bedienung per Fernbedienung oder Midea-App wird erkannt,
   wenn sich der Klima-Zustand ändert und der `context` nicht von einem eigenen Befehl stammt (eigene Kontexte merken) → `manuell_an` bzw. `manuell_aus`.
   `manuell_aus` hält die Klima aus.
3. **Heizung ersetzen** (Schalter, Winter): wenn außen < max. Außentemperatur (Standard 16 °C) und Strompreis jetzt < Grenzpreis bei aktueller Außentemperatur
   → BT-Thermostate `off`, Heizungsautomationen aus, Klima heizt. Wechsel frühestens nach 2 h Mindestdauer (Hysterese), außer Sicherheit.
4. **Überschuss** (Schalter): Akku-Plan meldet Abregelung in den nächsten 2 h (`plan.curtailed_wh(2.0) > 300`) **oder** SoC ≥ Schwelle (Standard 90 %, aus mit 85 %),
   nur wenn gerade nicht aus dem Netz geladen wird → Zieltemperatur +1 K (Heizen) bzw. −1 K (Kühlen) und Lüfter-Boost. Heizen oder Kühlen nach Außentemperatur (> 18 °C kühlen).
5. **Automatik** wie YAML: Kühlen ab Raum > 24,5 °C bis < 23,2 °C, wenn Preis ≤ Grenze Kühlen (Standard 0,30 €), jemand zuhause, 10–22 Uhr.
   Heizen (ohne Ersatz) ab Raum < 19 °C bis > 21,5 °C, wenn günstiger als Öl, jemand zuhause, 10–22 Uhr. 1 h Sperre nach Abschalten, 30 min Mindestlaufzeit.

**Zieltemperatur Raum** (wie YAML): im Wohnbereich 21 °C, zuhause 20 °C, Nacht 0–8 Uhr 19 °C, niemand da 18 °C – alle als Zahl einstellbar.

**Regelung beim Heizen:** Sollwert an der Klima = Zielraumtemperatur + gelernter Versatz (Gerätesensor − Raumsensor im Lauf; Start 5 K),
plus kleiner I-Anteil aus der Raumabweichung (max. ±2 K), gerundet auf 0,5 °C, begrenzt 16–30 °C. Befehle höchstens alle 5 min, nur bei Änderung.
Vor jedem Befehl `prompt_tone=false`. Nach jedem Einschalten (Zustandswechsel auf heat/cool) nach 2–3 min `indirect_wind=true` (nur Heizen) und Lüfter setzen.
Lüfter: silent normal; Boost (medium) bei Überschuss, Preis < Boost-Preis (Standard 0,15 €) oder Raum > 1,5 K unter Ziel; Wechsel höchstens alle 15 min.

## Lernen (`thermal_model.py`, reines Python, Tests in `tests/`)
Alle 15 min ein Datensatz: Raumtemp, Außentemp, Gerätesensor, Klima-Zustand, `compressor_power`, `compressor_frequency`, Heizkörper aktiv ja/nein, Lüfter.
Persistiert in einem eigenen `Store` (`smart_battery_optimizer.climate.<entry_id>`), höchstens 30 Tage Rohdaten.
Gelernt werden (gleitender Mittelwert, robust gegen Ausreißer):
- **Sensorversatz** Gerätesensor − Raum, getrennt Heizen/Kühlen, nur wenn Kompressor > 0 seit ≥ 20 min.
- **Auskühlrate** k in K/h pro K Differenz aus Phasen ohne Heizen (Klima aus, Heizkörper aus oder ohne Wärmebedarf) ≥ 2 h.
- **Heizwirkung**: Temperaturanstieg pro kWh Klimastrom, daraus mit k und Außentemperatur die Effizienz je Außentemperatur-Bereich (2-K-Klassen), nur wenn Heizkörper aus. Startwerte = Kurve oben, neue Werte langsam einmischen, Grenzen 1,2–7.
- **Leistung je Drehzahl** (Hz → W), damit der Planer die Klima-Last kennt.

## Zahlen für den Akku-Planer
Die Klima darf das Lastlernen nicht verfälschen: `compressor_power` wird wie die Wallbox von der gemessenen Hauslast abgezogen
(in `_step_measure`, analog zu `CONF_EXCLUDED_POWER_SENSORS`). In der Prognose wird die erwartete Klima-Last für die Stunden addiert,
in denen die App die Klima laufen lässt (Stufe 1: aktuelle Leistung für die nächsten 2 h, wenn sie läuft).

## Entitäten (Gerät „Klima Wohnzimmer“, `via_device` Hauptgerät, deutsche Namen, Werte über Neustart erhalten)
- Select **Betriebsmodus** (auto / manuell_an / manuell_aus)
- Switch **Steuerung aktiv** (aus = nur beobachten, **Standard aus**), **Heizung ersetzen**, **Überschuss nutzen**
- Number **Zieltemperatur im Raum / zuhause / Nacht / niemand**, **Ölpreis €/L**, **Kesselwirkungsgrad %**, **Max. Außentemperatur Ersatz**,
  **Max. Preis Kühlen**, **Überschuss SoC-Schwelle**, **Boost-Preis**
- Sensor **Entscheidung** (Text: was die App tun würde/tut und warum, Attribute: Sollwert, Lüfter, Grenzpreis, Kosten Klima/Öl je kWh Wärme, Quelle Raumsensor),
  **Gelernte Werte** (Versatz, Auskühlrate, Effizienzkurve, Datensätze), **Klimastrom heute / Monat** (kWh), **Ersetztes Öl Monat** (L), **Ersparnis Monat** (€)
- Konfiguration der Entity-IDs: Konstanten mit den Werten oben als Standard in `const.py`, überschreibbar über einen neuen Options-Schritt
  „Klima“ im `config_flow.py` (Entity-Selector). Kein Pflichtfeld, damit bestehende Installationen nicht brechen.

## Arbeitsregeln für die Durchläufe
- Code-Stil wie im Repo: Kommentare englisch und sparsam, Texte für den User deutsch. Reine Logik ohne HA-Import, testbar mit `pytest tests/`.
- Jeder Schritt endet mit grünen Tests (`.venv/Scripts/python.exe -m pytest -q tests`, sonst `python -m pytest`). Kein Schritt ohne Test-Lauf abhaken.
- Nach jedem abgeschlossenen Schritt committen (Branch `smart-battery-optimizer-integration-12324778900521018597`, das ist der Hauptbranch), Commit-Nachricht englisch,
  mit `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Nicht committen: Screenshots, fremde Dateien.
- Live-Deploy (User hat Live-Deployment erlaubt): `git push`, dann in HA das Update-Entity der Integration mit `update.install` und `version=<7-stelliger Commit-Hash>`,
  danach `homeassistant.restart`, dann prüfen: keine Fehler im Log zu `smart_battery_optimizer`, Entitäten vorhanden, „Steuerung aktiv“ = aus.
  Bei Fehlern: Ursache beheben oder auf den vorherigen Hash zurück-installieren. Nach dem Deploy eine `persistent_notification` mit Kurzbericht anlegen.
- HA-Zugriff: MCP `homeassistant`. Für Registry/Options-Flows: siehe Memory „ha-access“ (Node-Skripte mit Token aus `~/.claude.json`, `MSYS_NO_PATHCONV=1`).
- Nicht anfassen: `rate_select`, `out_silent`, die YAML-Automationen, „Steuerung aktiv“ (bleibt aus), die Klima selbst (kein Ein-/Ausschalten in den Durchläufen).
- Schritt 8 ist erst 24 h nach dem Deploy fällig (Zeit steht im Protokoll). Bis dahin Schritt 9 erledigen; ist nichts fällig, den Durchlauf ohne Änderung beenden.
- Ein Durchlauf darf mehrere Schritte erledigen, wenn Zeit bleibt. Vor dem Ende immer Checkliste und Protokoll aktualisieren und committen,
  damit ein Abbruch durch das Nutzungslimit nichts verliert. Halbfertige Arbeit als `WIP:`-Commit sichern und im Protokoll beschreiben.
- Hängt ein Schritt (zwei Durchläufe ohne Fortschritt), im Protokoll begründen und `persistent_notification` „Klima-Umsetzung braucht Hilfe“ anlegen.

## Checkliste
- [x] 1. `thermal_model.py`: Effizienzkurve, Grenzpreis, Lernen (Versatz, Auskühlrate, Effizienz, Leistung je Hz), `to_dict`/`load_dict`; Tests
- [x] 2. `climate_logic.py`: reine Entscheidungsfunktion (Eingaben → Modus, Klima an/aus, hvac_mode, Sollwert, Lüfter, Heizkörper an/aus, Begründungstext) mit Prioritäten, Hysterese, Sperren; Tests für jede Priorität
- [ ] 3. `climate_control.py`: HA-Anbindung (Zustände lesen inkl. Plausibilität, Kontext-Erkennung Fernbedienung, Befehle mit Ratenbegrenzung, `prompt_tone`/`indirect_wind`, Heizkörper-Übergabe, Sicherheits-Rückfall, Beobachten-Modus, 15-min-Datensätze, Store)
- [ ] 4. Einhängen in `coordinator.py` (eigener Schritt `climate` nach `execute`, isoliert mit try/except; Klimastrom aus Lastlernen abziehen; Klima-Last in Prognose)
- [ ] 5. Entitäten (select/switch/number/sensor) und Options-Schritt „Klima“ in `config_flow.py` + `translations`
- [ ] 6. Gesamttest lokal (alle Tests, `python -m py_compile` aller Dateien), Version in `manifest.json`/`const.py` auf 2.1.0
- [ ] 7. Push + Live-Deploy + Prüfung (siehe Arbeitsregeln), Bericht als Notification
- [ ] 8. 24 h nach Deploy: Daten prüfen (Datensätze kommen an, Entscheidungen plausibel, keine Befehle an Klima/Heizkörper im Beobachten-Modus), Fehler beheben
- [ ] 9. Nachtagent erweitern: Cloud-Routine „Smart Battery – Nachtanalyse“ (`trig_013C8bw8Cvz6WriVttRBCcqE`, RemoteTrigger) – Prompt ergänzen um Klima-Prüfung
      (Entscheidungen und gelernte Werte des Tages, Auffälligkeiten wie Takten, Versatz-Sprünge, Heizkörper nicht zurückgegeben) **und darf ändern**:
      die Zahlen-Entitäten des Klima-Geräts in sinnvollen Grenzen (Zieltemperaturen ±1 K nicht ohne Grund, Boost-Preis, Ölpreis nur auf User-Wunsch nein),
      sowie Lernwerte zurücksetzen, wenn offensichtlich falsch. Er darf **nicht** „Steuerung aktiv“ einschalten oder die YAML-Automationen ändern.
      Änderungen immer im Bericht nennen. Bestehenden Prompt lesen und nur ergänzen.
- [ ] 10. Abschluss: Notification an den User „Klima Stufe 1 fertig, bitte im Dashboard prüfen und dann ‚Steuerung aktiv‘ einschalten“, diese stündliche Aufgabe deaktivieren
      (`update_scheduled_task` enabled=false, Task-ID `klima-umsetzung`).

## Protokoll
- 2026-10-27 18:30 – Plan angelegt (interaktive Sitzung). Raumsensor umgestellt, Paket/Dashboard/BT live auf `sensor.wohnzimmer_temperatur`.
- 2026-10-27 17:24 START Durchlauf
- 2026-10-27 17:26 – Schritt 1 fertig (38c4787): thermal_model.py + tests/test_thermal.py (9 Tests). Entscheidung: Wärmekapazität C fest 1,0 kWh/K (aus 0,42 kW Verlust bei 10 K und 0,4 K/h), weil Effizienz und C mit nur einem Raumsensor nicht trennbar sind; Startversatz Kühlen 0 K; Leistung je Hz in 5-Hz-Klassen; Laufzeit seit Kompressorstart (`run_min`) liefert climate_control.
- 2026-10-27 17:30 – Schritt 2 fertig (9b8a528): climate_logic.py (`decide(Inputs, State) -> Decision`) + tests/test_climate_logic.py (20 Tests, alle 37 grün). Entscheidungen: State wird so fortgeschrieben, als wäre die Entscheidung ausgeführt (Beobachten zeigt echtes Verhalten); App aus → Klima unberührt (`climate_on=None`), Heizkörper zurück; Sensor ungültig → Klima aus außer manuell; Raum < 17 °C beendet nur den Ersatz (Klima darf weiter helfen); Überschuss wie YAML nur 10–22 Uhr, Kühl-Ziel 24 °C (−1 K = 23 °C); Ersatz umgeht die 1-h-Sperre; Nachlauf nach Ersatz-Ende = Modus `nachlauf`; I-Anteil 0,5 K/(K·h), ±2 K. Rate-Limits (5 min Sollwert, 15 min Lüfter) stecken in der Logik, die Verzögerung 2–3 min für indirect_wind macht climate_control.
- 2026-10-27 17:30 ENDE Durchlauf: Schritte 1 und 2 erledigt (38c4787, 9b8a528), 37 Tests grün. Nächster Punkt: 3 (climate_control.py). HA-MCP war in diesem Durchlauf nicht erreichbar (für 1–2 nicht nötig).
