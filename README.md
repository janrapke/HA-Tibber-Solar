# Smart Battery Optimizer for Home Assistant

Ein Custom Component für Home Assistant, das deinen Batteriespeicher intelligent steuert — auf Basis von Tibber-Strompreisen, aktueller Solarproduktion, Wettervorhersage und einem lernenden Algorithmus der deinen Hausverbrauch kennt.

**Kompatibel mit:** OpenDTU / Hoymiles · EcoFlow · Anker SOLIX · Bluetti · Victron · und jedem anderen Speicher mit Home Assistant Integration

---

## Inhaltsverzeichnis

- [Was macht dieser Optimizer?](#was-macht-dieser-optimizer)
- [Voraussetzungen](#voraussetzungen)
- [Installation](#installation)
- [Einrichtung: Schritt für Schritt](#einrichtung-schritt-für-schritt)
  - [Schritt 1: Tibber API & Sensoren](#schritt-1-tibber-api--sensoren)
  - [Schritt 2: Batterie konfigurieren](#schritt-2-batterie-konfigurieren)
  - [Schritt 3: Solar & Wechselrichter-Typ wählen](#schritt-3-solar--wechselrichter-typ-wählen)
    - [Profil A — OpenDTU / Hoymiles](#profil-a--opendtu--hoymiles)
    - [Profil B — Powerstation (EcoFlow, Anker, Bluetti ...)](#profil-b--powerstation-ecoflow-anker-bluetti-)
    - [Profil C — Generischer Schalter](#profil-c--generischer-schalter)
  - [Schritt 4: Systemparameter](#schritt-4-systemparameter)
  - [Schritt 5: Überschuss-Verbraucher (optional)](#schritt-5-überschuss-verbraucher-optional)
  - [Schritt 6: Netzladegerät (optional)](#schritt-6-netzladegerät-optional)
- [Wie funktioniert die Optimierung?](#wie-funktioniert-die-optimierung)
- [Klimageräte & Wärmepumpen — Verbrauchslernen](#klimageräte--wärmepumpen--verbrauchslernen)
- [Geräteplanung — Waschmaschine & Co. zum günstigsten Preis](#geräteplanung--waschmaschine--co-zum-günstigsten-preis)
- [Alle Steuerungsentitäten im Überblick](#alle-steuerungsentitäten-im-überblick)
- [Dashboard-Kacheln einrichten](#dashboard-kacheln-einrichten)
  - [Kachel 1: Batterie & Dispatch Prognose (ApexCharts)](#kachel-1-batterie--dispatch-prognose-apexcharts)
  - [Kachel 2: Battery Forecast Card](#kachel-2-battery-forecast-card)
  - [Kachel 3: Appliance Card (Gerätesteuerung)](#kachel-3-appliance-card-gerätesteuerung)
  - [Kachel 4: Savings Card (Ersparnis-Statistik)](#kachel-4-savings-card-ersparnis-statistik)
- [Einstellungen nachträglich ändern](#einstellungen-nachträglich-ändern)
- [Tipps für den Alltag](#tipps-für-den-alltag)
- [Fehlerbehebung](#fehlerbehebung)
- [FAQ](#faq)

---

## Was macht dieser Optimizer?

Tibber zeigt dir stündlich schwankende Strompreise. Morgens um 3 Uhr kostet Strom vielleicht 18 ct/kWh, abends um 18 Uhr 42 ct/kWh. Der Optimizer nutzt genau diese Information um deinen Speicher so zu steuern, dass du so wenig wie möglich zahlst:

- **Günstige Stunden** → Batterie aus dem Netz laden
- **Teure Stunden** → Batterie entladen, Netzstrom vermeiden
- **Sonne scheint** → Solar hat Vorrang, Batterie damit füllen
- **Batterie voll + Sonne übrig** → Priorisierte Geräte (Waschmaschine, Pool, Warmwasser) automatisch einschalten

Der Optimizer lernt dabei mit jedem Tag besser: Er merkt sich wie viel dein Haus typischerweise verbraucht und wie viel Solar du bei welcher Bewölkung produzierst. So werden die Entscheidungen täglich präziser.

---

## Voraussetzungen

Bevor du anfängst, stelle sicher dass Folgendes in Home Assistant bereits eingerichtet ist:

- **Tibber-Integration** installiert und verbunden (liefert Preis- und Verbrauchsdaten)
- **Tibber Pulse** oder ein anderer Smart Meter der Netzbezug und Einspeisung in Watt misst
- **Batterie-Integration** deines Speichers (liefert den Ladestand in %)
- **Solar-Integration** (liefert die aktuelle Solarproduktion in Watt)
- **Wechselrichter-Integration** je nach Profil (OpenDTU, EcoFlow HA-App, etc.)
- **Wetter-Entity** in HA (z. B. Met.no, Open-Meteo oder DWD)

---

## Installation

### Via HACS (empfohlen)

HACS ist der einfachste Weg. Falls du HACS noch nicht hast: [hacs.xyz](https://hacs.xyz)

1. HACS in der HA-Seitenleiste öffnen
2. Oben rechts auf die **drei Punkte** klicken → **„Benutzerdefinierte Repositories"**
3. Folgende URL eintragen: `https://github.com/janrapke/HA-Tibber-Solar`
4. Als Kategorie **„Integration"** wählen → **Hinzufügen** klicken
5. Den Dialog schließen, dann in der HACS-Suche nach **„Smart Battery Optimizer"** suchen
6. Auf **Installieren** klicken und warten
7. **Home Assistant neu starten** (wichtig — ohne Neustart wird die Integration nicht erkannt)
8. Nach dem Neustart: **Einstellungen → Geräte & Dienste → + Integration hinzufügen** → „Smart Battery Optimizer" suchen und auswählen

### Manuelle Installation

Falls du HACS nicht nutzt:

1. Den Ordner `custom_components/smart_battery_optimizer` aus diesem Repository herunterladen
2. In dein HA-Konfigurationsverzeichnis unter `custom_components/smart_battery_optimizer` kopieren
3. Home Assistant neu starten
4. Integration wie oben beschrieben hinzufügen

---

## Einrichtung: Schritt für Schritt

Der Setup-Assistent führt dich durch 6 Schritte. Du kannst alle Einstellungen später jederzeit über **Einstellungen → Geräte & Dienste → Smart Battery Optimizer → Konfigurieren** anpassen.

---

### Schritt 1: Tibber API & Sensoren

Hier verbindest du den Optimizer mit deinem Tibber-Konto und den Tibber-Sensoren in HA.

| Feld | Was eintragen | Wo finden |
|---|---|---|
| **Tibber API-Token** | Dein persönlicher API-Schlüssel | [app.tibber.com](https://app.tibber.com) → Konto → Entwickler → API-Zugang |
| **Strompreissensor** | Sensor der den aktuellen Preis in €/kWh anzeigt | Tibber-Integration in HA, z. B. `sensor.tibber_pulse_aktueller_stundenstrompreis` |
| **Netzverbrauchssensor** | Sensor für aktuellen Strombezug in **Watt** | Tibber Pulse, z. B. `sensor.tibber_pulse_leistung` |
| **Netzeinspeisesensor** | Sensor für aktuelle Netzeinspeisung in **Watt** | Oft der gleiche wie der Verbrauchssensor (negative Werte = Einspeisung) — trotzdem separat eintragen |

> **Tipp:** Den API-Token findest du unter [app.tibber.com](https://app.tibber.com) → oben rechts auf dein Profilbild → Konto → ganz unten „Entwickler" → „Persönlicher API-Zugang". Der Token sieht aus wie eine lange Zeichenkette.

---

### Schritt 2: Batterie konfigurieren

Hier beschreibst du deinen Speicher — seine Kapazität und die Grenzen für Laden und Entladen.

| Feld | Empfohlener Wert | Erklärung |
|---|---|---|
| **Batteriestand-Sensor** | Sensor der 0–100 % zurückgibt | Von deiner Batterie- oder Wechselrichter-Integration, z. B. `sensor.batterie_soc` |
| **Kapazität (Wh)** | z. B. `5000` | Die Gesamtkapazität deiner Batterie in Wattstunden. 5 kWh = 5000 |
| **Minimaler Ladestand (%)** | `10–20` | Die Batterie wird nie unter diesen Wert entladen — Notreserve für Stromausfälle |
| **Maximaler Ladestand für Netzladung (%)** | `80–95` | Bis hierher darf aus dem Netz geladen werden. Solarüberschuss kann die Batterie weiter füllen |
| **Wirkungsgrad (%)** | `90` | Typischer Lade-/Entlade-Wirkungsgrad. 90 % bedeutet: 100 Wh rein → 90 Wh nutzbar |
| **Ladezustand-Sensor Solarladegerät** | Optional | Wenn dein Solarladegerät einen Status-Sensor hat (z. B. „Absorption", „Float"), verbessert das die Vorhersagegenauigkeit |

> **Was bedeutet „maximaler Ladestand für Netzladung"?** Der Optimizer lädt die Batterie aus dem Netz wenn der Tibber-Preis günstig genug ist. Damit du trotzdem noch Platz für kostenlosenSolarstrom hast, lädst du aus dem Netz nie über diesen Wert. Kommst du dann später in die Mittagssonne, kann Solar die Batterie weiter bis 100 % füllen.

---

### Schritt 3: Solar & Wechselrichter-Typ wählen

Zuerst trägst du deine Solar-Sensoren ein, dann wählst du das passende **Wechselrichter-Profil** für dein System.

#### Gemeinsame Solar-Felder

| Feld | Was eintragen | Erklärung |
|---|---|---|
| **Solarleistungs-Sensoren** | Ein oder mehrere Sensoren in Watt | Wenn du mehrere Wechselrichter oder Strings hast, wähle alle aus — der Optimizer addiert sie |
| **Balkonkraftwerk-Sensor** | Optional | Nur wenn du ein Stecker-PV hast das **nicht** am Speicher hängt und separat gemessen wird |
| **Maximale Wechselrichterleistung (W)** | z. B. `800` | Die Maximalleistung die dein Wechselrichter ausgeben darf. In Deutschland für Stecker-PV gesetzlich auf 800 W begrenzt |
| **Speicher-/Wechselrichter-Typ** | Profil auswählen | Sieh die drei Profile unten |

---

### Profil A — OpenDTU / Hoymiles

**Für wen ist dieses Profil?**
Du hast einen oder mehrere **Hoymiles-Mikrowechselrichter** (z. B. HM-800, HMS-800, HMT-1800) und betreibst diese mit **OpenDTU** — der Open-Source-Firmware die die proprietäre DTU von Hoymiles ersetzt. OpenDTU läuft auf einem ESP32 und stellt eine eigene Home Assistant Integration bereit.

**Wie steuert der Optimizer den Wechselrichter?**

Der Optimizer unterscheidet zwei Betriebsmodi:

1. **Ohne DPL (Dynamic Power Limiter):** Der Wechselrichter wird hart ein- oder ausgeschaltet — per Button-Entity. Entweder er liefert volle Leistung oder gar keine.

2. **Mit DPL:** OpenDTU kann den Wechselrichter stufenlos drosseln. Der Optimizer setzt den DPL-Modus auf `0` (= DPL aktiv, Wechselrichter folgt dem Limit) oder `1` (= aus). Das ermöglicht feinere Regelung z. B. für Nulleinspeisung.

**Welche Entities brauchst du?**

Diese Entities werden automatisch von der [OpenDTU Home Assistant Integration](https://github.com/tbnobody/OpenDTU) erstellt, sobald du OpenDTU in HA einbindest:

| Feld | Entity-Typ | Beispiel-Name | Pflicht |
|---|---|---|---|
| **Einschalten-Button** | `button.*` | `button.mein_wechselrichter_turn_on` | ✅ Pflicht |
| **Ausschalten-Button** | `button.*` | `button.mein_wechselrichter_turn_off` | ✅ Pflicht |
| **Produziert-Sensor** | `binary_sensor.*` | `binary_sensor.mein_wechselrichter_producing` | ✅ Pflicht |
| **Ausgangsleistungs-Sensor** | `sensor.*` | `sensor.mein_wechselrichter_power` | ✅ Pflicht |
| **DPL-Modus-Entity** | `select.*` oder `number.*` | `select.mein_wechselrichter_limit_mode` | Optional |

> **Tipp:** Den DPL musst du nur eintragen wenn du ihn in OpenDTU aktiviert hast und stufenlose Regelung willst. Für die meisten Nutzer reicht die einfache Ein/Aus-Steuerung über die Buttons vollkommen aus.

> **Mehrere Wechselrichter?** Wenn du mehrere Hoymiles-Einheiten hast, richte den Optimizer mit dem Wechselrichter ein der an der Batterie hängt. Die anderen tauchen als Solar-Sensoren auf.

---

### Profil B — Powerstation (EcoFlow, Anker, Bluetti ...)

**Für wen ist dieses Profil?**
Du hast eine **All-in-One-Powerstation** — ein Gerät das Solareingang, Batterie und Wechselrichter in einem Gehäuse vereint. Typische Beispiele: EcoFlow Delta Pro, EcoFlow DELTA 2, Anker SOLIX C800, Bluetti AC200P, Jackery Explorer. Diese Geräte steuern Laden und Entladen intern — du kannst von außen nur sagen wie viel Leistung sie maximal ausgeben sollen.

**Wie steuert der Optimizer die Powerstation?**

Statt einen Wechselrichter ein/auszuschalten, setzt der Optimizer die **Entladeleistung** in Watt:

- **Optimizer sagt „entladen"** → Entladeleistung wird auf die konfigurierte Maximalleistung gesetzt (z. B. 800 W)
- **Optimizer sagt „nicht entladen"** → Entladeleistung wird auf `0 W` gesetzt

Optional kann er zusätzlich den AC-Ausgang per Schalter aktivieren/deaktivieren und die Ladeleistung anpassen.

**Welche Entities brauchst du?**

| Feld | Entity-Typ | Beschreibung | Pflicht |
|---|---|---|---|
| **Entladeleistung-Entity** | `number.*` | Steuert die maximale Entladeleistung in Watt | ✅ Pflicht |
| **Ladeleistung-Entity** | `number.*` | Steuert die maximale Ladeleistung in Watt | Optional |
| **AC-Ausgang Schalter** | `switch.*` | Aktiviert/deaktiviert den AC-Wechselstromausgang | Optional |
| **Ausgangsleistungs-Sensor** | `sensor.*` | Misst die aktuelle Ausgangsleistung in Watt | Optional (verbessert Überschuss-Berechnung) |

**EcoFlow — Setup-Anleitung**

Installiere die [EcoFlow Cloud Integration für HA](https://github.com/tolwi/hassio-ecoflow-cloud) via HACS. Nach der Einrichtung findest du folgende Entities:

```
Entladeleistung:  number.ecoflow_delta2_max_ac_out_power
Ladeleistung:     number.ecoflow_delta2_ac_charging_power    (optional)
AC-Ausgang:       switch.ecoflow_delta2_ac_enabled           (optional)
Ausgangsleistung: sensor.ecoflow_delta2_ac_out_watts         (optional)
```

> **Hinweis zu EcoFlow:** Die genauen Entity-Namen variieren je nach Gerätemodell. Delta 2, Delta Pro und PowerStation haben teilweise unterschiedliche Namen. Geh in HA unter **Einstellungen → Geräte & Dienste → EcoFlow** und schaue welche `number.*`-Entities für dein Gerät verfügbar sind. Die Entladeleistung heißt meistens `max_ac_out_power` oder `discharge_limit`.

**Anker SOLIX — Setup-Anleitung**

Installiere die [Anker SOLIX Integration für HA](https://github.com/thomluther/hacs-anker-solix) via HACS. Nach der Einrichtung:

```
Entladeleistung:  number.solix_c800_discharge_power
Ausgangsleistung: sensor.solix_c800_ac_out_power             (optional)
```

**Bluetti — Setup-Anleitung**

Für Bluetti gibt es mehrere Integrationen abhängig vom Modell. Für AC200P, AC300, EP600 empfiehlt sich [bluetti-ha](https://github.com/MacroPower/homeassistant-bluetti) oder die offizielle Bluetti App-Integration. Suche nach einer `number.*`-Entity die die AC-Ausgangsleistung oder Entladegrenze steuert.

**Victron — Setup-Anleitung**

Victron-Systeme (z. B. MultiPlus mit Cerbo GX) lassen sich über die [Victron Integration](https://github.com/sfstar/hass-victron) einbinden. Relevant ist die Entity für die AC-Ausgangsleistung oder die ESS-Sollleistung:

```
Entladeleistung:  number.victron_ess_setpoint    (oder ähnlich)
Ausgangsleistung: sensor.victron_ac_out_power
```

> **Genereller Tipp für Profil B:** Öffne in HA **Entwicklerwerkzeuge → Zustände** und filtere nach deinem Gerätename. Suche nach einer `number.*`-Entity deren Name auf `power`, `discharge` oder `out` endet und deren Wert in Watt angegeben ist — das ist deine Entladeleistungs-Entity.

---

### Profil C — Generischer Schalter

**Für wen ist dieses Profil?**
Dieses Profil ist der universellste Weg und funktioniert mit nahezu jedem Gerät, das sich in irgendeiner Form als Switch in Home Assistant abbilden lässt. Es eignet sich für:

- Ältere Wechselrichter ohne eigene HA-Integration, aber mit einem Shelly oder Sonoff dazwischen
- Victron-Systeme die über einen virtuellen Switch gesteuert werden
- Selbstgebaute Setups mit einem Relais
- Jede Powerstation die keine `number.*`-Entity hat, aber einen Ein/Aus-Schalter
- HA-Skripte oder Template-Switches die komplexere Logik kapseln

**Wie steuert der Optimizer das System?**

Ganz simpel: Switch ein = Wechselrichter/Entladung aktiv. Switch aus = Wechselrichter/Entladung gestoppt.

**Welche Entities brauchst du?**

| Feld | Entity-Typ | Beschreibung | Pflicht |
|---|---|---|---|
| **Wechselrichter-Schalter** | `switch.*` | Der Schalter der deinen Wechselrichter oder Speicher ein-/ausschaltet | ✅ Pflicht |
| **Ausgangsleistungs-Sensor** | `sensor.*` | Aktuelle Ausgangsleistung in Watt | Optional |

**Beispiel: Shelly am Wechselrichter**

Du hast einen älteren Wechselrichter ohne HA-Integration, aber einen Shelly 1PM der ihn ein- und ausschaltet:

```
Schalter: switch.shelly_wechselrichter
Sensor:   sensor.shelly_wechselrichter_power   (optional, wenn Shelly 1PM mit Strommessung)
```

**Beispiel: Template-Switch für Powerstation**

Wenn deine Powerstation keine direkte Schalter-Entity hat aber ein Skript ansprechen kann:

```yaml
# In configuration.yaml oder als HA-Helfer
switch:
  - platform: template
    switches:
      batterie_entladen:
        value_template: "{{ is_state('sensor.batterie_discharge_mode', 'active') }}"
        turn_on:
          service: script.batterie_entladen_ein
        turn_off:
          service: script.batterie_entladen_aus
```

Dann trägst du `switch.batterie_entladen` als Wechselrichter-Schalter ein.

---

### Schritt 4: Systemparameter

Diese Werte beschreiben dein Energiesystem und fließen in alle Vorhersage- und Optimierungsberechnungen ein.

| Feld | Empfohlener Wert | Erklärung |
|---|---|---|
| **Wetter-Entity** | `weather.home` | Die Wolkenbedeckungsvorhersage wird für die Solarprognose genutzt. Met.no und Open-Meteo funktionieren sehr gut |
| **Grundverbrauch des Hauses (W)** | `150–400` je nach Haushalt | Der durchschnittliche Stromverbrauch deines Hauses ohne große Sonderverbraucher, in Watt. Tipp: Schau in der Tibber-App wieviel du nachts um 3 Uhr verbrauchst |
| **Maximale Solarleistung (W)** | Summe aller Panele | Die nominale Spitzenleistung deiner gesamten Solaranlage. Beispiel: 10 × 400-W-Module = 4000 W |
| **Extrempreisgrenze (€/kWh)** | `0.35–0.50` | Ab diesem Preis gilt Strom als „extrem teuer". Der Optimizer entlädt die Batterie dann bevorzugt unabhängig vom restlichen Plan |
| **Ausgeschlossene Leistungssensoren** | Optional | Sensoren von Großverbrauchern die beim Berechnen des Grundverbrauchs ignoriert werden sollen — z. B. E-Auto-Wallbox oder Wärmepumpe, damit der Optimizer nicht denkt dein Grundverbrauch sei 3000 W |

> **Warum Großverbraucher ausschließen?** Der Optimizer lernt deinen typischen Hausverbrauch. Wenn du gerade dein E-Auto lädst, sieht er plötzlich 11.000 W statt 300 W Verbrauch. Das würde das Lernmodell verzerren. Schließe deshalb Wallbox und Wärmepumpe aus — der Optimizer berücksichtigt sie dann beim Lernen nicht.

---

### Schritt 5: Überschuss-Verbraucher (optional)

Wenn Solarstrom produziert wird der weder direkt verbraucht noch in der Batterie gespeichert werden kann, würde er ins Netz eingespeist — oft zu sehr schlechten Vergütungen (in Deutschland häufig unter 10 ct/kWh). Stattdessen kannst du Geräte definieren die der Optimizer dann automatisch einschaltet um den Überschuss selbst zu nutzen.

Das System arbeitet mit **drei Prioritätsstufen** und einer separaten Kategorie für den frühen Morgen.

---

#### Primäre Verbraucher (höchste Priorität)

Das sind die Geräte die **zuerst** eingeschaltet werden sobald Solarüberschuss erkannt wird. Sie haben Vorrang vor allem anderen.

**Typische Kandidaten:**
- Warmwasserbereiter / Boiler (großer Verbraucher, ideal für Überschuss)
- Waschmaschine (wenn du sie morgens vorbereitest und sie dann automatisch starten lassen willst)
- Spülmaschine
- Pool-Pumpe oder Pool-Heizung
- Ladestation für E-Bike oder Rasenmähroboter

**Wie es funktioniert:** Sobald der Optimizer mehr Solarproduktion als Hausverbrauch + Batterieladeleistung sieht, schaltet er die primären Verbraucher ein. Er schaltet sie wieder aus wenn der Überschuss wegfällt (z. B. Wolken) — aber erst nach der konfigurierten Mindestlaufzeit.

---

#### Sekundäre Verbraucher (zweite Priorität)

Diese Geräte werden **nur dann** eingeschaltet wenn nach dem Einschalten aller primären Verbraucher noch immer Überschuss übrig ist.

**Typische Kandidaten:**
- Infrarotheizung im Badezimmer
- Luftentfeuchter im Keller
- Lüftungsanlage auf höherer Stufe
- Zusätzliche Pool-Heizung

**Wann sinnvoll:** Wenn du z. B. einen 2000-W-Boiler als primären Verbraucher hast und an sehr sonnigen Tagen noch 1000 W zusätzlicher Überschuss anfällt, kannst du diesen mit einem sekundären Verbraucher nutzen.

---

#### Frühe Überschuss-Verbraucher (Vormittag)

Dies ist eine besondere Kategorie die **unabhängig vom aktuellen Solarüberschuss** funktioniert. Diese Geräte werden schon **früh morgens** eingeschaltet — noch bevor voller Solarüberschuss vorhanden ist — solange die Batterie über einem Mindeststand liegt.

**Idee dahinter:** An einem sonnigen Tag weißt du morgens um 7 Uhr bereits dass du bis mittag mehr produzieren wirst als du brauchst. Du könntest den Boiler jetzt schon vorwärmen, obwohl die Sonne noch nicht voll scheint — weil du weißt dass die Batterie das kurz kompensieren kann und sich später wieder füllt.

**Typische Kandidaten:**
- Warmwasserbereiter / Boiler (morgens vorwärmen bevor Duschen)
- Fußbodenheizung im Bad
- Geräte die eine Vorlaufzeit brauchen

**Konfigurationsfelder für frühe Verbraucher:**

| Feld | Empfehlung | Erklärung |
|---|---|---|
| **Erwarteter Verbrauch (W)** | Tatsächliche Wattzahl des Geräts | Wieviel die frühen Verbraucher zusammen ziehen — damit der Optimizer abschätzen kann ob genug Solar kommen wird |
| **Mindest-Batteriestand (%)** | `25–40 %` | Die Batterie muss mindestens diesen Stand haben bevor frühe Verbraucher eingeschaltet werden. Schutz vor Entladen bei trübem Morgen |

---

#### Alle konfigurierbaren Felder für Überschuss-Verbraucher

| Feld | Beschreibung | Empfehlung |
|---|---|---|
| **Primäre Verbraucher** | `switch.*`-Entities, höchste Priorität | Geräte mit hohem Verbrauch die viel Überschuss aufnehmen können |
| **Sekundäre Verbraucher** | `switch.*`-Entities, zweite Priorität | Geräte die nur bei sehr viel Überschuss laufen sollen |
| **Frühe Überschuss-Verbraucher** | `switch.*`-Entities, morgens | Geräte die Vorlaufzeit brauchen |
| **Erwarteter Verbrauch früher Verbraucher (W)** | Wattzahl der frühen Geräte | z. B. `2000` für einen 2-kW-Boiler |
| **Mindest-Batteriestand für frühe Verbraucher (%)** | Prozent | z. B. `30` — Schutz bei trübem Morgen |
| **Mindestlaufzeit pro Einschaltvorgang (min)** | Minuten | z. B. `10`–`20` — verhindert nervöses Ein-/Ausschalten bei wechselhafter Bewölkung |

> **Tipp:** Geräte die in HA als Switch vorhanden sind können direkt eingetragen werden. Wenn dein Boiler keinen Switch hat, lässt er sich oft über einen Shelly oder Sonoff nachrüsten — dann erscheint er als `switch.*` in HA.

> **Tipp Mindestlaufzeit:** Setze diesen Wert nicht zu niedrig. Bei 5 Minuten könnte eine kurze Wolke dazu führen dass der Boiler ständig ein- und ausgeschaltet wird. 10–15 Minuten sind ein guter Kompromiss zwischen Reaktionsgeschwindigkeit und Stabilität.

---

### Schritt 6: Netzladegerät (optional)

Wenn du ein separates Ladegerät hast das die Batterie aus dem Netz lädt (z. B. ein MPPT-Ladegerät mit externer Steuerung, ein Victron-Ladegerät oder ein DIY-Ladegerät mit Shelly), kannst du es hier einrichten.

Der Optimizer schaltet das Ladegerät dann automatisch ein wenn der Tibber-Preis günstig genug ist und die Batterie noch Kapazität hat.

| Feld | Beschreibung |
|---|---|
| **Netzladegerät-Schalter** | `switch.*`-Entity die das Ladegerät ein-/ausschaltet. Kann leer bleiben wenn nicht vorhanden |
| **Ladeleistung (W)** | Wieviel Watt das Ladegerät aus dem Netz zieht — wichtig für die Kostenberechnung |

> **Hinweis für Powerstation-Nutzer:** Wenn du Profil B gewählt hast und dort bereits eine Ladeleistungs-Entity eingetragen hast, ist dieses Feld optional. Der Optimizer steuert die Ladeleistung dann direkt über die Number-Entity.

---

## Wie funktioniert die Optimierung?

### Der Zyklus

Der Optimizer läuft alle **15 Minuten** und durchläuft folgende Schritte:

1. **Tibber-Preise abrufen** — Stündliche Preisvorhersage für die nächsten 24–48 Stunden
2. **Solarvorhersage berechnen** — Auf Basis der Wettervorhersage (Wolkenbedeckung) und des gelernten Solarprofils deiner Anlage
3. **Verbrauchsvorhersage berechnen** — Auf Basis des gelernten Verbrauchsprofils deines Hauses (Wochentag, Uhrzeit)
4. **Optimalen Plan erstellen** — Für jede Stunde wird berechnet: Soll die Batterie laden, entladen oder im Standby bleiben?
5. **Aktuellen Block ausführen** — Der Befehl für das laufende 15-Minuten-Fenster wird an deinen Wechselrichter oder deine Powerstation gesendet

### Der Lernalgorithmus

Nach einigen Tagen kennt der Optimizer dein System immer besser:

- **Solarlernen**: Er weiß dass bei 30 % Bewölkung im Juli zwischen 10 und 14 Uhr typischerweise 2400 W produziert werden
- **Verbrauchslernen**: Er weiß dass montags zwischen 7 und 8 Uhr dein Haus 850 W verbraucht (weil du morgens duschst und Kaffee kochst)

Diese Vorhersagen werden täglich aktualisiert und verbessern sich kontinuierlich.

### Laderaum-Vorbereitung (Proaktive Entladung)

Wenn morgen ein sehr sonniger Tag erwartet wird, ist es manchmal sinnvoll die Batterie am heutigen Abend etwas zu entladen — damit morgens früh mehr Platz für Solarstrom ist. Der Optimizer erkennt diese Situationen und kann die Batterie proaktiv leeren. Diese Funktion kann mit dem Schalter **„Laderaum vorbereiten"** aktiviert werden.

---

## Klimageräte & Wärmepumpen — Verbrauchslernen

Klimaanlagen und Wärmepumpen sind oft die größten Stromverbraucher im Haus — und ihr Verbrauch schwankt stark je nach Außentemperatur. An einem Wintertag mit -5°C zieht eine Wärmepumpe vielleicht 2500 W, an einem milden Tag mit 10°C nur 800 W. Ohne dieses Wissen würde der Optimizer den Verbrauch falsch einschätzen und die Batterie zu früh oder zu spät entladen.

### Was der Optimizer macht (und was nicht)

**Der Optimizer steuert Klimageräte und Wärmepumpen nicht.** Er schaltet sie nicht ein oder aus und ändert keine Temperatureinstellungen. Das bleibt vollständig in deiner Hand.

Was er stattdessen tut: Er **lernt wie viel Strom dein Klimagerät oder deine Wärmepumpe verbraucht** — in Abhängigkeit von der Außentemperatur und der eingestellten Solltemperatur. Dieses Wissen fließt in die Verbrauchsvorhersage ein, damit der Optimizer weiß: „Morgen früh wird es kalt, die Wärmepumpe zieht mehr — also halte ich die Batterie besser voll."

### Das W/°C-Modell

Der Lernalgorithmus arbeitet mit einem **Watts-pro-Grad-Koeffizient (W/°C)**:

- **Heizen:** Wie viel Watt zieht das Gerät pro Grad Temperaturdifferenz zwischen Außentemperatur und Solltemperatur? Beispiel: Außen 0°C, Solltemperatur 20°C → Differenz 20°C. Wenn das Gerät dabei 1200 W zieht, ergibt das einen W/°C-Wert von 60.
- **Kühlen:** Dasselbe Prinzip umgekehrt — wie viel Watt pro Grad Differenz bei Kühlung?
- **Wärmepumpen (Heizen + Kühlen):** Beide Werte werden separat gelernt und je nach Betriebsmodus angewendet.

Dieser Koeffizient startet mit deiner manuellen Schätzung (die du beim Einrichten eingibst) und wird mit jeder gemessenen Viertelstunde präziser. Nach einigen Wochen kennt der Optimizer das Gerät besser als du.

### Einrichtung

Klimageräte werden über **Einstellungen → Geräte & Dienste → Smart Battery Optimizer → Konfigurieren → Klimageräte** hinzugefügt.

Pro Gerät gibst du an:

| Feld | Beschreibung |
|---|---|
| **Name** | Frei wählbar, z. B. „Wärmepumpe Haus" |
| **Gerätetyp** | „Nur Heizen", „Nur Kühlen" oder „Wärmepumpe (Heizen+Kühlen)" |
| **Solltemperatur** | Die typisch eingestellte Zieltemperatur (wird für die W/°C-Berechnung verwendet) |
| **Geschätzte Leistung (W)** | Deine erste Schätzung des Verbrauchs — der Algorithmus verfeinert das automatisch |
| **Leistungs-Sensor** | Optional: Ein `sensor.*` der die aktuelle Leistungsaufnahme des Geräts in Watt misst. Wenn vorhanden, lernt der Algorithmus deutlich schneller |

> **Tipp:** Einen Leistungs-Sensor bekommst du entweder direkt von der Wärmepumpen-Integration (viele moderne Wärmepumpen liefern das), oder durch einen Shelly EM / Shelly Plus 1PM der zwischen Sicherungskasten und Wärmepumpe eingebaut ist.

> **Tipp für Wärmepumpen:** Wähle immer „Wärmepumpe (Heizen+Kühlen)" auch wenn du sie aktuell nur zum Heizen nutzt. So kann der Optimizer bei zukünftigem Kühlbetrieb sofort mit einem vernünftigen Startmodell arbeiten.

---

## Geräteplanung — Waschmaschine & Co. zum günstigsten Preis

Neben der Echtzeit-Steuerung der Batterie kann der Optimizer auch **große Einmal-Verbraucher** wie Waschmaschinen, Spülmaschinen oder Trockner auf den günstigsten Startzeitpunkt innerhalb eines Zeitfensters planen.

### Das Prinzip

Du sagst dem Optimizer: „Ich möchte dass meine Waschmaschine bis **spätestens 8:00 Uhr** fertig ist." Der Optimizer schaut sich die Tibber-Preisvorhersage an und berechnet das günstigste Startzeitfenster — z. B. Programmstart um 2:30 Uhr weil der Strom dann am billigsten ist.

Er berücksichtigt dabei:
- Die **Programmdauer** des gewählten Waschprogramms
- Den **stündlichen Leistungsverlauf** des Programms (Waschmaschinen ziehen am Anfang und beim Schleudern besonders viel)
- Die **Tibber-Preisvorhersage** für die nächsten Stunden
- Den **aktuellen Batteriestand** — wenn die Batterie eh entladen wird, kann auch ein etwas teureres Zeitfenster günstiger sein als Netzstrom

### Programm-Lernen

Der Optimizer lernt die Lastprofile deiner Geräte automatisch. Beim ersten Mal startest du ein Programm manuell — der Optimizer beobachtet den Leistungsverlauf über die gesamte Laufzeit und speichert ihn als „Programm-Profil". Beim nächsten Mal kennt er die genaue Dauer und den typischen Verbrauch dieses Programms.

Mit jedem Durchlauf wird das Profil präziser. Nach 3–5 Wäschen kennt er das Programm sehr gut.

### Einrichtung

Die Geräteplanung wird über das Feld **„Erweiterte Gerätekonfiguration (JSON)"** im Schritt „Überschuss-Verbraucher" eingerichtet. Das ist ein fortgeschrittenes Feature für Nutzer die bereit sind etwas JSON zu schreiben.

Beispiel für eine Waschmaschine:

```json
[
  {
    "name": "Waschmaschine",
    "sensor": "sensor.waschmaschine_leistung",
    "type": "washer"
  }
]
```

| Feld | Beschreibung |
|---|---|
| `name` | Anzeigename des Geräts |
| `sensor` | `sensor.*`-Entity die die aktuelle Leistungsaufnahme in Watt misst (z. B. von einem Shelly) |
| `type` | Gerätetyp: `washer` (Waschmaschine), `dishwasher` (Spülmaschine), `dryer` (Trockner) |

### Planung auslösen

Sobald das Gerät eingerichtet ist, erscheint in HA eine neue Entity mit der du den nächsten Lauf planen kannst. Du wählst:
1. Das gewünschte **Programm** (wird erkannt sobald der Optimizer es einmal gesehen hat)
2. Den **spätesten Fertigstellungszeitpunkt** (Deadline)

Der Optimizer plant den Start automatisch und zeigt dir im Dashboard wann er das Gerät starten wird.

> **Voraussetzung:** Du brauchst einen Leistungs-Sensor am Gerät — z. B. einen **Shelly Plus 1PM** oder **Shelly EM** in der Steckdose oder im Sicherungskasten. Ohne Leistungsmessung kann weder gelernt noch geplant werden.

> **Tipp:** Viele Steckdosen-Zwischenstecker haben eine integrierte Leistungsmessung (z. B. Shelly Plug S, NOUS A1T, Tasmota-basierte Stecker). Das ist oft die einfachste Lösung für Waschmaschine und Spülmaschine.

---

## Alle Steuerungsentitäten im Überblick

Nach der Einrichtung erscheinen folgende Entities in HA unter dem Gerät „Smart Battery Optimizer":

### Hauptschalter

| Entity | Typ | Beschreibung |
|---|---|---|
| **Optimizer aktiv** | Switch | Der Hauptschalter — ausschalten deaktiviert die gesamte automatische Steuerung |
| **Überschuss-Automatik** | Switch | Aktiviert/deaktiviert die automatische Steuerung der Überschuss-Verbraucher |
| **Netzladen erlauben** | Switch | Erlaubt dem Optimizer bei günstigen Preisen aus dem Netz zu laden |
| **Laderaum vorbereiten** | Switch | Aktiviert die proaktive Entladung vor sonnigen Tagen |

### Statussensoren

| Entity | Typ | Beschreibung |
|---|---|---|
| **Aktueller Betriebsmodus** | Sensor (Text) | Was der Optimizer gerade tut, z. B. „Günstig laden bis 14:00", „Dispatch aktiv (0.41€): DTU an" |
| **Heutiger Stundenplan** | Sensor (JSON) | Der vollständige Tagesplan mit allen geplanten Aktionen pro Stunde |
| **Nächste geplante Aktion** | Sensor | Wann der nächste Umschaltzeitpunkt ist |
| **Heutige Ersparnis (€)** | Sensor | Berechnete Ersparnis durch den Optimizer heute |
| **Gesamtersparnis (€)** | Sensor | Kumulierte Ersparnis seit Inbetriebnahme |

### Konfigurationsparameter

Diese Entities erlauben dir Parameter direkt in HA anzupassen ohne den Setup-Assistenten zu öffnen:

| Entity | Typ | Beschreibung |
|---|---|---|
| **Preis-Schwellwert Laden (ct/kWh)** | Number | Unter diesem Preis lädt der Optimizer aus dem Netz |
| **Preis-Schwellwert Entladen (ct/kWh)** | Number | Über diesem Preis entlädt der Optimizer die Batterie |
| **Mindest-Intervall Umschaltung (min)** | Number | Wie oft maximal umgeschaltet werden darf — verhindert Relais-Verschleiß |
| **Überschuss: Einschalten ab (%)** | Number | Ab welchem Solarüberschuss primäre Verbraucher eingeschaltet werden |

---

## Dashboard-Kacheln einrichten

Der Smart Battery Optimizer bringt **vier Dashboard-Kacheln** mit die dir auf einen Blick zeigen was gerade passiert, was geplant ist und wie viel du gespart hast. Alle Kacheln werden automatisch mit der Integration installiert — du musst sie nur noch in dein Dashboard einbauen.

---

### Kachel 1: Batterie & Dispatch Prognose (ApexCharts)

Diese Kachel ist die **Hauptansicht** des Optimizers. Sie zeigt in einem Zeitdiagramm:

- Den **aktuellen und geplanten Batteriestand** als Kurve (linke Y-Achse, %)
- Den **Tibber-Strompreis** als Kurve (rechte Y-Achse, ct/kWh)
- Die **geplante Aktion** für jede Stunde als farbigen Hintergrund

**Farblegende der Aktionen:**

| Farbe | Kürzel | Bedeutung |
|---|---|---|
| 🟢 Grün | DIS | Dispatch aktiv — Batterie entlädt (teuerste Stunden) |
| 🔴 Rot | SAV | Akku sparen — Wechselrichter aus (günstige Stunden) |
| 🟠 Orange | OVF | Überschussvermeidung — Batterie fast voll |
| 🔵 Blau | LAD | Netzladen — Strom ist sehr günstig |
| 🟡 Hellgrün | FUL | Batterie voll |
| 🟣 Lila | NEG | Negativpreis — Wechselrichter bleibt aus |
| ⬛ Grau | MIN | Batterie am Minimum |
| 🩵 Cyan | PRE | Laderaum-Vorbereitung — Nacht-Entladung vor sonnigem Tag |

**Voraussetzung:** Die Kachel basiert auf `custom:apexcharts-card` das du zuerst via HACS installieren musst:
HACS → Frontend → „ApexCharts Card" suchen → Installieren → HA neu laden.

**Installation der Kachel:**

1. Die Datei `dashboard_card.yaml` aus diesem Repository öffnen
2. Die zwei markierten Zeilen anpassen:
   ```yaml
   entity: sensor.DEIN_AKKU_SENSOR    # <-- z.B. sensor.battery_soc
   entity: sensor.DEIN_PLAN_SENSOR    # <-- z.B. sensor.smart_battery_optimizer_tagesplan_vorhersage
   ```
3. In HA ein Dashboard öffnen → Bearbeiten → Karte hinzufügen → **„Manuell"** wählen
4. Den angepassten YAML-Code einfügen → Speichern

> **Tipp:** Den genauen Namen des Tagesplan-Sensors findest du in HA unter **Einstellungen → Geräte & Dienste → Smart Battery Optimizer → Entities** und suchst nach einer Entity die „Tagesplan" oder „hourly_plan" im Namen hat.

---

### Kachel 2: Battery Forecast Card

Diese Kachel ist die **kompakte Übersichts-Kachel** und zeigt in einer einfachen, übersichtlichen Darstellung:

- Den aktuellen Batteriestand als Balkengrafik
- Die geplanten Aktionen für die nächsten Stunden als farbige Timeline
- Den aktuellen Strompreis und Betriebsmodus

Sie eignet sich gut für die **Hauptübersicht** deines Dashboards weil sie wenig Platz braucht aber alle wichtigen Infos auf einen Blick zeigt.

**Installation:**

Die Kachel wird automatisch als Lovelace-Ressource registriert wenn die Integration installiert ist. Karte hinzufügen → **„Benutzerdefiniert"** → `custom:battery-forecast-card` auswählen.

Minimale Konfiguration:
```yaml
type: custom:battery-forecast-card
entity: sensor.smart_battery_optimizer_tagesplan_vorhersage
battery_entity: sensor.dein_batteriestand_sensor
```

---

### Kachel 3: Appliance Card (Gerätesteuerung)

Die Appliance Card ist eine **interaktive Steuerkarte** speziell für die Überschuss-Verbraucher. Sie zeigt alle konfigurierten Geräte auf einen Blick und erlaubt es sie manuell zu steuern.

**Was die Karte zeigt:**
- Alle primären, sekundären und frühen Verbraucher in einer Liste
- Ob jedes Gerät gerade automatisch (vom Optimizer) oder manuell gesteuert wird
- Den aktuellen Schaltzustand jedes Geräts
- Einen visuellen Editor zum Bearbeiten der Gerätezuordnung direkt aus dem Dashboard

**Warum eine eigene Karte?** Die Standard-HA-Schalter zeigen nur Ein/Aus. Die Appliance Card zeigt zusätzlich ob der Optimizer das Gerät gesteuert hat oder ob du manuell eingegriffen hast — das vermeidet Verwirrung wenn ein Gerät unerwwartet ein oder aus ist.

**Installation:**

Wird automatisch als Ressource registriert. Karte hinzufügen → **„Benutzerdefiniert"** → `custom:appliance-card`.

Minimale Konfiguration:
```yaml
type: custom:appliance-card
entity: sensor.smart_battery_optimizer_status
```

---

### Kachel 4: Savings Card (Ersparnis-Statistik)

Die Savings Card zeigt dir **wie viel Geld du durch den Optimizer gespart hast** — aufgeteilt in verschiedene Zeiträume.

**Was die Karte anzeigt:**

- **Heute**: Ersparnis am heutigen Tag
- **7 Tage**: Ersparnis der letzten Woche
- **30 Tage**: Ersparnis des letzten Monats
- **Gesamt**: Gesamtersparnis seit Inbetriebnahme

Die Berechnung basiert auf dem Vergleich: Was hat der Strom mit Optimizer gekostet vs. was hätte er ohne Optimizer gekostet (immer zum aktuellen Tibber-Preis kaufen).

**Installation:**

Wird automatisch als Ressource registriert. Karte hinzufügen → **„Benutzerdefiniert"** → `custom:savings-card`.

Minimale Konfiguration:
```yaml
type: custom:savings-card
entity: sensor.smart_battery_optimizer_ersparnis
```

---

### Komplettes Dashboard-Beispiel

So könnte ein vollständiges Dashboard aussehen:

```yaml
title: Energie
views:
  - title: Batterie
    cards:
      # Hauptkarte: Prognose-Chart
      - type: custom:battery-forecast-card
        entity: sensor.smart_battery_optimizer_tagesplan_vorhersage
        battery_entity: sensor.dein_batteriestand_sensor

      # Ersparnis-Statistik
      - type: custom:savings-card
        entity: sensor.smart_battery_optimizer_ersparnis

      # Gerätesteuerung
      - type: custom:appliance-card
        entity: sensor.smart_battery_optimizer_status

      # Hauptschalter als einfache Entitätskarte
      - type: entities
        title: Optimizer Steuerung
        entities:
          - entity: switch.smart_battery_optimizer_aktiv
          - entity: switch.smart_battery_optimizer_ueberschuss_automatik
          - entity: switch.smart_battery_optimizer_netzladen
          - entity: switch.smart_battery_optimizer_laderaum_vorbereiten
          - entity: sensor.smart_battery_optimizer_betriebsmodus

      # Detaillierter ApexCharts-Chart (braucht apexcharts-card aus HACS)
      # → Inhalt aus dashboard_card.yaml einfügen
```

---

## Einstellungen nachträglich ändern

Alle Einstellungen lassen sich jederzeit anpassen ohne die Integration neu installieren zu müssen:

1. **Einstellungen → Geräte & Dienste**
2. Bei „Smart Battery Optimizer" auf **„Konfigurieren"** klicken
3. Im Menü den gewünschten Bereich auswählen:
   - Tibber Energietarif
   - Batterie
   - Solar & Wechselrichter *(hier kannst du auch das Profil wechseln)*
   - Systemparameter
   - Überschuss-Verbraucher
   - Netzladegerät

> **Profil wechseln:** Wenn du z. B. von OpenDTU auf eine Powerstation umsteigst, geh einfach auf „Solar & Wechselrichter", wähle das neue Profil und trage die neuen Entities ein. Die bisherigen Einstellungen bleiben gespeichert.

---

## Tipps für den Alltag

**Die ersten Tage**
Lass den Optimizer die ersten 3–7 Tage laufen ohne einzugreifen. Der Lernalgorithmus braucht einige Tage um deinen Verbrauch und deine Solarproduktion zu verstehen. Die Optimierung wird mit jedem Tag besser.

**Den Plan prüfen**
Schau dir täglich kurz den Sensor „Heutiger Stundenplan" an. Er zeigt dir genau wann der Optimizer plant zu laden und wann zu entladen. So erkennst du ob die Vorhersage realistisch ist.

**Betriebsmodus beobachten**
Der Sensor „Aktueller Betriebsmodus" zeigt dir in Echtzeit was der Optimizer gerade tut und warum. Das ist besonders hilfreich beim Einrichten.

**Großverbraucher nicht vergessen**
Wenn du regelmäßig dein E-Auto lädst oder eine Wärmepumpe hast, trag diese unbedingt unter „Ausgeschlossene Leistungssensoren" ein. Sonst lernt der Optimizer einen falschen Grundverbrauch.

**Urlaubsmodus**
Wenn du längere Zeit weg bist: Optimizer aktiv lassen, aber „Netzladen erlauben" ausschalten wenn du nicht willst dass in deiner Abwesenheit geladen wird.

---

## Fehlerbehebung

**Der Optimizer tut nichts / Betriebsmodus zeigt immer dasselbe**

Prüfe ob der Optimizer-Hauptschalter eingeschaltet ist. Prüfe die HA-Logs unter **Einstellungen → System → Logs** nach Einträgen von `smart_battery_optimizer`.

**Die Buttons/Switches werden nicht erkannt**

Geh zu **Entwicklerwerkzeuge → Zustände** und suche nach dem Entity-Namen den du eingetragen hast. Prüfe ob er existiert und einen gültigen Zustand hat (nicht `unavailable` oder `unknown`).

**Der Wechselrichter schaltet sich zu oft um**

Erhöhe den Parameter „Mindest-Intervall Umschaltung" unter den Konfigurationsparametern. Standard sind 15 Minuten — du kannst auf 30 oder 60 Minuten erhöhen.

**Falsche Verbrauchsvorhersage**

Stelle sicher dass alle Großverbraucher unter „Ausgeschlossene Leistungssensoren" eingetragen sind. Der Lernalgorithmus braucht außerdem mindestens eine Woche um gute Vorhersagen zu liefern.

**Powerstation (Profil B) reagiert nicht**

Prüfe ob die eingetragene `number.*`-Entity wirklich die Entladeleistung steuert indem du in **Entwicklerwerkzeuge → Dienste** den Service `number.set_value` manuell mit deiner Entity und Wert `0` bzw. `800` aufrufst und beobachtest ob die Powerstation reagiert.

---

## FAQ

**Funktioniert der Optimizer ohne Tibber?**
Nein. Tibber-Preise bilden die Basis aller Optimierungsentscheidungen. Die Tibber-Integration für HA muss installiert und mit deinem Konto verbunden sein.

**Muss ich Tibber Pulse haben?**
Du brauchst einen Sensor der den aktuellen Strombezug und die Einspeisung in Watt misst. Tibber Pulse ist die einfachste Option. Alternativ funktioniert jeder andere Smartmeter der in HA als Power-Sensor verfügbar ist.

**Verliere ich meine Einstellungen wenn ich auf eine neue Version update?**
Nein. Alle Einstellungen werden im HA-Config-Entry gespeichert und bleiben bei Updates erhalten. Bestehende Installationen die vor der Mehrprofil-Unterstützung eingerichtet wurden, bleiben auf dem OpenDTU-Profil (rückwärtskompatibel).

**Kann ich mehrere Instanzen installieren (z. B. für zwei verschiedene Speicher)?**
Aktuell ist die Integration für eine Instanz pro HA-Installation ausgelegt. Mehrere Speicher könnten in einer zukünftigen Version unterstützt werden.

**Meine Powerstation hat keine `number.*`-Entity. Was nun?**
Wechsle zu Profil C (Generischer Schalter) und erstelle in HA einen Template-Switch oder ein Helfer-Skript das deine Powerstation anspricht. Damit lässt sich fast jede Powerstation anbinden.

**Wie genau ist die Solarvorhersage?**
In den ersten Tagen noch ungenau, nach 1–2 Wochen lernt der Algorithmus deine Anlage kennen. Die Genauigkeit hängt außerdem stark von der Qualität deiner Wetter-Entity ab. Open-Meteo und Met.no liefern sehr gute Ergebnisse.

**Kann ich den Wechselrichter-Typ nachträglich ändern?**
Ja, jederzeit. Einstellungen → Geräte & Dienste → Smart Battery Optimizer → Konfigurieren → Solar & Wechselrichter → Profil wechseln.

**Der Optimizer lädt nie aus dem Netz obwohl der Preis günstig ist.**
Stelle sicher dass der Schalter „Netzladen erlauben" eingeschaltet ist. Prüfe außerdem ob ein Netzladegerät konfiguriert ist (Schritt 6) — ohne Ladegerät-Switch kann der Optimizer nicht aus dem Netz laden.
