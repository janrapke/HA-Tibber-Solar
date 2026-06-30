# Smart Battery Optimizer for Home Assistant

Ein Custom Component für Home Assistant, das einen Batteriespeicher intelligent auf Basis von Tibber-Preisen, Solarproduktion und gelerntem Hausverbrauch steuert.

**Kompatibel mit:** OpenDTU/Hoymiles · EcoFlow · Anker · Bluetti · Victron · und jedem anderen Speicher mit HA-Integration

---

## Inhaltsverzeichnis

- [Features](#features)
- [Installation](#installation)
- [Einrichtung: Schritt für Schritt](#einrichtung-schritt-für-schritt)
  - [Schritt 1: Tibber](#schritt-1-tibber)
  - [Schritt 2: Batterie](#schritt-2-batterie)
  - [Schritt 3: Solar & Wechselrichter-Typ](#schritt-3-solar--wechselrichter-typ)
  - [Profil A: OpenDTU / Hoymiles](#profil-a-opendtu--hoymiles)
  - [Profil B: Powerstation (EcoFlow, Anker, Bluetti ...)](#profil-b-powerstation-ecoflow-anker-bluetti-)
  - [Profil C: Generischer Schalter](#profil-c-generischer-schalter)
  - [Schritt 4: Systemparameter](#schritt-4-systemparameter)
  - [Schritt 5: Überschuss-Verbraucher (optional)](#schritt-5-überschuss-verbraucher-optional)
  - [Schritt 6: Netzladegerät (optional)](#schritt-6-netzladegerät-optional)
- [Wie funktioniert die Optimierung?](#wie-funktioniert-die-optimierung)
- [Steuerungsentitäten](#steuerungsentitäten)
- [FAQ](#faq)

---

## Features

- **Preisoptimierung**: Lädt günstig aus dem Netz, entlädt teuer — auf Basis stündlicher Tibber-Preise
- **Solarüberschuss-Management**: Schaltet priorisierte Verbraucher ein wenn Batterie voll und Sonne scheint
- **Lernalgorithmus**: Lernt deinen täglichen Hausverbrauch und die Solarproduktion in Abhängigkeit vom Wetter
- **Laderaum-Vorbereitung**: Entlädt die Batterie proaktiv vor einem sonnigen Tag um Platz für Solarstrom zu schaffen
- **Klimagerät-Integration**: Steuert Wärmepumpen und Klimaanlagen optimal nach Preis und Wetter
- **Kompatibel mit allen Speichern**: OpenDTU, Powerstations und generische Schalter werden unterstützt

---

## Installation

### Via HACS (empfohlen)

1. HACS in Home Assistant öffnen
2. Drei-Punkte-Menü oben rechts → **„Benutzerdefinierte Repositories"**
3. Repository-URL eintragen: `https://github.com/janrapke/HA-Tibber-Solar`
4. Kategorie: **Integration** → Hinzufügen
5. Dialog schließen → nach **„Smart Battery Optimizer"** suchen → Installieren
6. Home Assistant neu starten
7. **Einstellungen → Geräte & Dienste → Integration hinzufügen** → „Smart Battery Optimizer"

### Manuell

Den Ordner `custom_components/smart_battery_optimizer` in dein HA-`custom_components`-Verzeichnis kopieren und HA neu starten.

---

## Einrichtung: Schritt für Schritt

### Schritt 1: Tibber

| Feld | Beschreibung |
|---|---|
| **Tibber API-Token** | Unter app.tibber.com → Konto → Entwickler |
| **Strompreissensor** | Tibber-Sensor mit aktuellem Preis (z. B. `sensor.tibber_pulse_aktueller_stundenstrompreis`) |
| **Netzverbrauchssensor** | Sensor für Strombezug in Watt (Tibber Pulse) |
| **Netzeinspeisesensor** | Sensor für Einspeisung in Watt (oft gleicher Sensor mit negativen Werten) |

### Schritt 2: Batterie

| Feld | Empfehlung |
|---|---|
| **Batteriestand-Sensor** | Sensor 0–100 % — von deiner Batterie-Integration |
| **Kapazität (Wh)** | z. B. `5000` für eine 5-kWh-Batterie |
| **Minimaler Ladestand** | `10–20 %` als Notreserve |
| **Maximaler Ladestand für Netzladung** | `80–95 %` — Solarüberschuss kann darüber hinaus laden |
| **Wirkungsgrad** | `90 %` typisch |

### Schritt 3: Solar & Wechselrichter-Typ

Hier gibst du zuerst deine Solarsensoren an und wählst dann deinen **Speicher-/Wechselrichter-Typ**:

| Feld | Beschreibung |
|---|---|
| **Solarleistungs-Sensoren** | Ein oder mehrere Sensoren in Watt (Mehrfachauswahl möglich) |
| **Balkonkraftwerk-Sensor** | Optional: separater Sensor für Stecker-PV ohne Speicher |
| **Maximale Wechselrichterleistung** | In Deutschland 800 W für Stecker-PV |
| **Speicher-/Wechselrichter-Typ** | Profil auswählen → sieh unten |

---

### Profil A: OpenDTU / Hoymiles

**Für wen:** Wer einen Hoymiles-Mikrowechselrichter mit OpenDTU-Firmware betreibt.

**Wie es funktioniert:** Der Optimizer drückt per Button-Entity auf Ein/Aus und kann optional den Dynamic Power Limiter (DPL) steuern um die Ausgangsleistung stufenlos zu regeln.

**Benötigte Entities in HA** (werden von der OpenDTU-Integration bereitgestellt):

| Entity-Typ | Beispiel | Pflicht |
|---|---|---|
| `button.*` Einschalten | `button.dtu_inverter_turn_on` | ✅ |
| `button.*` Ausschalten | `button.dtu_inverter_turn_off` | ✅ |
| `binary_sensor.*` Produziert | `binary_sensor.dtu_inverter_producing` | ✅ |
| `sensor.*` Ausgangsleistung (W) | `sensor.dtu_inverter_power` | ✅ |
| `select.*` oder `number.*` DPL-Modus | `select.dtu_inverter_limit_mode` | Optional |

**Tipp:** Das DPL-Feld leer lassen wenn du nur Ein/Aus-Steuerung brauchst.

---

### Profil B: Powerstation (EcoFlow, Anker, Bluetti ...)

**Für wen:** Wer eine All-in-One-Powerstation hat (kein separater Wechselrichter). Die Station lädt und entlädt selbst.

**Wie es funktioniert:** Der Optimizer setzt die **Entladeleistung** auf `0 W` (Batterie aus) oder auf die konfigurierte Maximalleistung (Batterie an). Optional kann er auch den AC-Ausgang per Schalter aktivieren.

**Benötigte Entities in HA** (werden von der jeweiligen HA-Integration bereitgestellt):

| Entity-Typ | Beschreibung | Pflicht |
|---|---|---|
| `number.*` Entladeleistung | Maximale Entladeleistung in Watt | ✅ |
| `number.*` Ladeleistung | Maximale Ladeleistung in Watt | Optional |
| `switch.*` AC-Ausgang | Aktiviert den Wechselstrom-Ausgang | Optional |
| `sensor.*` Ausgangsleistung | Aktuelle Ausgangsleistung in Watt | Optional |

**EcoFlow-Beispiel** (mit der [EcoFlow Cloud HA-Integration](https://github.com/tolwi/hassio-ecoflow-cloud)):

```
number.ecoflow_delta2_discharge_limit  → Entladeleistung
number.ecoflow_delta2_charge_limit     → Ladeleistung (optional)
switch.ecoflow_delta2_ac_enabled       → AC-Ausgang (optional)
sensor.ecoflow_delta2_ac_out_watts     → Ausgangsleistung (optional)
```

**Anker SOLIX-Beispiel** (mit der [Anker SOLIX HA-Integration](https://github.com/thomluther/hacs-anker-solix)):

```
number.anker_solix_discharge_power     → Entladeleistung
sensor.anker_solix_output_power        → Ausgangsleistung (optional)
```

> **Hinweis:** Die genauen Entity-Namen hängen von der jeweiligen HA-Integration ab. Suche in HA unter **Einstellungen → Geräte & Dienste** nach deiner Powerstation und prüfe welche `number.*`-Entities verfügbar sind.

---

### Profil C: Generischer Schalter

**Für wen:** Jeder der seinen Wechselrichter oder Speicher über einen einfachen Ein/Aus-Schalter in HA steuern kann. Das ist der universellste Weg — funktioniert mit nahezu allem.

**Wie es funktioniert:** Der Optimizer schaltet den konfigurierten Switch einfach ein oder aus.

**Benötigte Entities:**

| Entity-Typ | Beschreibung | Pflicht |
|---|---|---|
| `switch.*` Wechselrichter | Schaltet den Wechselrichter / die Entladung ein/aus | ✅ |
| `sensor.*` Ausgangsleistung | Aktuelle Ausgangsleistung in Watt | Optional |

**Beispiele:**
- Ein Shelly-Schalter am Wechselrichter
- Ein Victron-System das per `switch.*` gesteuert wird
- Eine selbst erstellte HA-Helfer-Entity die ein Skript triggert

---

### Schritt 4: Systemparameter

| Feld | Empfehlung |
|---|---|
| **Wetter-Entity** | `weather.home` (Met.no oder Open-Meteo) |
| **Grundverbrauch (W)** | Durchschnittsverbrauch nachts — in der Tibber-App ablesbar |
| **Maximale Solarleistung (W)** | Summe aller Panelleistungen |
| **Extrempreisgrenze (€/kWh)** | Ab wann Strom „extrem teuer" ist, typisch `0.40` |
| **Ausgeschlossene Sensoren** | Optional: Wallbox, Wärmepumpe etc. |

### Schritt 5: Überschuss-Verbraucher (optional)

Geräte, die automatisch eingeschaltet werden wenn Solarüberschuss vorhanden ist:

- **Primäre Verbraucher**: Höchste Priorität (z. B. Waschmaschine, Spülmaschine)
- **Sekundäre Verbraucher**: Wenn nach den primären noch Überschuss bleibt
- **Frühe Überschuss-Verbraucher**: Schalten schon morgens bei geladenem Akku

### Schritt 6: Netzladegerät (optional)

Wenn du ein separates Netzladegerät hast das per Switch gesteuert wird:

| Feld | Beschreibung |
|---|---|
| **Netzladegerät-Schalter** | `switch.*` Entity des Ladegeräts |
| **Ladeleistung (W)** | Wieviel Watt das Ladegerät zieht |

---

## Wie funktioniert die Optimierung?

Der Optimizer läuft alle 15 Minuten und berechnet:

1. **Tibber-Preisvorhersage** für die nächsten 24 Stunden abrufen
2. **Solarvorhersage** aus Wetterbedeckung + gelernter Solarhistorie
3. **Verbrauchsvorhersage** aus gelernter Hausverbrauchshistorie
4. **Optimalen Lade-/Entladeplan** berechnen: Wann ist Strom günstig genug zum Speichern? Wann teuer genug zum Entladen?
5. **Steuerbefehl** an Wechselrichter oder Powerstation senden

---

## Steuerungsentitäten

Nach der Einrichtung erscheinen folgende Entities im HA-Gerät:

| Entity | Typ | Funktion |
|---|---|---|
| Optimizer aktiv | Switch | Hauptschalter |
| Überschuss-Automatik | Switch | Überschuss-Verbraucher ein/aus |
| Netzladen | Switch | Netzladen erlauben |
| Laderaum vorbereiten | Switch | Proaktiv entladen vor sonnigem Tag |
| Aktueller Betriebsmodus | Sensor | Was der Optimizer gerade macht |
| Nächste Aktion | Sensor | Wann nächste Umschaltung geplant ist |
| Heutiger Stundenplan | Sensor | Vollständiger Tagesplan |

---

## FAQ

**Meine Powerstation hat keine `number.*`-Entity für die Entladeleistung. Was tun?**
→ Profil „Generischer Schalter" verwenden. Mit einem Template-Switch der ein HA-Skript auslöst lässt sich fast jede Powerstation anbinden.

**Ich habe OpenDTU aber kein DPL. Muss ich das ausfüllen?**
→ Nein, DPL ist optional. Ohne DPL schaltet der Optimizer den Wechselrichter hart ein/aus.

**Funktioniert die Integration ohne Tibber?**
→ Nein, Tibber-Preise sind der Kern der Optimierung. Die Tibber-Integration für HA muss installiert sein.

**Bestehende Installation aktualisieren — verliere ich meine Einstellungen?**
→ Nein. Das Profil „OpenDTU" ist der Standard-Rückfallwert für alle bestehenden Installationen ohne explizite Profilauswahl.

**Kann ich den Wechselrichter-Typ nachträglich ändern?**
→ Ja. **Einstellungen → Geräte & Dienste → Smart Battery Optimizer → Konfigurieren → Solar & Wechselrichter**.
