from H3_utils import latlng_to_h3, get_h3_distance, h3_to_latlng , get_nearest_vehicle_h3
from Helper_func import _safe_datetime_parse, _get_time_minutes, _get_pickup_time_minutes, get_distance
import logging
from Config import Config
from typing import Dict, List, Optional, Tuple
from collections import defaultdict
import requests

logger = logging.getLogger(__name__)



def get_distanceapi(origin_lat, origin_lng, dest_lat, dest_lng, api_key):
    url = "https://maps.googleapis.com/maps/api/directions/json"
    params = {
        "origin": f"{origin_lat},{origin_lng}",
        "destination": f"{dest_lat},{dest_lng}",
        "key": api_key
    }
    response = requests.get(url, params=params)
    data = response.json()
    if data["status"] == "OK":
        # Get distance in meters from the first route/leg
        distance_meters = data["routes"][0]["legs"][0]["distance"]["value"]
        distance_km = distance_meters / 1000.0
        return distance_km
    else:
        raise Exception(f"Google Maps API error: {data['status']}")

# def _get_available_vehicles_for_booking(self, booking: Dict) -> List[int]:
#     """Get available vehicles for a booking based on type, time, and H3 distance (no caching)."""
#     booking_type = booking.get("vehicle_type", "class1")
#     available_vehicles = []
#     try:
#         booking_hex = latlng_to_h3(booking["pickup_lat"], booking["pickup_lon"], self.h3_resolution)
#     except Exception as e:
#         logger.warning(f"Failed to convert booking location to H3: {e}")
#         booking_hex = None

#     for i, vehicle in enumerate(self.vehicles):
#         if vehicle["vehicle_type"] != booking_type:
#             continue
#         try:
#             booking_pickup_min = _get_pickup_time_minutes(booking["pickup_time"])
#             vehicle_available_min = vehicle["available_time"]
#             if not (booking_pickup_min - 30 <= vehicle_available_min <= booking_pickup_min + 30):
#                 continue
#         except Exception as e:
#             logger.warning(f"Failed to check vehicle available_time: {e}")
#             continue
#         if booking_hex:
#             try:
#                 vehicle_hex = vehicle["h3_hex"]
#                 if vehicle_hex:
#                     h3_distance = get_h3_distance(booking_hex, vehicle_hex)
#                     if h3_distance > Config.MAX_H3_DISTANCE:
#                         continue
#             except Exception as e:
#                 logger.warning(f"H3 distance calculation failed: {e}")
#                 continue
#         available_vehicles.append(i)
#     return available_vehicles

def _calculate_ddm(route: List[Tuple[float, float]], home_lat: float, home_lng: float) -> float:
        if len(route) < 2:
            return 0.0
        
        ddm = 0.0
        first_pickup = route[0]
        if (first_pickup[0] != home_lat or first_pickup[1] != home_lng):
            ddm += get_distance((home_lat, home_lng), (first_pickup[0], first_pickup[1]))
        
        for i in range(1, len(route) - 1, 2):
            if i + 1 < len(route):
                dropoff = route[i]
                next_pickup = route[i + 1]
                if (dropoff[0] != next_pickup[0] or dropoff[1] != next_pickup[1]):
                    ddm += get_distance((dropoff[0], dropoff[1]), (next_pickup[0], next_pickup[1]))
        
        last_dropoff = route[-1]
        if (last_dropoff[0] != home_lat or last_dropoff[1] != home_lng):
            ddm += get_distance((last_dropoff[0], last_dropoff[1]), (home_lat, home_lng))
        
        return ddm
    
def _calculate_active_km(route: List[Tuple[float, float]], bookings: List[Dict]) -> float:
        if len(route) < 2:
            return 0.0

        active_km = 0.0
        for i in range(0, len(route) - 1, 2):
            if i + 1 < len(route):
                pickup = route[i]
                dropoff = route[i + 1]
                # Find the booking with matching pickup and dropoff
                found = False
                for booking in bookings:
                    if (abs(booking["pickup_lat"] - pickup[0]) < 1e-6 and
                        abs(booking["pickup_lon"] - pickup[1]) < 1e-6 and
                        abs(booking["drop_lat"] - dropoff[0]) < 1e-6 and
                        abs(booking["drop_lon"] - dropoff[1]) < 1e-6):
                        active_km += booking.get("distance_km", 0.0)
                        found = True
                        break
                if not found:
                    # fallback to calculated distance if booking not found
                    active_km += get_distance((pickup[0], pickup[1]), (dropoff[0], dropoff[1]))
        return active_km
    



    