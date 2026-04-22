import logging
from datetime import datetime
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

TIBBER_API_URL = "https://api.tibber.com/v1-beta/gql"

async def fetch_tibber_prices(hass, api_token: str) -> list[dict]:
    """Fetch today's and tomorrow's 15-minute prices from Tibber."""
    query = """
    {
      viewer {
        homes {
          currentSubscription {
            priceInfo {
              today(resolution: QUARTER_HOURLY) {
                total
                energy
                tax
                startsAt
              }
              tomorrow(resolution: QUARTER_HOURLY) {
                total
                energy
                tax
                startsAt
              }
            }
          }
        }
      }
    }
    """

    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json"
    }

    try:
        session = async_get_clientsession(hass)
        async with session.post(TIBBER_API_URL, json={"query": query}, headers=headers) as response:
            if response.status != 200:
                _LOGGER.error("Failed to fetch Tibber prices: HTTP %s", response.status)
                return []

            data = await response.json()

            # Safely extract the prices from the deeply nested GraphQL response
            try:
                homes = data.get("data", {}).get("viewer", {}).get("homes", [])
                if not homes:
                    return []

                price_info = homes[0].get("currentSubscription", {}).get("priceInfo", {})
                today = price_info.get("today", [])
                tomorrow = price_info.get("tomorrow", [])

                # Combine today and tomorrow
                all_prices = today + tomorrow

                # Parse the dates so we can use them easily
                for price in all_prices:
                    # Tibber returns ISO 8601 strings like "2023-10-25T00:00:00.000+02:00"
                    price["datetime"] = datetime.fromisoformat(price["startsAt"])

                return all_prices
            except (KeyError, TypeError) as e:
                _LOGGER.error("Failed to parse Tibber API response: %s", e)
                return []

    except Exception as e:
        _LOGGER.error("Error communicating with Tibber API: %s", e)
        return []
