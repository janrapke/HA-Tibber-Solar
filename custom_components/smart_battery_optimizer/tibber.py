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
              today {
                total
                energy
                tax
                startsAt
              }
              tomorrow {
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
                raw_prices = today + tomorrow
                if not raw_prices:
                    return []

                # Convert to 15-minute intervals internally
                from datetime import timedelta

                quarter_prices = []
                for price in raw_prices:
                    base_dt = datetime.fromisoformat(price["startsAt"])

                    # Create 4 entries for the hour (00, 15, 30, 45)
                    for quarter in range(4):
                        q_price = price.copy()
                        q_price["datetime"] = base_dt + timedelta(minutes=15 * quarter)
                        quarter_prices.append(q_price)

                return quarter_prices
            except (KeyError, TypeError) as e:
                _LOGGER.error("Failed to parse Tibber API response: %s", e)
                return []

    except Exception as e:
        _LOGGER.error("Error communicating with Tibber API: %s", e)
        return []
