# Smart Battery Optimizer for Home Assistant

A custom component for Home Assistant that intelligently controls a battery storage system (using Hoymiles via OpenDTU) based on Tibber prices, solar production, and learning your house consumption.

## Features

- **Zero-Export (Nulleinspeisung) Control**: Automatically adjusts OpenDTU limit to match house consumption.
- **Price Optimization**: Uses cheap grid power when available, saving solar energy for expensive hours.
- **Overfill Prevention**: Prevents the battery from reaching 100% too early and wasting solar energy.
- **Learning Algorithm**: Learns your daily house consumption and solar production based on weather.
- **Smart Exclusions**: Exclude heavy loads (like EV chargers) from the learning algorithm.
- **Excess Energy Management**: Turn on prioritized consumers (like pool heaters) when the battery is full and excess solar is available.

## Installation via HACS (Recommended)

1. Open HACS in your Home Assistant.
2. Click on the 3 dots in the top right corner and select **Custom repositories**.
3. Add the URL of this repository.
4. Select **Integration** as the category.
5. Click **Add**.
6. Search for "Smart Battery Optimizer" in HACS and install it.
7. Restart Home Assistant.
8. Go to **Settings > Devices & Services > Add Integration** and search for "Smart Battery Optimizer".

## Configuration

During setup, you will be asked to provide:
- **Tibber Price Sensor**: Your Tibber sensor containing future prices.
- **Tibber Consumption Sensor**: Your Tibber Pulse consumption sensor.
- **Tibber Export Sensor**: Your Tibber Pulse export sensor.
- **Battery Level Sensor (%)**: Your battery charge percentage sensor.
- **Solar Power Sensor (W)**: Current total solar production.
- **OpenDTU Limit Entity**: The `number` entity to control the inverter limit.
- **Weather Entity**: Used for cloud cover prediction (e.g., Met.no or Open-Meteo).
- **Battery Capacity**: In Wh (e.g., 5000 for a 5kWh battery).
- **Minimum Battery %**: The minimum level to keep in the battery (e.g., 10%).
- **Excluded Sensors**: (Optional) Power sensors to ignore for house load calculation.
- **Prioritized Consumers**: (Optional) Switches to turn on when excess power is available.
