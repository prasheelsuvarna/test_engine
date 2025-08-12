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

class HeuristicBookingAssigner:
    def reset(self):
        """Reset all vehicles, assignments, and unassigned bookings."""
        self.vehicles = []
        self.unassigned_bookings = []
        self.assignments = {}
    """Heuristic-based booking assignment system optimized for profit and efficiency"""
    
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
                total_driver_pay=0.0
            )
            self.vehicles.append(vehicle_state)
            self.assignments[vehicle["vehicle_id"]] = []
        
        logger.info(f"Initialized {len(self.vehicles)} vehicles")

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
                if vehicle.vehicle_type == booking_type and self.is_vehicle_available_for_booking(vehicle, booking):
                    suitable_vehicles.append(vehicle)
            return suitable_vehicles
        
        # Expanding search with increasing radius
        max_search_radius = 25  # Maximum search radius in H3 rings
        
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

    def find_vehicle_one_class_above(self, booking: Dict) -> Optional[VehicleState]:
        """Find a vehicle one class above that's returning towards home"""
        booking_type = booking.get("vehicle_type", "class1")
        
        # Get the next higher class
        class_num = int(booking_type.replace("class", ""))
        if class_num >= 9:
            return None
        
        higher_class = f"class{class_num + 1}"
        
        try:
            booking_hex = latlng_to_h3(booking["pickup_lat"], booking["pickup_lon"], self.config.H3_RESOLUTION)
        except Exception:
            booking_hex = None
        
        best_vehicle = None
        min_dead_km_increase = float('inf')
        
        for vehicle in self.vehicles:
            if vehicle.vehicle_type != higher_class:
                continue
            
            if not self.is_vehicle_available_for_booking(vehicle, booking):
                continue
            
            # Check if this assignment would reduce overall dead km
            # by bringing vehicle closer to home
            home_hex = latlng_to_h3(vehicle.home_lat, vehicle.home_lng, self.config.H3_RESOLUTION)
            drop_hex = latlng_to_h3(booking["drop_lat"], booking["drop_lon"], self.config.H3_RESOLUTION)
            
            if home_hex and drop_hex:
                # Calculate if this brings vehicle closer to home
                current_distance_to_home = get_h3_distance(vehicle.h3_hex, home_hex)
                future_distance_to_home = get_h3_distance(drop_hex, home_hex)
                
                if future_distance_to_home < current_distance_to_home:
                    # This would bring vehicle closer to home
                    pickup_location = (booking["pickup_lat"], booking["pickup_lon"])
                    current_location = (vehicle.current_lat, vehicle.current_lng)
                    test_dead_km = get_distance(current_location, pickup_location)
                    
                    if test_dead_km < min_dead_km_increase:
                        min_dead_km_increase = test_dead_km
                        best_vehicle = vehicle
        
        return best_vehicle

    
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
                
                # Note: We don't add final return home distance here as it's added in calculate_final_metrics
            
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
            self.assignments[vehicle.vehicle_id].append(booking_id)

            logger.info(f"Assigned booking {booking_id} to vehicle {vehicle.vehicle_id}")

        except Exception as e:
            logger.error(f"Error assigning booking {booking['booking_id']} to vehicle {vehicle.vehicle_id}: {e}")

    def process_bookings(self, bookings: List[Dict]):
        """Process all bookings using heuristic assignment"""
        self.unassigned_bookings = []
        total_bookings = len(bookings)
        assigned_count = 0
        
        logger.info(f"Processing {total_bookings} bookings")
        
        for booking in bookings:
            assigned = False
            booking_type = booking.get("vehicle_type", "class1")
            
            # First, try to find suitable vehicles of the same class
            suitable_vehicles = self.get_suitable_vehicles(booking)
            
            if suitable_vehicles:
                # Select the best vehicle based on dead km - active km comparison
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
                
                if best_vehicle:
                    self.assign_booking_to_vehicle(booking, best_vehicle)
                    assigned = True
                    assigned_count += 1
            
            # If no suitable vehicle found, try only one class higher
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
                            self.assign_booking_to_vehicle(booking, best_vehicle)
                            assigned = True
                            assigned_count += 1
                            logger.info(f"Assigned booking {booking['booking_id']} to {higher_class} vehicle {best_vehicle.vehicle_id}")
            
            if not assigned:
                self.unassigned_bookings.append(booking)
                logger.warning(f"Could not assign booking {booking['booking_id']} (type: {booking_type})")
        
        logger.info(f"Assignment complete: {assigned_count}/{total_bookings} bookings assigned")
        return assigned_count, len(self.unassigned_bookings)

    def calculate_final_metrics(self, all_bookings: List[Dict]):
        """Calculate final metrics for all vehicles"""
        total_profit = 0
        total_active_km = 0
        total_dead_km = 0
        total_customer_fare = 0
        total_driver_pay = 0
        
        for vehicle in self.vehicles:
            if vehicle.assigned_bookings:
                # Add final dead km to home
                final_location = (vehicle.current_lat, vehicle.current_lng)
                home_location = (vehicle.home_lat, vehicle.home_lng)
                final_dead_km = get_distance(final_location, home_location)
                
                vehicle.dead_km += final_dead_km
                dead_pay = self.dead_driver_pay.get(vehicle.vehicle_type, 10)
                vehicle.total_driver_pay += final_dead_km * dead_pay
                
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
        print("VEHICLE SUMMARY TABLE")
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
        
        # Booking Assignment Table
        print("\n" + "="*140)
        print("BOOKING ASSIGNMENT TABLE")
        print("="*140)
        print(f"{'Booking ID':<11} {'Type':<7} {'Distance':<9} {'Pickup Time':<12} {'Vehicle ID':<10} {'Vehicle Type':<12} {'Fare':<10} {'Search Radius':<13} {'Status':<8}")
        print("-"*140)
        
        for booking in all_bookings:
            booking_id = booking["booking_id"]
            booking_type = booking.get("vehicle_type", "N/A")
            distance = booking.get("distance_km", 0)
            pickup_time = booking.get("pickup_start_time", "N/A")
            
            # Find which vehicle was assigned
            assigned_vehicle = None
            assigned_vehicle_type = "N/A"
            search_radius = "N/A"
            status = "Unassigned"
            
            for vehicle in self.vehicles:
                if booking_id in vehicle.assigned_bookings:
                    assigned_vehicle = vehicle.vehicle_id
                    assigned_vehicle_type = vehicle.vehicle_type
                    status = "Assigned"
                    break
            
            # Calculate fare
            if assigned_vehicle:
                dead_km_factor = self.dead_km_percentage.get(assigned_vehicle_type, 0.40)
                customer_price = self.customer_price_per_km.get(assigned_vehicle_type, 20)
                fare = (distance + distance * dead_km_factor) * customer_price
            else:
                fare = 0
            
            print(f"{booking_id:<11} {booking_type:<7} {distance:<9.2f} {pickup_time:<12} {assigned_vehicle or 'None':<10} {assigned_vehicle_type:<12} ₹{fare:<9.2f} {search_radius:<13} {status:<8}")
        
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
                print(f"{booking['booking_id']:<11} {booking.get('vehicle_type', 'N/A'):<7} {booking.get('distance_km', 0):<9.2f} {booking.get('pickup_start_time', 'N/A'):<12} {pickup_loc:<20} {drop_loc:<20}")
        
        # Vehicle Route Details
        print("\n" + "="*120)
        print("VEHICLE ROUTE DETAILS")
        print("="*120)
        
        for vehicle in self.vehicles:
            if vehicle.assigned_bookings:
                print(f"\nVehicle {vehicle.vehicle_id} ({vehicle.vehicle_type}) - Home: ({vehicle.home_lat:.3f}, {vehicle.home_lng:.3f})")
                print(f"Route: {len(vehicle.route)//2} bookings")
                print(f"Assigned Booking IDs: {', '.join(map(str, vehicle.assigned_bookings))}")
                
                if vehicle.route:
                    print("Route Points:")
                    for i in range(0, len(vehicle.route), 2):
                        if i+1 < len(vehicle.route):
                            pickup = vehicle.route[i]
                            dropoff = vehicle.route[i+1]
                            booking_num = (i//2) + 1
                            print(f"  Booking {booking_num}: Pickup ({pickup[0]:.3f}, {pickup[1]:.3f}) → Dropoff ({dropoff[0]:.3f}, {dropoff[1]:.3f})")
                print("-"*60)

def main():
    """Main function to run the heuristic booking assignment"""
    try:
        # Initialize the assigner
        assigner = HeuristicBookingAssigner()
        
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
        
        # Process bookings
        assigned, unassigned = assigner.process_bookings(bookings_data)
        
        # Calculate final metrics
        metrics = assigner.calculate_final_metrics(bookings_data)
        
        print("\n=== ASSIGNMENT RESULTS ===")
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
        
        return assigner
        
    except Exception as e:
        logger.error(f"Error in main execution: {e}")
        return None

if __name__ == "__main__":
    main()
