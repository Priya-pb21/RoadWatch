import asyncio
from datetime import datetime
from winrt.windows.devices.geolocation import Geolocator

async def get_live_location():
    locator = Geolocator()

    print("Checking Windows device location...\n")

    while True:
        try:
            position = await locator.get_geoposition_async()

            coordinates = position.coordinate.point.position

            latitude = coordinates.latitude
            longitude = coordinates.longitude
            accuracy = position.coordinate.accuracy

            print("Time:", datetime.now().strftime("%H:%M:%S"))
            print(f"Latitude:  {latitude:.6f}")
            print(f"Longitude: {longitude:.6f}")
            print(f"Accuracy:  {accuracy:.2f} metres")
            print("-" * 35)

        except Exception as error:
            print("Location error:", error)

        await asyncio.sleep(5)

asyncio.run(get_live_location())