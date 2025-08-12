import h3
from Config import Config
import logging
from typing import Optional, Dict

logger = logging.getLogger(__name__)

def latlng_to_h3(lat, lng, resolution):
    """
    Convert latitude/longitude to H3 hex string at specified resolution.
    
    Args:
        lat: Latitude
        lng: Longitude  
        resolution: H3 resolution (default 9)
        
    Returns:
        H3 hex string
    """
    try:
        return h3.latlng_to_cell(lat, lng, resolution)
    except Exception as e:
        print(f"Error converting lat/lng to H3: {e}")
        return ""

def get_h3_distance(hex1, hex2):
    """
    Get distance between two H3 hexes in number of hex cells.
    
    Args:
        hex1: First H3 hex string
        hex2: Second H3 hex string
        
    Returns:
        Distance in hex cells
    """
    try:
        if not hex1 or not hex2:
            print(f"Warning: Invalid H3 hex input - hex1: {hex1}, hex2: {hex2}")
            return float('inf')
        
        # Use h3.grid_distance for H3 v4
        distance_cells = h3.grid_distance(hex1, hex2)
        
        # Get the resolution from one of the hexes
        resolution = h3.get_resolution(hex1)
        
        # Get average edge length in km for this resolution
        edge_length_km = h3.average_hexagon_edge_length(resolution, 'km')
        
        # Each cell-to-cell step is about one edge length
        distance_km = distance_cells * edge_length_km
        
        return distance_km
    except Exception as e:
        # If H3 distance fails, fall back to simple comparison
        print(f"Error calculating H3 distance: {e}")
        print(f"Failed hexes: {hex1}, {hex2}")
        # Return 0 if same hex, otherwise return a reasonable default
        if hex1 == hex2:
            return 0.0
        else:
            return 5.0  # Default 5km if calculation fails


def h3_to_latlng(hex_id):
    """
    Convert H3 hex string to latitude/longitude coordinates.
    
    Args:
        hex_id: H3 hex string
        
    Returns:
        Tuple of (latitude, longitude)
    """
    try:
        return h3.cell_to_latlng(hex_id)
    except Exception as e:
        print(f"Error converting H3 to lat/lng: {e}")
        return (0.0, 0.0)

def get_nearest_vehicle_h3(booking_pickup_lat, booking_pickup_lng, vehicles, pickup_time):
    """
    Find the nearest available vehicle to a booking pickup location by expanding H3 k-ring neighbors.
    
    Args:
        booking_pickup_lat, booking_pickup_lng: Booking pickup coordinates
        vehicles: List of vehicle dictionaries with current locations
        pickup_time: Booking pickup time as string (format: "YYYY-MM-DD HH:MM:SS")
        
    Returns:
        Index of nearest vehicle, or -1 if none found
    """
    return -1  # Placeholder implementation

# def _find_nearest_vehicle_by_h3(self, booking: Dict) -> Optional[int]:
#         """Find the nearest vehicle based on H3 proximity and vehicle type matching"""
#         if not self.vehicles:
#             return None
            
#         booking_type = booking.get("vehicle_type", "class1")
        
#         try:
#             booking_hex = latlng_to_h3(booking["pickup_lat"], booking["pickup_lon"], Config.H3_RESOLUTION)
#             if not booking_hex:
#                 # Fallback to first vehicle of correct type if H3 conversion fails
#                 for i, vehicle in enumerate(self.vehicles):
#                     if vehicle["vehicle_type"] == booking_type:
#                         return i
#                 return None
                
#             min_h3_distance = float('inf')
#             nearest_vehicle_idx = None
            
#             for i, vehicle in enumerate(self.vehicles):
#                 # Only consider vehicles of the correct type
#                 if vehicle["vehicle_type"] != booking_type:
#                     continue
                    
#                 vehicle_hex = vehicle["h3_hex"]
#                 if vehicle_hex:
#                     h3_distance = get_h3_distance(booking_hex, vehicle_hex)
#                     if h3_distance < min_h3_distance:
#                         min_h3_distance = h3_distance
#                         nearest_vehicle_idx = i
                        
#             return nearest_vehicle_idx
            
#         except Exception as e:
#             logger.warning(f"Error finding nearest vehicle by H3: {e}")
#             # Fallback to first vehicle of correct type
#             for i, vehicle in enumerate(self.vehicles):
#                 if vehicle["vehicle_type"] == booking_type:
#                     return i
#             return None