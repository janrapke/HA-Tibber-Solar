# Smart Battery Optimizer – Entwicklungsregeln

## Home Assistant MCP Server

In diesem Projekt ist ein Home Assistant MCP Server verfügbar (`homeassistant`). 

**WICHTIG: Nutze AUSSCHLIESSLICH den MCP Server für alle HA-Abfragen. Niemals REST API, curl, Token abfragen oder andere Alternativen verwenden — auch nicht wenn der Server beim Start noch lädt. Warten bis er verfügbar ist.**

Tools: `mcp__homeassistant__list_devices`, `mcp__homeassistant__get_history`, `mcp__homeassistant__control`, `mcp__homeassistant__notify` u.a.

## Neue Entities anlegen

Jede neue Entity MUSS folgende Muster einhalten:

### 1. EntityCategory immer setzen

```python
from homeassistant.const import EntityCategory
```

| Entity-Typ | EntityCategory | Wann |
|---|---|---|
| Aktive Steuerung (täglich genutzt) | *(keins)* | Optimizer an/aus, Überschuss-Automatik, Netzladen, Urlaubsmodus |
| Konfigurationsparameter | `EntityCategory.CONFIG` | Schwellwerte, Prozentwerte, Zeitintervalle, Feature-Toggles, Klimaslot-Einstellungen |
| Diagnosedaten | `EntityCategory.DIAGNOSTIC` | Read-only Status, interne Zähler |

**Faustregel:** Wenn der Nutzer den Wert einmal setzt und dann vergisst → CONFIG. Wenn er ihn täglich toggled → kein Category.

### 2. Einheitliche deutsche Namensgebung

- Sprache: **immer Deutsch**
- Kein English-Mix (nicht "Force Zero Export", nicht "Enabled")
- Schema für zusammengesetzte Namen: `"Gruppe: Beschreibung (Einheit)"`
  - Beispiel: `"Überschuss primär: Einschalten ab (%)"` ✓
  - Beispiel: `"Netzladen: Wirkungsgrad (%)"` ✓
  - Nicht: `"Primär Überschuss Ein (%)"` ✗ (kein Doppelpunkt, falsche Reihenfolge)
- Einheit immer in Klammern am Ende: `(%)`, `(W)`, `(min)`, `(°C)`, `(€/kWh)`, `(ct/kWh)`
- Namen kurz genug für die HA-Geräteseite: max ~35 Zeichen

### 3. Icons immer setzen

Jede Entity braucht `_attr_icon`. Passende MDI-Icons:

| Kontext | Icon |
|---|---|
| Batterie laden | `mdi:battery-charging-high` |
| Batterie entladen / Grenze nach oben | `mdi:battery-arrow-up` |
| Grenze nach unten | `mdi:battery-arrow-down` |
| Solar | `mdi:solar-power` |
| Wetter/Wolken | `mdi:weather-cloudy` |
| Temperatur | `mdi:thermometer` |
| Timer/Intervall | `mdi:timer-outline` |
| Lernfunktion | `mdi:brain` |
| Preis/Euro | `mdi:currency-eur` |
| Wirkungsgrad/Prozent | `mdi:percent` |
| Schutzpuffer | `mdi:shield-sun` |
| Wärmepumpe/Klima | `mdi:heat-pump` |
| Blitz/Leistung | `mdi:lightning-bolt` |
| Optimizer/Roboter | `mdi:robot` |

### 4. Pflichtstruktur für jede neue Klasse

```python
class MeineNeueEntity(CoordinatorEntity, NumberEntity):  # oder SwitchEntity, SelectEntity etc.

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG  # oder weglassen für Steuerelemente
    _attr_icon = "mdi:..."

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": "Smart Battery Optimizer",
            "manufacturer": "Custom",
        }
        self._attr_unique_id = f"{entry_id}_eindeutiger_key"
        self._attr_name = "Gruppe: Beschreibung (Einheit)"
```

### 5. Klimaslot-Entities (Slots 1–3)

Klimaslot-Entities sind IMMER `EntityCategory.CONFIG` – sie konfigurieren das Gerät, sie steuern es nicht.

Name-Schema: `f"Klimagerät {slot_number} Beschreibung (Einheit)"`

### 6. Config Flow (config_flow.py)

Neue Konfigurationsfelder gehören in den thematisch passenden Step:

| Step | Inhalt |
|---|---|
| `user` | Tibber API-Token + Sensoren |
| `battery` | Batterie-Sensor, Kapazität, Ladegrenzen |
| `solar_inverter` | Solar-Sensoren, OpenDTU-Entities, Wechselrichterleistung |
| `system` | Wetter, Grundverbrauch, Solarspitze, Extrempreisgrenze |
| `excess` | Überschuss-Verbraucher (primär/sekundär/früh) |
| `grid_charger` | Netzladegerät |

Für jedes neue Feld MUSS in **beiden** Übersetzungsdateien ein Eintrag ergänzt werden:
- `translations/de.json` – Label (`data`) + Beschreibung (`data_description`)
- `translations/en.json` – Label (`data`) + Beschreibung (`data_description`)

Die `data_description` erklärt dem Nutzer in einem Satz was das Feld bedeutet und welchen Wert er eintragen soll.
