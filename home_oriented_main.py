import json
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
import math

# Import utility functions
from H3_utils import latlng_to_h3, get_h3_distance, h3_to_latlng
from Helper_func import _safe_datetime_parse, _get_time_minutes, _get_pickup_time_minutes, get_distance
from Config import Config
from service import _calculate_ddm, _calculate_active_km
import h3

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class VehicleState:
    """Current state of a vehicle including route and timing"""
    vehicle_id: int
    home_lat: float
    home_lng: float
    current_lat: float
    current_lng: float
    vehicle_type: str
    route: List[Tuple[float, float]]  # List of (lat, lng) waypoints
    assigned_bookings: List[int]
    active_km: float
    dead_km: float
    available_time: float  # Time in minutes from start of day
    h3_hex: str
    total_driver_pay: float
    is_routed: bool = False  # Track if vehicle already has a complete route

class HomeOrientedBookingAssigner:
    """Home-oriented booking assignment system that ensures vehicles return near home"""
    
    def __init__(self):
        self.config = Config()
        self.vehicles: List[VehicleState] = []
        self.unassigned_bookings: List[Dict] = []
        self.assignments: Dict[int, List[int]] = {}  # vehicle_id -> [booking_ids]
        
        # Driver pay rates per km by class
        self.active_driver_pay = {
            "class1": 16, "class2": 20, "class3": 22, "class4": 26, "class5": 32,
            "class6": 40, "class7": 50, "class8": 60, "class9": 70
        }
        
        self.dead_driver_pay = {
            "class1": 10, "class2": 15, "class3": 18, "class4": 22, "class5": 28,
            "class6": 32, "class7": 40, "class8": 50, "class9": 60
        }
        
        # Customer price per km by class
        self.customer_price_per_km = {
            "class1": 20, "class2": 24, "class3": 28, "class4": 32, "class5": 40,
            "class6": 50, "class7": 60, "class8": 70, "class9": 80
        }
        
        # Dead km percentage for customer pricing
        self.dead_km_percentage = {
            "class1": 0.40, "class2": 0.40, "class3": 0.40, "class4": 0.40, "class5": 0.40,
            "class6": 0.30, "class7": 0.30, "class8": 0.25, "class9": 0.25
        }

    def reset(self):
        """Reset all vehicles, assignments, and unassigned bookings."""
        self.vehicles = []
        self.unassigned_bookings = []
        self.assignments = {}
        
        self.dead_driver_pay = {
            "class1": 10, "class2": 15, "class3": 18, "class4": 22, "class5": 28,
            "class6": 32, "class7": 40, "class8": 50, "class9": 60
        }
        
        # Customer price per km by class
        self.customer_price_per_km = {
            "class1": 20, "class2": 24, "class3": 28, "class4": 32, "class5": 40,
            "class6": 50, "class7": 60, "class8": 70, "class9": 80
        }
        
        # Dead km percentage for customer pricing
        self.dead_km_percentage = {
            "class1": 0.40, "class2": 0.40, "class3": 0.40, "class4": 0.40, "class5": 0.40,
            "class6": 0.30, "class7": 0.30, "class8": 0.25, "class9": 0.25
        }

    def initialize_vehicles(self, vehicles_data: List[Dict]):
        """Initialize vehicles from input data"""
        self.vehicles = []
        for vehicle in vehicles_data:
            # All vehicles start at 6:00 AM (360 minutes) to handle early bookings
            start_time = 6 * 60  # 6:00 AM in minutes
            
            vehicle_state = VehicleState(
                vehicle_id=vehicle["vehicle_id"],
                home_lat=vehicle["home_lat"],
                home_lng=vehicle["home_lng"],
                current_lat=vehicle["home_lat"],
                current_lng=vehicle["home_lng"],
                vehicle_type=vehicle["vehicle_type"],
                route=[],
                assigned_bookings=[],
                active_km=0.0,
                dead_km=0.0,
                available_time=start_time,
                h3_hex=latlng_to_h3(vehicle["home_lat"], vehicle["home_lng"], self.config.H3_RESOLUTION),
                total_driver_pay=0.0,
                is_routed=False
            )
            self.vehicles.append(vehicle_state)
            self.assignments[vehicle["vehicle_id"]] = []
        
        logger.info(f"Initialized {len(self.vehicles)} vehicles")

    def sort_bookings_by_time(self, bookings: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
        """
        Sort bookings in ascending order (morning to evening) and descending order (evening to morning)
        Returns: (ascending_bookings, descending_bookings)
        """
        # Sort bookings by pickup time
        sorted_bookings = sorted(bookings, key=lambda x: _get_pickup_time_minutes(x["pickup_time"]))
        
        # Create ascending (7am to 6pm) and descending (6pm to 7am next day) lists
        ascending_bookings = sorted_bookings.copy()
        descending_bookings = sorted_bookings[::-1]  # Reverse order
        
        logger.info(f"Sorted {len(ascending_bookings)} bookings in ascending time order")
        logger.info(f"Created descending order list for home-oriented routing")
        
        return ascending_bookings, descending_bookings

    # def find_best_ending_booking(self, vehicle: VehicleState, available_bookings: List[Dict]) -> Optional[Dict]:
    #     """
    #     Find the best ending booking for a vehicle that brings it closest to home
    #     """
    #     home_hex = latlng_to_h3(vehicle.home_lat, vehicle.home_lng, self.config.H3_RESOLUTION)
    #     best_booking = None
    #     min_distance_to_home = float('inf')
        
    #     for booking in available_bookings:
    #         # Check if booking is of correct vehicle type or one class below (we can use higher class for lower)
    #         booking_class = int(booking["vehicle_type"].replace("class", ""))
    #         vehicle_class = int(vehicle.vehicle_type.replace("class", ""))
            
    #         if booking_class > vehicle_class:
    #             continue  # Can't use lower class vehicle for higher class booking
                
    #         # Calculate distance from drop location to home
    #         drop_hex = latlng_to_h3(booking["drop_lat"], booking["drop_lon"], self.config.H3_RESOLUTION)
    #         distance_to_home = get_h3_distance(drop_hex, home_hex)
            
    #         if distance_to_home < min_distance_to_home:
    #             min_distance_to_home = distance_to_home
    #             best_booking = booking
        
    #     return best_booking

    def calculate_travel_time(self, distance_km: float) -> float:
        """Calculate travel time in minutes"""
        return (distance_km / 30) * 60

    def is_vehicle_available_for_booking(self, vehicle: VehicleState, booking: Dict) -> bool:
        """Check if vehicle can reach pickup location within time window"""
        try:
            booking_pickup_min = _get_pickup_time_minutes(booking["pickup_time"])
            
            # Calculate travel time to pickup location
            pickup_location = (booking["pickup_lat"], booking["pickup_lon"])
            current_location = (vehicle.current_lat, vehicle.current_lng)
            travel_distance = get_distance(current_location, pickup_location)
            travel_time = self.calculate_travel_time(travel_distance)
            
            # Calculate the earliest time the vehicle can reach the pickup location
            earliest_arrival_time = vehicle.available_time + travel_time
            
            # Vehicle is available if it can reach pickup location within 60 minutes after booking time
            # This allows vehicles to arrive early and wait, which is more realistic
            is_available = (earliest_arrival_time <= booking_pickup_min + 60)
            
            return is_available
            
        except Exception as e:
            logger.warning(f"Error checking vehicle availability: {e}")
            return False

    def get_suitable_vehicles(self, booking: Dict) -> List[VehicleState]:
        """Get vehicles that can handle the booking using expanding H3 search radius"""
        booking_type = booking.get("vehicle_type", "class1")
        
        try:
            booking_hex = latlng_to_h3(booking["pickup_lat"], booking["pickup_lon"], self.config.H3_RESOLUTION)
        except Exception:
            booking_hex = None
        
        # If H3 conversion failed, fallback to simple search
        if not booking_hex:
            suitable_vehicles = []
            for vehicle in self.vehicles:
                if (vehicle.vehicle_type == booking_type and 
                    not vehicle.is_routed and 
                    self.is_vehicle_available_for_booking(vehicle, booking)):
                    suitable_vehicles.append(vehicle)
            return suitable_vehicles
        
        # Expanding search with increasing radius
        max_search_radius = 20 # Maximum search radius in H3 rings
        
        for search_radius in range(max_search_radius + 1):
            suitable_vehicles = []

            # Get all hexes within the current search radius
            if search_radius == 0:
                # Search only in the current hex
                search_hexes = [booking_hex]
            else:
                # Get hexes in the current ring
                try:
                    # Use h3.grid_ring to get hexes at the specific distance
                    search_hexes = list(h3.grid_ring(booking_hex, search_radius))
                except Exception:
                    # If ring generation fails, fall back to distance-based search
                    search_hexes = []

            # Search for vehicles in the current radius
            for vehicle in self.vehicles:
                # Skip already routed vehicles
                if vehicle.is_routed:
                    continue
                    
                # Check vehicle type match
                if vehicle.vehicle_type != booking_type:
                    continue

                # Check time availability
                if not self.is_vehicle_available_for_booking(vehicle, booking):
                    continue

                # Check if vehicle is within current search radius
                if search_radius == 0:
                    # Only check vehicles in the exact same hex
                    if vehicle.h3_hex == booking_hex:
                        suitable_vehicles.append(vehicle)
                else:
                    # Check if vehicle is within the search hexes or distance
                    if search_hexes and vehicle.h3_hex in search_hexes:
                        suitable_vehicles.append(vehicle)
                    elif not search_hexes:  # Fallback to distance-based search
                        h3_distance_rings = get_h3_distance(booking_hex, vehicle.h3_hex) / 0.5  # Approximate rings
                        if h3_distance_rings <= search_radius:
                            suitable_vehicles.append(vehicle)

            # If we found suitable vehicles, return them and break the loop
            if suitable_vehicles:
                logger.info(f"Found {len(suitable_vehicles)} suitable vehicles for booking {booking.get('booking_id', 'N/A')} at search radius {search_radius}")
                return suitable_vehicles
        
        # If no vehicles found even after maximum search radius
        logger.warning(f"No suitable vehicles found for booking {booking.get('booking_id', 'N/A')} even after expanding search to radius {max_search_radius}")
        return []

    def assign_booking_to_vehicle(self, booking: Dict, vehicle: VehicleState):
        """Assign a booking to a vehicle and update vehicle state"""
        try:
            booking_id = booking["booking_id"]
            pickup_location = (booking["pickup_lat"], booking["pickup_lon"])
            drop_location = (booking["drop_lat"], booking["drop_lon"])
            current_location = (vehicle.current_lat, vehicle.current_lng)

            # Calculate distances for timing and pay
            travel_to_pickup = get_distance(current_location, pickup_location)
            active_distance = booking.get("distance_km", 0)

            # Update vehicle route
            vehicle.route.extend([pickup_location, drop_location])
            vehicle.assigned_bookings.append(booking_id)

            # Update vehicle position and timing
            vehicle.current_lat = booking["drop_lat"]
            vehicle.current_lng = booking["drop_lon"]

            # Update timing
            travel_time = self.calculate_travel_time(travel_to_pickup)
            active_time = booking.get("travel_time", 0)  # Use provided travel time from booking data
            service_time = 30  # 30 minutes service time
            
            # Calculate actual pickup time
            booking_pickup_min = _get_pickup_time_minutes(booking["pickup_time"])
            earliest_arrival_time = vehicle.available_time + travel_time
            
            # If vehicle arrives early, it waits until pickup time
            actual_pickup_start_time = max(earliest_arrival_time, booking_pickup_min)
            
            # Vehicle becomes available after: actual pickup time + active time + service time
            vehicle.available_time = actual_pickup_start_time + active_time + service_time

            # Update distances - accumulate active km, recalculate total dead km
            vehicle.active_km += active_distance
            
            # Recalculate total dead km for the full route
            total_dead_km = 0.0
            if vehicle.route:
                # Distance from home to first pickup
                first_pickup = vehicle.route[0]
                total_dead_km += get_distance((vehicle.home_lat, vehicle.home_lng), first_pickup)
                
                # Distance between dropoffs and next pickups
                for i in range(1, len(vehicle.route) - 1, 2):
                    if i + 1 < len(vehicle.route):
                        dropoff = vehicle.route[i]
                        next_pickup = vehicle.route[i + 1]
                        total_dead_km += get_distance(dropoff, next_pickup)
            
            vehicle.dead_km = total_dead_km

            # Update H3 hex
            vehicle.h3_hex = latlng_to_h3(vehicle.current_lat, vehicle.current_lng, self.config.H3_RESOLUTION)

            # Calculate financial impact
            vehicle_type = vehicle.vehicle_type
            active_pay = self.active_driver_pay.get(vehicle_type, 16)
            dead_pay = self.dead_driver_pay.get(vehicle_type, 10)

            # Update driver pay
            vehicle.total_driver_pay += (active_distance * active_pay) + (travel_to_pickup * dead_pay)

            # Update assignments
            if vehicle.vehicle_id not in self.assignments:
                self.assignments[vehicle.vehicle_id] = []
            self.assignments[vehicle.vehicle_id].append(booking_id)

            logger.info(f"Assigned booking {booking_id} to vehicle {vehicle.vehicle_id}")

        except Exception as e:
            logger.error(f"Error assigning booking {booking['booking_id']} to vehicle {vehicle.vehicle_id}: {e}")

    def find_ending_booking(self, vehicle: VehicleState, descending_bookings: List[Dict], assigned_booking_ids: set) -> Optional[Dict]:
        """Find a good ending booking from evening bookings that brings vehicle close to home"""
        home_hex = latlng_to_h3(vehicle.home_lat, vehicle.home_lng, self.config.H3_RESOLUTION)
        best_ending_booking = None
        min_distance_to_home = float('inf')
        max_acceptable_distance_strict = 5.0  # Strict filter: 5km
        max_acceptable_distance_fallback = 15.0  # Fallback filter: 15km
        min_time_gap_required = 180  # Minimum 3 hours gap needed for middle bookings
        
        # First pass: Look for bookings within strict distance (5km)
        for booking in descending_bookings:
            if booking["booking_id"] in assigned_booking_ids:
                continue
                
            # Check if vehicle can handle this booking (time and type constraints)
            if not self.is_vehicle_available_for_booking(vehicle, booking):
                continue
                
            # Check vehicle type compatibility (same class or one higher)
            booking_class = int(booking["vehicle_type"].replace("class", ""))
            vehicle_class = int(vehicle.vehicle_type.replace("class", ""))
            
            if not (vehicle_class == booking_class or vehicle_class == booking_class + 1):
                 continue
            
            # Check if there's enough time gap for middle bookings
            booking_pickup_time = _get_pickup_time_minutes(booking["pickup_time"])
            time_gap = booking_pickup_time - vehicle.available_time
            
            if time_gap < min_time_gap_required:
                continue  # Skip if not enough time for middle bookings
                
            # Calculate distance from drop location to home using direct distance for accuracy
            home_location = (vehicle.home_lat, vehicle.home_lng)
            drop_location = (booking["drop_lat"], booking["drop_lon"])
            distance_to_home = get_distance(drop_location, home_location)
                
            # Only consider bookings that end reasonably close to home (strict)
            if distance_to_home <= max_acceptable_distance_strict:
                # If this booking is very close to home, select it immediately
                if distance_to_home <= 3.0:  # Within 2km of home
                    best_ending_booking = booking
                    min_distance_to_home = distance_to_home
                    logger.info(f"Found excellent ending booking {booking['booking_id']} within 2km of home for vehicle {vehicle.vehicle_id}")
                    break  # Stop searching, found excellent match
                
                # Otherwise, track the closest one within strict limit
                if distance_to_home < min_distance_to_home:
                    min_distance_to_home = distance_to_home
                    best_ending_booking = booking
        
        # If no booking found within strict distance, try fallback with relaxed distance
        if not best_ending_booking:
            logger.info(f"No ending booking found within {max_acceptable_distance_strict}km for vehicle {vehicle.vehicle_id}, trying fallback with {max_acceptable_distance_fallback}km")
            min_distance_to_home = float('inf')
            
            for booking in descending_bookings:
                if booking["booking_id"] in assigned_booking_ids:
                    continue
                    
                # Check if vehicle can handle this booking (time and type constraints)
                if not self.is_vehicle_available_for_booking(vehicle, booking):
                    continue
                    
                # Check vehicle type compatibility (same class or one higher)
                booking_class = int(booking["vehicle_type"].replace("class", ""))
                vehicle_class = int(vehicle.vehicle_type.replace("class", ""))
                
                if not (vehicle_class == booking_class or vehicle_class == booking_class + 1):
                     continue
                
                # Check if there's enough time gap for middle bookings
                booking_pickup_time = _get_pickup_time_minutes(booking["pickup_time"])
                time_gap = booking_pickup_time - vehicle.available_time
                
                if time_gap < min_time_gap_required:
                    continue  # Skip if not enough time for middle bookings
                    
                # Calculate distance from drop location to home
                home_location = (vehicle.home_lat, vehicle.home_lng)
                drop_location = (booking["drop_lat"], booking["drop_lon"])
                distance_to_home = get_distance(drop_location, home_location)
                    
                # Consider bookings within fallback distance
                if distance_to_home <= max_acceptable_distance_fallback:
                    if distance_to_home < min_distance_to_home:
                        min_distance_to_home = distance_to_home
                        best_ending_booking = booking
        
        if best_ending_booking:
            logger.info(f"Found ending booking {best_ending_booking['booking_id']} for vehicle {vehicle.vehicle_id} - distance to home: {min_distance_to_home:.1f}km")
        else:
            logger.warning(f"No suitable ending booking found for vehicle {vehicle.vehicle_id} within {max_acceptable_distance_fallback}km of home with sufficient time gap")
        
        return best_ending_booking

    def find_middle_bookings(self, vehicle: VehicleState, ending_booking: Dict, available_bookings: List[Dict], assigned_booking_ids: set, all_bookings: List[Dict]) -> List[Dict]:
        """Find middle bookings between current vehicle position and ending booking"""
        middle_bookings = []
        max_middle_bookings = 10  # Limit middle bookings
        
        # Get time window for middle bookings
        current_time = vehicle.available_time
        ending_pickup_time = _get_pickup_time_minutes(ending_booking["pickup_time"])
        
        # Find bookings that fit in the time window
        candidate_bookings = []
        for booking in available_bookings:
            if booking["booking_id"] in assigned_booking_ids:
                continue
            if booking["booking_id"] == ending_booking["booking_id"]:
                continue
                
            booking_pickup_time = _get_pickup_time_minutes(booking["pickup_time"])
            
            # Check if this booking fits in the time window
            if current_time <= booking_pickup_time < ending_pickup_time:
                # Check vehicle type compatibility
                booking_class = int(booking["vehicle_type"].replace("class", ""))
                vehicle_class = int(vehicle.vehicle_type.replace("class", ""))
                
                if (vehicle_class == booking_class or vehicle_class == booking_class + 1):  # Same class or one higher
                    candidate_bookings.append(booking)
        
        # Sort candidate bookings by pickup time
        candidate_bookings.sort(key=lambda x: _get_pickup_time_minutes(x["pickup_time"]))
        
        # Optimally select middle bookings based on dead km - active km optimization
        temp_vehicle_state = {
            'current_lat': vehicle.current_lat,
            'current_lng': vehicle.current_lng,
            'available_time': vehicle.available_time,
            'route': vehicle.route.copy()
        }
        
        # Create a temporary list of already assigned booking IDs for this route
        temp_assigned_ids = set(vehicle.assigned_bookings + [ending_booking["booking_id"]])
        
        while len(middle_bookings) < max_middle_bookings and candidate_bookings:
            best_booking = None
            best_difference = float('inf')
            best_booking_index = -1
            
            for i, booking in enumerate(candidate_bookings):
                # Check if we can reach this booking in time
                pickup_location = (booking["pickup_lat"], booking["pickup_lon"])
                current_location = (temp_vehicle_state['current_lat'], temp_vehicle_state['current_lng'])
                travel_distance = get_distance(current_location, pickup_location)
                travel_time = self.calculate_travel_time(travel_distance)
                
                booking_pickup_time = _get_pickup_time_minutes(booking["pickup_time"])
                earliest_arrival = temp_vehicle_state['available_time'] + travel_time
                
                # Check if we can reach the booking (allow 60 min grace period)
                if earliest_arrival <= booking_pickup_time + 60:
                    # Check if we can still reach the ending booking after this one
                    active_time = booking.get("travel_time", 30)  # Default 30 min if not specified
                    service_time = 30
                    
                    # Update temp state after this booking
                    actual_pickup_start = max(earliest_arrival, booking_pickup_time)
                    booking_end_time = actual_pickup_start + active_time + service_time
                    
                    # Check if we can still reach ending booking
                    ending_pickup_location = (ending_booking["pickup_lat"], ending_booking["pickup_lon"])
                    drop_location = (booking["drop_lat"], booking["drop_lon"])
                    distance_to_ending = get_distance(drop_location, ending_pickup_location)
                    time_to_ending = self.calculate_travel_time(distance_to_ending)
                    
                    ending_pickup_time = _get_pickup_time_minutes(ending_booking["pickup_time"])
                    
                    if booking_end_time + time_to_ending <= ending_pickup_time + 60:
                        # This booking fits time-wise - now check dead km optimization
                        
                        # Create test route with this middle booking added
                        test_route = temp_vehicle_state['route'] + [pickup_location, drop_location]
                        # Add ending booking to test route
                        ending_pickup = (ending_booking["pickup_lat"], ending_booking["pickup_lon"])
                        ending_drop = (ending_booking["drop_lat"], ending_booking["drop_lon"])
                        test_route.extend([ending_pickup, ending_drop])
                        
                        # Calculate total dead km for the test route
                        test_dead_km = _calculate_ddm(test_route, vehicle.home_lat, vehicle.home_lng)
                        
                        # Calculate total active km for the test route
                        # Include current assigned bookings + this test booking + ending booking
                        temp_assigned_for_test = temp_assigned_ids.copy()
                        temp_assigned_for_test.add(booking["booking_id"])
                        
                        test_active_km = sum(b.get("distance_km", 0) for b in all_bookings 
                                           if b["booking_id"] in temp_assigned_for_test)
                        
                        # Calculate difference (dead km - active km) - we want this minimal
                        # Only consider if dead km is not greater than active km
                        if test_dead_km <= test_active_km:
                            # Calculate difference (dead km - active km) - we want this minimal
                            difference = test_dead_km - test_active_km

                            if difference < best_difference:
                                best_difference = difference
                                best_booking = booking
                                best_booking_index = i
            
            # If we found a good middle booking, add it
            if best_booking is not None:
                middle_bookings.append(best_booking)
                temp_assigned_ids.add(best_booking["booking_id"])
                
                # Update temp vehicle state
                pickup_location = (best_booking["pickup_lat"], best_booking["pickup_lon"])
                drop_location = (best_booking["drop_lat"], best_booking["drop_lon"])
                
                # Calculate timing for state update
                travel_distance = get_distance((temp_vehicle_state['current_lat'], temp_vehicle_state['current_lng']), pickup_location)
                travel_time = self.calculate_travel_time(travel_distance)
                booking_pickup_time = _get_pickup_time_minutes(best_booking["pickup_time"])
                earliest_arrival = temp_vehicle_state['available_time'] + travel_time
                active_time = best_booking.get("travel_time", 30)
                service_time = 30
                actual_pickup_start = max(earliest_arrival, booking_pickup_time)
                booking_end_time = actual_pickup_start + active_time + service_time
                
                temp_vehicle_state['current_lat'] = best_booking["drop_lat"]
                temp_vehicle_state['current_lng'] = best_booking["drop_lon"]
                temp_vehicle_state['available_time'] = booking_end_time
                temp_vehicle_state['route'].extend([pickup_location, drop_location])
                
                # Remove the selected booking from candidates
                candidate_bookings.pop(best_booking_index)
            else:
                # No more suitable bookings found
                break
        
        logger.info(f"Found {len(middle_bookings)} middle bookings for vehicle {vehicle.vehicle_id}")
        return middle_bookings

    def complete_vehicle_route(self, vehicle: VehicleState, available_bookings: List[Dict], descending_bookings: List[Dict], all_bookings: List[Dict], global_assigned_ids: set) -> List[int]:
        """Complete a vehicle's route by first finding ending booking, then middle bookings"""
        assigned_booking_ids = []
        assigned_booking_ids_set = set()
        
        # Store initial vehicle state to restore if efficiency check fails
        initial_vehicle_state = {
            'route': vehicle.route.copy(),
            'assigned_bookings': vehicle.assigned_bookings.copy(),
            'active_km': vehicle.active_km,
            'dead_km': vehicle.dead_km,
            'current_lat': vehicle.current_lat,
            'current_lng': vehicle.current_lng,
            'available_time': vehicle.available_time,
            'total_driver_pay': vehicle.total_driver_pay,
            'h3_hex': vehicle.h3_hex
        }
        
        # Step 1: Find a good ending booking from evening bookings that brings vehicle close to home
        # Pass global assigned IDs to prevent double-assignment
        ending_booking = self.find_ending_booking(vehicle, descending_bookings, global_assigned_ids)
        
        if ending_booking:
            # Step 2: Find middle bookings between current position and ending booking
            # Use combined assigned IDs to prevent conflicts
            combined_assigned_ids = global_assigned_ids.union(assigned_booking_ids_set)
            middle_bookings = self.find_middle_bookings(vehicle, ending_booking, available_bookings, combined_assigned_ids, all_bookings)
            
            # Step 3: Assign middle bookings first (in time order)
            for booking in middle_bookings:
                self.assign_booking_to_vehicle(booking, vehicle)
                assigned_booking_ids.append(booking["booking_id"])
                assigned_booking_ids_set.add(booking["booking_id"])
            
            # Step 4: Assign ending booking last
            self.assign_booking_to_vehicle(ending_booking, vehicle)
            assigned_booking_ids.append(ending_booking["booking_id"])
            assigned_booking_ids_set.add(ending_booking["booking_id"])
            
            # Step 5: Add final return home distance for efficiency calculation
            if vehicle.route:
                final_location = (vehicle.current_lat, vehicle.current_lng)
                home_location = (vehicle.home_lat, vehicle.home_lng)
                final_dead_km = get_distance(final_location, home_location)
                total_dead_km_with_home = vehicle.dead_km + final_dead_km
                
                # Calculate efficiency: active_km / (active_km + total_dead_km) * 100
                total_km = vehicle.active_km + total_dead_km_with_home
                efficiency = (vehicle.active_km / total_km * 100) if total_km > 0 else 0
                
                min_efficiency_threshold = 55.0  # Minimum 55% efficiency required
                
                logger.info(f"Vehicle {vehicle.vehicle_id} route efficiency check: {efficiency:.1f}% (Active: {vehicle.active_km:.1f}km, Dead: {total_dead_km_with_home:.1f}km)")
                
                # Step 6: Check if efficiency meets minimum threshold AND final distance from home is reasonable
                max_final_distance_from_home = 20.0  # Maximum 20km final distance from home
                
                if efficiency < min_efficiency_threshold:
                    logger.warning(f"Vehicle {vehicle.vehicle_id} route efficiency {efficiency:.1f}% is below {min_efficiency_threshold}% threshold - rejecting route")
                    logger.info(f"Vehicle {vehicle.vehicle_id} will keep only the fresh booking and remain available for other fresh bookings")
                    
                    # Restore initial vehicle state (keeping only the fresh booking)
                    vehicle.route = initial_vehicle_state['route']
                    vehicle.assigned_bookings = initial_vehicle_state['assigned_bookings']
                    vehicle.active_km = initial_vehicle_state['active_km']
                    vehicle.dead_km = initial_vehicle_state['dead_km']
                    vehicle.current_lat = initial_vehicle_state['current_lat']
                    vehicle.current_lng = initial_vehicle_state['current_lng']
                    vehicle.available_time = initial_vehicle_state['available_time']
                    vehicle.total_driver_pay = initial_vehicle_state['total_driver_pay']
                    vehicle.h3_hex = initial_vehicle_state['h3_hex']
                    
                    # Update available time to after the fresh booking completion
                    if vehicle.assigned_bookings:
                        fresh_booking = next((b for b in all_bookings if b["booking_id"] == vehicle.assigned_bookings[0]), None)
                        if fresh_booking:
                            fresh_booking_time = _get_pickup_time_minutes(fresh_booking["pickup_time"])
                            active_time = fresh_booking.get("travel_time", 30)
                            service_time = 30
                            vehicle.available_time = fresh_booking_time + active_time + service_time
                    
                    # Mark vehicle as NOT routed so it can be considered for other fresh bookings
                    vehicle.is_routed = False
                    
                    # Return empty list since route was rejected
                    return []
                elif final_dead_km > max_final_distance_from_home:
                    logger.warning(f"Vehicle {vehicle.vehicle_id} final distance from home {final_dead_km:.1f}km is above {max_final_distance_from_home}km threshold - rejecting route")
                    logger.info(f"Vehicle {vehicle.vehicle_id} will keep only the fresh booking and remain available for other fresh bookings")
                    
                    # Restore initial vehicle state (keeping only the fresh booking)
                    vehicle.route = initial_vehicle_state['route']
                    vehicle.assigned_bookings = initial_vehicle_state['assigned_bookings']
                    vehicle.active_km = initial_vehicle_state['active_km']
                    vehicle.dead_km = initial_vehicle_state['dead_km']
                    vehicle.current_lat = initial_vehicle_state['current_lat']
                    vehicle.current_lng = initial_vehicle_state['current_lng']
                    vehicle.available_time = initial_vehicle_state['available_time']
                    vehicle.total_driver_pay = initial_vehicle_state['total_driver_pay']
                    vehicle.h3_hex = initial_vehicle_state['h3_hex']
                    
                    # Update available time to after the fresh booking completion
                    if vehicle.assigned_bookings:
                        fresh_booking = next((b for b in all_bookings if b["booking_id"] == vehicle.assigned_bookings[0]), None)
                        if fresh_booking:
                            fresh_booking_time = _get_pickup_time_minutes(fresh_booking["pickup_time"])
                            active_time = fresh_booking.get("travel_time", 30)
                            service_time = 30
                            vehicle.available_time = fresh_booking_time + active_time + service_time
                    
                    # Mark vehicle as NOT routed so it can be considered for other fresh bookings
                    vehicle.is_routed = False
                    
                    
                    # Update available time to after the fresh booking completion
                    if vehicle.assigned_bookings:
                        fresh_booking = next((b for b in all_bookings if b["booking_id"] == vehicle.assigned_bookings[0]), None)
                        if fresh_booking:
                            fresh_booking_time = _get_pickup_time_minutes(fresh_booking["pickup_time"])
                            active_time = fresh_booking.get("travel_time", 30)
                            service_time = 30
                            vehicle.available_time = fresh_booking_time + active_time + service_time
                    
                    # Mark vehicle as NOT routed so it can be considered for other fresh bookings
                    vehicle.is_routed = False
                    
                    # Return empty list since route was rejected
                    return []
                else:
                    logger.info(f"Vehicle {vehicle.vehicle_id} route efficiency {efficiency:.1f}% meets threshold - accepting route")
            
        else:
            # No suitable ending booking found - vehicle should remain available for other fresh bookings
            logger.info(f"No suitable ending booking found for vehicle {vehicle.vehicle_id}, keeping vehicle available for other fresh bookings")
            
            # Update available time to after the fresh booking completion
            if vehicle.assigned_bookings:
                fresh_booking = next((b for b in all_bookings if b["booking_id"] == vehicle.assigned_bookings[0]), None)
                if fresh_booking:
                    fresh_booking_time = _get_pickup_time_minutes(fresh_booking["pickup_time"])
                    active_time = fresh_booking.get("travel_time", 30)
                    service_time = 30
                    vehicle.available_time = fresh_booking_time + active_time + service_time
            
            # Mark vehicle as NOT routed so it can be considered for other fresh bookings
            vehicle.is_routed = False
            
            # Return empty list since no route was completed
            return []
        
        # Only mark vehicle as routed and add final dead km if we actually assigned additional bookings
        # OR if the vehicle already has bookings from the fresh booking assignment
        if assigned_booking_ids or vehicle.assigned_bookings:
            # Mark vehicle as routed only if we assigned additional bookings
            if assigned_booking_ids:
                vehicle.is_routed = True
            
            # Add final return home distance to dead km for any vehicle that has bookings
            if vehicle.route:
                final_location = (vehicle.current_lat, vehicle.current_lng)
                home_location = (vehicle.home_lat, vehicle.home_lng)
                final_dead_km = get_distance(final_location, home_location)
                
                # Safety check: If final dead km is too high, log a warning
                max_acceptable_final_dead_km = 20.0  # Maximum 20km dead km back home
                if final_dead_km > max_acceptable_final_dead_km:
                    logger.warning(f"Vehicle {vehicle.vehicle_id} has high dead km back home: {final_dead_km:.1f}km (>{max_acceptable_final_dead_km}km)")
                    logger.warning(f"Last drop location: ({vehicle.current_lat:.3f}, {vehicle.current_lng:.3f}), Home: ({vehicle.home_lat:.3f}, {vehicle.home_lng:.3f})")
                
                vehicle.dead_km += final_dead_km
                
                # Add final dead km pay
                dead_pay = self.dead_driver_pay.get(vehicle.vehicle_type, 10)
                vehicle.total_driver_pay += final_dead_km * dead_pay
            
            logger.info(f"Completed route for vehicle {vehicle.vehicle_id}: {len(vehicle.assigned_bookings)} total bookings")
        
        return assigned_booking_ids

    # def find_suitable_vehicles_for_booking(self, booking: Dict) -> List[VehicleState]:
    #     """Find vehicles that can handle the booking (not already routed)"""
    #     booking_type = booking.get("vehicle_type", "class1")
    #     suitable_vehicles = []
        
    #     for vehicle in self.vehicles:
    #         # Skip already routed vehicles
    #         if vehicle.is_routed:
    #             continue
                
    #         # Check vehicle type (can use same class or higher)
    #         booking_class = int(booking_type.replace("class", ""))
    #         vehicle_class = int(vehicle.vehicle_type.replace("class", ""))
            
    #         if vehicle_class >= booking_class:
    #             suitable_vehicles.append(vehicle)
        
    #     return suitable_vehicles

    def calculate_route_metrics(self, vehicle: VehicleState, route_bookings: List[Dict]) -> Dict:
        """Calculate total dead km, active km for a complete route"""
        if not route_bookings:
            return {"dead_km": 0, "active_km": 0, "is_valid": False}
        
        total_dead_km = 0
        total_active_km = 0
        current_lat, current_lng = vehicle.home_lat, vehicle.home_lng
        current_time = vehicle.available_time
        
        for i, booking in enumerate(route_bookings):
            pickup_lat, pickup_lng = booking["pickup_lat"], booking["pickup_lon"]
            drop_lat, drop_lng = booking["drop_lat"], booking["drop_lon"]
            
            # Dead km to pickup
            dead_km_to_pickup = get_distance((current_lat, current_lng), (pickup_lat, pickup_lng))
            total_dead_km += dead_km_to_pickup
            
            # Active km for this booking
            active_km = booking.get("distance_km", 0)
            total_active_km += active_km
            
            # Update current location and time
            current_lat, current_lng = drop_lat, drop_lng
            
            # Check time constraint - vehicle shouldn't be more than 30 min late
            pickup_time = _get_pickup_time_minutes(booking["pickup_time"])
            travel_time = (dead_km_to_pickup / 30) * 60  # minutes
            arrival_time = current_time + travel_time
            
            if arrival_time > pickup_time + 30:  # More than 30 min late
                return {"dead_km": float('inf'), "active_km": 0, "is_valid": False}
                
            # Update time after completing this booking
            service_time = booking.get("travel_time", 0) + 30  # travel + service time
            current_time = max(arrival_time, pickup_time) + service_time
        
        # Dead km back to home from last drop
        dead_km_home = get_distance((current_lat, current_lng), (vehicle.home_lat, vehicle.home_lng))
        total_dead_km += dead_km_home
        
        # Check if dead km is not more than active km
        is_valid = total_dead_km <= total_active_km
        
        return {
            "dead_km": total_dead_km,
            "active_km": total_active_km,
            "is_valid": is_valid
        }

    def create_vehicle_route(self, vehicle: VehicleState, first_booking: Dict, 
                           last_booking: Dict, available_bookings: List[Dict]) -> List[Dict]:
        """
        Create a complete route for vehicle starting with first_booking, ending with last_booking,
        and filling middle bookings optimally
        """
        route_bookings = [first_booking]
        used_bookings = {first_booking["booking_id"]}
        
        if first_booking["booking_id"] != last_booking["booking_id"]:
            route_bookings.append(last_booking)
            used_bookings.add(last_booking["booking_id"])
        
        # Find suitable middle bookings
        middle_bookings = []
        for booking in available_bookings:
            if booking["booking_id"] in used_bookings:
                continue
                
            # Check vehicle type compatibility
            booking_class = int(booking["vehicle_type"].replace("class", ""))
            vehicle_class = int(vehicle.vehicle_type.replace("class", ""))
            
            if booking_class <= vehicle_class:
                # Check if booking time is between first and last booking
                first_time = _get_pickup_time_minutes(first_booking["pickup_time"])
                last_time = _get_pickup_time_minutes(last_booking["pickup_time"])
                booking_time = _get_pickup_time_minutes(booking["pickup_time"])
                
                if first_time <= booking_time <= last_time:
                    middle_bookings.append(booking)
        
        # Sort middle bookings by time and try to add them
        middle_bookings.sort(key=lambda x: _get_pickup_time_minutes(x["pickup_time"]))
        
        # Try different combinations and pick the best valid route
        best_route = route_bookings.copy()
        best_metrics = self.calculate_route_metrics(vehicle, best_route)
        
        # Try adding middle bookings one by one
        for booking in middle_bookings:
            test_route = [first_booking] + [booking] + ([last_booking] if first_booking["booking_id"] != last_booking["booking_id"] else [])
            test_metrics = self.calculate_route_metrics(vehicle, test_route)
            
            if test_metrics["is_valid"] and test_metrics["dead_km"] < best_metrics["dead_km"]:
                best_route = test_route
                best_metrics = test_metrics
        
        return best_route

    def assign_route_to_vehicle(self, vehicle: VehicleState, route_bookings: List[Dict]):
        """Assign a complete route to a vehicle"""
        vehicle.is_routed = True
        vehicle.assigned_bookings = [booking["booking_id"] for booking in route_bookings]
        
        # Build route waypoints
        vehicle.route = []
        for booking in route_bookings:
            pickup = (booking["pickup_lat"], booking["pickup_lon"])
            drop = (booking["drop_lat"], booking["drop_lon"])
            vehicle.route.extend([pickup, drop])
        
        # Calculate metrics
        metrics = self.calculate_route_metrics(vehicle, route_bookings)
        vehicle.active_km = metrics["active_km"]
        vehicle.dead_km = metrics["dead_km"]
        
        # Update final position
        if route_bookings:
            last_booking = route_bookings[-1]
            vehicle.current_lat = last_booking["drop_lat"]
            vehicle.current_lng = last_booking["drop_lon"]
        
        # Calculate driver pay
        vehicle.total_driver_pay = (vehicle.active_km * self.active_driver_pay.get(vehicle.vehicle_type, 20) + 
                                  vehicle.dead_km * self.dead_driver_pay.get(vehicle.vehicle_type, 15))
        
        # Update assignments
        self.assignments[vehicle.vehicle_id] = vehicle.assigned_bookings
        
        logger.info(f"Assigned complete route to vehicle {vehicle.vehicle_id}: {len(route_bookings)} bookings, "
                   f"Active KM: {vehicle.active_km:.1f}, Dead KM: {vehicle.dead_km:.1f}")

    def process_bookings_home_oriented(self, bookings: List[Dict]):
        """
        Main logic: Process bookings using existing assignment logic, then complete vehicle routes
        """
        self.unassigned_bookings = []
        ascending_bookings, descending_bookings = self.sort_bookings_by_time(bookings)
        
        assigned_booking_ids = set()
        total_bookings = len(bookings)
        assigned_count = 0
        
        logger.info(f"Starting home-oriented booking assignment with {total_bookings} bookings")
        
        # Process each fresh booking from ascending sorted list
        for booking in ascending_bookings:
            if booking["booking_id"] in assigned_booking_ids:
                continue
                
            assigned = False
            booking_type = booking.get("vehicle_type", "class1")
            
            # First, try to find suitable vehicles of the same class using existing logic
            suitable_vehicles = self.get_suitable_vehicles(booking)
            
            if suitable_vehicles:
                # Select the best vehicle based on dead km - active km comparison (existing logic)
                best_vehicle = None
                best_difference = float('inf')  # We want the smallest difference
                
                for vehicle in suitable_vehicles:
                    # Test assignment: create a test route with this booking added
                    pickup_location = (booking["pickup_lat"], booking["pickup_lon"])
                    drop_location = (booking["drop_lat"], booking["drop_lon"])
                    
                    # Create test route with the new booking added
                    test_route = vehicle.route + [pickup_location, drop_location]
                    
                    # Calculate total dead km for the entire route including this booking
                    test_dead_km = _calculate_ddm(test_route, vehicle.home_lat, vehicle.home_lng)
                    
                    # Calculate total active km for the entire route including this booking
                    test_active_km = _calculate_active_km(test_route, bookings)  # Pass bookings for lookup
                    
                    # Calculate difference (dead km - active km) - we want this to be minimal
                    difference = test_dead_km - test_active_km

                    if difference < best_difference:
                        best_difference = difference
                        best_vehicle = vehicle
                
                            # Calculate difference (dead km - active km) - we want this minimal
                            
                if best_vehicle:
                    # Assign the fresh booking to the best vehicle but don't mark as routed yet
                    self.assign_booking_to_vehicle(booking, best_vehicle)
                    assigned = True
                    assigned_count += 1
                    assigned_booking_ids.add(booking["booking_id"])
                    
                    # Now complete this vehicle's entire route
                    available_bookings = [b for b in bookings if b["booking_id"] not in assigned_booking_ids]
                    additional_assigned = self.complete_vehicle_route(best_vehicle, available_bookings, descending_bookings, bookings, assigned_booking_ids)
                    
                    if additional_assigned:  # Only update if bookings were actually assigned
                        assigned_booking_ids.update(additional_assigned)
                        assigned_count += len(additional_assigned)
                        logger.info(f"Completed route for vehicle {best_vehicle.vehicle_id}: {len(best_vehicle.assigned_bookings)} total bookings")
                    else:
                        # Check if vehicle was rejected due to efficiency or no ending booking found
                        # In this case, vehicle should remain available for other fresh bookings
                        if not best_vehicle.is_routed and best_vehicle.assigned_bookings:
                            # Vehicle has only fresh booking and is still available for other fresh bookings
                            logger.info(f"Vehicle {best_vehicle.vehicle_id} route was rejected or no ending booking found - keeping available for other fresh bookings")
                            # Update the vehicle's available time to after this fresh booking
                            if best_vehicle.route:
                                # Calculate time after completing this fresh booking
                                fresh_booking_time = _get_pickup_time_minutes(booking["pickup_time"])
                                active_time = booking.get("travel_time", 30)
                                service_time = 30
                                best_vehicle.available_time = fresh_booking_time + active_time + service_time
                        else:
                            logger.info(f"No additional bookings assigned to vehicle {best_vehicle.vehicle_id}, only has the fresh booking")
            
            # If no suitable vehicle found, try only one class higher (existing logic)
            if not assigned:
                booking_type = booking.get("vehicle_type", "class1")
                class_num = int(booking_type.replace("class", ""))
                if class_num < 9:
                    higher_class = f"class{class_num + 1}"
                    logger.info(f"Trying {higher_class} vehicles for booking {booking['booking_id']} (originally {booking_type})")
                    higher_class_booking = booking.copy()
                    higher_class_booking["vehicle_type"] = higher_class
                    higher_class_vehicles = self.get_suitable_vehicles(higher_class_booking)
                    
                    if higher_class_vehicles:
                        best_vehicle = None
                        best_difference = float('inf')
                        
                        for vehicle in higher_class_vehicles:
                            pickup_location = (booking["pickup_lat"], booking["pickup_lon"])
                            drop_location = (booking["drop_lat"], booking["drop_lon"])
                            test_route = vehicle.route + [pickup_location, drop_location]
                            test_dead_km = _calculate_ddm(test_route, vehicle.home_lat, vehicle.home_lng)
                            test_active_km = _calculate_active_km(test_route, bookings)
                            
                                
                            difference = test_dead_km - test_active_km  
                            
                          
                            if difference < best_difference:
                                best_difference = difference
                                best_vehicle = vehicle
                        
                        if best_vehicle:
                            # Assign the fresh booking to the best higher class vehicle but don't mark as routed yet
                            self.assign_booking_to_vehicle(booking, best_vehicle)
                            assigned = True
                            assigned_count += 1
                            assigned_booking_ids.add(booking["booking_id"])
                            logger.info(f"Assigned booking {booking['booking_id']} to {higher_class} vehicle {best_vehicle.vehicle_id}")
                            
                            # Now complete this vehicle's entire route
                            available_bookings = [b for b in bookings if b["booking_id"] not in assigned_booking_ids]
                            additional_assigned = self.complete_vehicle_route(best_vehicle, available_bookings, descending_bookings, bookings, assigned_booking_ids)
                            
                            if additional_assigned:  # Only update if bookings were actually assigned
                                assigned_booking_ids.update(additional_assigned)
                                assigned_count += len(additional_assigned)
                                logger.info(f"Completed route for vehicle {best_vehicle.vehicle_id}: {len(best_vehicle.assigned_bookings)} total bookings")
                            else:
                                # Check if vehicle was rejected due to efficiency or no ending booking found
                                # In this case, vehicle should remain available for other fresh bookings
                                if not best_vehicle.is_routed and best_vehicle.assigned_bookings:
                                    # Vehicle has only fresh booking and is still available for other fresh bookings
                                    logger.info(f"Vehicle {best_vehicle.vehicle_id} route was rejected or no ending booking found - keeping available for other fresh bookings")
                                    # Update the vehicle's available time to after this fresh booking
                                    if best_vehicle.route:
                                        # Calculate time after completing this fresh booking
                                        fresh_booking_time = _get_pickup_time_minutes(booking["pickup_time"])
                                        active_time = booking.get("travel_time", 30)
                                        service_time = 30
                                        best_vehicle.available_time = fresh_booking_time + active_time + service_time
                                else:
                                    logger.info(f"No additional bookings assigned to vehicle {best_vehicle.vehicle_id}, only has the fresh booking")
            
            if not assigned:
                logger.warning(f"Could not assign booking {booking['booking_id']} (type: {booking_type})")
        
        # Finalize vehicles that only have fresh bookings (no route extension was successful)
        for vehicle in self.vehicles:
            if vehicle.assigned_bookings and not vehicle.is_routed:
                # Add final dead km for vehicles with only fresh booking
                if vehicle.route:
                    final_location = (vehicle.current_lat, vehicle.current_lng)
                    home_location = (vehicle.home_lat, vehicle.home_lng)
                    final_dead_km = get_distance(final_location, home_location)
                    vehicle.dead_km += final_dead_km
                    
                    # Add final dead km pay
                    dead_pay = self.dead_driver_pay.get(vehicle.vehicle_type, 10)
                    vehicle.total_driver_pay += final_dead_km * dead_pay
                
                # Mark as routed since it has at least a fresh booking
                vehicle.is_routed = True
                logger.info(f"Finalized vehicle {vehicle.vehicle_id} with fresh booking only")
        
        # Add unassigned bookings
        for booking in bookings:
            if booking["booking_id"] not in assigned_booking_ids:
                self.unassigned_bookings.append(booking)
        
        unassigned_count = len(self.unassigned_bookings)
        
        logger.info(f"Home-oriented assignment complete: {assigned_count}/{total_bookings} bookings assigned")
        return assigned_count, unassigned_count

    def calculate_final_metrics(self, all_bookings: List[Dict]):
        """Calculate final metrics for all vehicles"""
        total_profit = 0
        total_active_km = 0
        total_dead_km = 0
        total_customer_fare = 0
        total_driver_pay = 0
        
        for vehicle in self.vehicles:
            if vehicle.assigned_bookings:
                # Calculate customer fares for this vehicle's bookings
                vehicle_customer_fare = 0
                for booking_id in vehicle.assigned_bookings:
                    # Find the booking details
                    booking = next((b for b in all_bookings if b["booking_id"] == booking_id), None)
                    if booking:
                        active_distance = booking.get("distance_km", 0)
                        vehicle_type = vehicle.vehicle_type
                        dead_km_factor = self.dead_km_percentage.get(vehicle_type, 0.40)
                        customer_price = self.customer_price_per_km.get(vehicle_type, 20)
                        booking_fare = (active_distance + active_distance * dead_km_factor) * customer_price
                        vehicle_customer_fare += booking_fare
                
                # Calculate vehicle profit (customer fare - driver pay)
                vehicle_profit = vehicle_customer_fare - vehicle.total_driver_pay
                
                total_profit += vehicle_profit
                total_active_km += vehicle.active_km
                total_dead_km += vehicle.dead_km
                total_customer_fare += vehicle_customer_fare
                total_driver_pay += vehicle.total_driver_pay
                
                logger.info(f"Vehicle {vehicle.vehicle_id} ({vehicle.vehicle_type}): "
                          f"Customer Fare: ₹{vehicle_customer_fare:.2f}, "
                          f"Driver Pay: ₹{vehicle.total_driver_pay:.2f}, "
                          f"Profit: ₹{vehicle_profit:.2f}, "
                          f"Total Active KM: {vehicle.active_km:.2f}, "
                          f"Total Dead KM: {vehicle.dead_km:.2f}, "
                          f"Efficiency: {(vehicle.active_km/(vehicle.active_km + vehicle.dead_km)*100) if (vehicle.active_km + vehicle.dead_km) > 0 else 0:.1f}%")
        
        overall_efficiency = (total_active_km / (total_active_km + total_dead_km) * 100) if (total_active_km + total_dead_km) > 0 else 0
        
        return {
            "total_profit": total_profit,
            "total_customer_fare": total_customer_fare,
            "total_driver_pay": total_driver_pay,
            "total_active_km": total_active_km,
            "total_dead_km": total_dead_km,
            "overall_efficiency": overall_efficiency,
            "assigned_bookings": sum(len(vehicle.assigned_bookings) for vehicle in self.vehicles),
            "unassigned_bookings": len(self.unassigned_bookings)
        }

    def print_detailed_tables(self, all_bookings: List[Dict]):
        """Print detailed tables showing vehicle and booking information"""
        
        # Vehicle Summary Table
        print("\n" + "="*130)
        print("HOME-ORIENTED VEHICLE SUMMARY TABLE")
        print("="*130)
        print(f"{'Vehicle ID':<10} {'Type':<7} {'Bookings':<9} {'Active KM':<10} {'Dead KM':<9} {'Customer Fare':<13} {'Driver Pay':<11} {'Profit':<10} {'Efficiency':<10}")
        print("-"*130)
        
        for vehicle in self.vehicles:
            if vehicle.assigned_bookings:
                # Calculate customer fare for this vehicle
                vehicle_customer_fare = 0
                for booking_id in vehicle.assigned_bookings:
                    booking = next((b for b in all_bookings if b["booking_id"] == booking_id), None)
                    if booking:
                        active_distance = booking.get("distance_km", 0)
                        vehicle_type = vehicle.vehicle_type
                        dead_km_factor = self.dead_km_percentage.get(vehicle_type, 0.40)
                        customer_price = self.customer_price_per_km.get(vehicle_type, 20)
                        booking_fare = (active_distance + active_distance * dead_km_factor) * customer_price
                        vehicle_customer_fare += booking_fare
                
                vehicle_profit = vehicle_customer_fare - vehicle.total_driver_pay
                efficiency = (vehicle.active_km/(vehicle.active_km + vehicle.dead_km)*100) if (vehicle.active_km + vehicle.dead_km) > 0 else 0
                
                print(f"{vehicle.vehicle_id:<10} {vehicle.vehicle_type:<7} {len(vehicle.assigned_bookings):<9} {vehicle.active_km:<10.2f} {vehicle.dead_km:<9.2f} ₹{vehicle_customer_fare:<12.2f} ₹{vehicle.total_driver_pay:<10.2f} ₹{vehicle_profit:<9.2f} {efficiency:<9.1f}%")
        
        # Unassigned Bookings Table
        if self.unassigned_bookings:
            print("\n" + "="*100)
            print("UNASSIGNED BOOKINGS")
            print("="*100)
            print(f"{'Booking ID':<11} {'Type':<7} {'Distance':<9} {'Pickup Time':<12} {'Pickup Location':<20} {'Drop Location':<20}")
            print("-"*100)
            
            for booking in self.unassigned_bookings:
                pickup_loc = f"({booking['pickup_lat']:.3f}, {booking['pickup_lon']:.3f})"
                drop_loc = f"({booking['drop_lat']:.3f}, {booking['drop_lon']:.3f})"
                print(f"{booking['booking_id']:<11} {booking.get('vehicle_type', 'N/A'):<7} {booking.get('distance_km', 0):<9.2f} {booking.get('pickup_time', 'N/A'):<12} {pickup_loc:<20} {drop_loc:<20}")

    def print_booking_assignment_details(self, all_bookings: List[Dict]):
        """Print detailed booking assignment information"""
        print("\n" + "="*150)
        print("DETAILED BOOKING ASSIGNMENT TABLE")
        print("="*150)
        print(f"{'Booking ID':<10} {'Type':<7} {'Distance':<8} {'Pickup Time':<12} {'Vehicle ID':<10} {'Vehicle Type':<12} {'Assignment Type':<15} {'Route Position':<13}")
        print("-"*150)
        
        # Create a mapping of booking_id to vehicle assignment
        booking_to_vehicle = {}
        for vehicle in self.vehicles:
            for i, booking_id in enumerate(vehicle.assigned_bookings):
                booking_to_vehicle[booking_id] = {
                    'vehicle_id': vehicle.vehicle_id,
                    'vehicle_type': vehicle.vehicle_type,
                    'position': i + 1
                }
        
        # Sort bookings by pickup time for better readability
        sorted_bookings = sorted(all_bookings, key=lambda x: _get_pickup_time_minutes(x["pickup_time"]))
        
        for booking in sorted_bookings:
            booking_id = booking["booking_id"]
            if booking_id in booking_to_vehicle:
                assignment = booking_to_vehicle[booking_id]
                pickup_time = booking.get("pickup_time", "N/A")
                
                # Determine assignment type
                vehicle = next(v for v in self.vehicles if v.vehicle_id == assignment['vehicle_id'])
                if len(vehicle.assigned_bookings) == 1:
                    assignment_type = "Fresh Only"
                elif assignment['position'] == 1:
                    assignment_type = "Fresh Booking"
                elif assignment['position'] == len(vehicle.assigned_bookings):
                    assignment_type = "Ending Booking"
                else:
                    assignment_type = "Middle Booking"
                
                print(f"{booking_id:<10} {booking.get('vehicle_type', 'N/A'):<7} {booking.get('distance_km', 0):<8.1f} {pickup_time:<12} {assignment['vehicle_id']:<10} {assignment['vehicle_type']:<12} {assignment_type:<15} {assignment['position']:<13}")
            else:
                print(f"{booking_id:<10} {booking.get('vehicle_type', 'N/A'):<7} {booking.get('distance_km', 0):<8.1f} {booking.get('pickup_time', 'N/A'):<12} {'UNASSIGNED':<10} {'N/A':<12} {'Unassigned':<15} {'N/A':<13}")

    def print_detailed_vehicle_routes(self, all_bookings: List[Dict]):
        """Print detailed route information for each vehicle"""
        print("\n" + "="*150)
        print("DETAILED VEHICLE ROUTE ANALYSIS")
        print("="*150)
        
        for vehicle in self.vehicles:
            if vehicle.assigned_bookings:
                print(f"\n🚗 VEHICLE {vehicle.vehicle_id} ({vehicle.vehicle_type})")
                print(f"📍 Home Location: ({vehicle.home_lat:.3f}, {vehicle.home_lng:.3f})")
                print(f"📊 Route Summary: {len(vehicle.assigned_bookings)} bookings, Active: {vehicle.active_km:.1f}km, Dead: {vehicle.dead_km:.1f}km, Efficiency: {(vehicle.active_km/(vehicle.active_km + vehicle.dead_km)*100) if (vehicle.active_km + vehicle.dead_km) > 0 else 0:.1f}%")
                
                # Calculate customer fare and profit for this vehicle
                vehicle_customer_fare = 0
                for booking_id in vehicle.assigned_bookings:
                    booking = next((b for b in all_bookings if b["booking_id"] == booking_id), None)
                    if booking:
                        active_distance = booking.get("distance_km", 0)
                        vehicle_type = vehicle.vehicle_type
                        dead_km_factor = self.dead_km_percentage.get(vehicle_type, 0.40)
                        customer_price = self.customer_price_per_km.get(vehicle_type, 20)
                        booking_fare = (active_distance + active_distance * dead_km_factor) * customer_price
                        vehicle_customer_fare += booking_fare
                
                vehicle_profit = vehicle_customer_fare - vehicle.total_driver_pay
                print(f"💰 Financial: Customer Fare: ₹{vehicle_customer_fare:.2f}, Driver Pay: ₹{vehicle.total_driver_pay:.2f}, Profit: ₹{vehicle_profit:.2f}")
                
                print(f"🛣️  Detailed Route:")
                print(f"    Start: Home → First Pickup")
                
                for i, booking_id in enumerate(vehicle.assigned_bookings):
                    booking = next((b for b in all_bookings if b["booking_id"] == booking_id), None)
                    if booking:
                        pickup_loc = f"({booking['pickup_lat']:.3f}, {booking['pickup_lon']:.3f})"
                        drop_loc = f"({booking['drop_lat']:.3f}, {booking['drop_lon']:.3f})"
                        distance = booking.get('distance_km', 0)
                        pickup_time = booking.get('pickup_time', 'N/A')
                        
                        # Calculate dead km to this pickup
                        if i == 0:
                            # First booking - dead km from home
                            dead_km_to_pickup = get_distance((vehicle.home_lat, vehicle.home_lng), (booking['pickup_lat'], booking['pickup_lon']))
                        else:
                            # Subsequent bookings - dead km from previous drop
                            prev_booking_id = vehicle.assigned_bookings[i-1]
                            prev_booking = next((b for b in all_bookings if b["booking_id"] == prev_booking_id), None)
                            if prev_booking:
                                dead_km_to_pickup = get_distance((prev_booking['drop_lat'], prev_booking['drop_lon']), (booking['pickup_lat'], booking['pickup_lon']))
                            else:
                                dead_km_to_pickup = 0
                        
                        print(f"    {i+1}. Booking {booking_id} ({booking.get('vehicle_type', 'N/A')}) - {pickup_time}")
                        print(f"       Dead KM to pickup: {dead_km_to_pickup:.1f}km")
                        print(f"       Pickup: {pickup_loc} → Drop: {drop_loc}")
                        print(f"       Active KM: {distance:.1f}km")
                
                # Calculate final dead km back to home
                if vehicle.assigned_bookings:
                    last_booking_id = vehicle.assigned_bookings[-1]
                    last_booking = next((b for b in all_bookings if b["booking_id"] == last_booking_id), None)
                    if last_booking:
                        final_dead_km = get_distance((last_booking['drop_lat'], last_booking['drop_lon']), (vehicle.home_lat, vehicle.home_lng))
                        print(f"    Final: Last Drop → Home")
                        print(f"       Dead KM back home: {final_dead_km:.1f}km")
                        print(f"       🏠 Distance to home from final drop: {final_dead_km:.1f}km")
                
                print("-" * 100)

def main():
    """Main function to run the home-oriented booking assignment"""
    try:
        # Initialize the assigner
        assigner = HomeOrientedBookingAssigner()
        
        # Load vehicles from JSON file
        try:
            with open('data/vehicles.json', 'r') as f:
                vehicles_data = json.load(f)
            logger.info(f"Loaded {len(vehicles_data)} vehicles from data/vehicles.json")
        except FileNotFoundError:
            logger.error("vehicles.json file not found in data folder")
            return None
        except json.JSONDecodeError:
            logger.error("Error parsing vehicles.json file")
            return None
        
        # Load bookings from JSON file
        try:
            with open('data/bookings.json', 'r') as f:
                bookings_data = json.load(f)
            logger.info(f"Loaded {len(bookings_data)} bookings from data/bookings.json")
        except FileNotFoundError:
            logger.error("bookings.json file not found in data folder")
            return None
        except json.JSONDecodeError:
            logger.error("Error parsing bookings.json file")
            return None
        
        # Initialize vehicles
        assigner.initialize_vehicles(vehicles_data)
        
        # Process bookings with home-oriented logic
        assigned, unassigned = assigner.process_bookings_home_oriented(bookings_data)
        
        # Calculate final metrics
        metrics = assigner.calculate_final_metrics(bookings_data)
        
        print("\n=== HOME-ORIENTED ASSIGNMENT RESULTS ===")
        print(f"Total Customer Fare: ₹{metrics['total_customer_fare']:.2f}")
        print(f"Total Driver Pay: ₹{metrics['total_driver_pay']:.2f}")
        print(f"Total Profit: ₹{metrics['total_profit']:.2f}")
        print(f"Overall Efficiency: {metrics['overall_efficiency']:.1f}%")
        print(f"Total Active KM: {metrics['total_active_km']:.2f}")
        print(f"Total Dead KM: {metrics['total_dead_km']:.2f}")
        print(f"Assigned Bookings: {metrics['assigned_bookings']}")
        print(f"Unassigned Bookings: {metrics['unassigned_bookings']}")
        
        # Print detailed tables
        assigner.print_detailed_tables(bookings_data)
        
        # Print detailed booking assignment information
        assigner.print_booking_assignment_details(bookings_data)
        
        # Print detailed vehicle route analysis
        assigner.print_detailed_vehicle_routes(bookings_data)
        
        return assigner
        
    except Exception as e:
        logger.error(f"Error in main execution: {e}")
        return None

if __name__ == "__main__":
    main()
