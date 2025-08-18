import json
import logging
import time
import random
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Set
from dataclasses import dataclass

# Import the home-oriented assignment logic
from home_oriented_main import HomeOrientedBookingAssigner, VehicleState
from Helper_func import _get_pickup_time_minutes, _safe_datetime_parse

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class InstantBooking:
    """Represents an instant booking with its load time"""
    booking_id: int
    pickup_time: int  # minutes from start of day
    load_time: int    # minutes when this booking was loaded into system
    booking_data: Dict
    is_loaded: bool = False
    is_assigned: bool = False

class RealTimeDispatchSimulator:
    """Real-time dispatch simulator for instant bookings"""
    
    def __init__(self):
        self.assigner = HomeOrientedBookingAssigner()
        self.current_sim_time = 0  # minutes from midnight (start from 6 AM = 360 minutes)
        self.start_time = 6 * 60   # 6 AM in minutes
        self.end_time = 19 * 60    # 11 AM in minutes (changed from 8 PM for easier testing)
        self.step_interval = 30    # 30 minutes per step
        self.real_time_step = 6    # 6 seconds real time per step
        
        # Booking management
        self.scheduled_assignments = {}  # From home_oriented_main.py results
        self.scheduled_bookings: List[Dict] = []  # Store scheduled booking data
        self.instant_bookings: List[InstantBooking] = []
        self.loaded_instant_bookings: List[Dict] = []
        self.locked_bookings: Set[int] = set()
        
        # Track simulation state
        self.last_assignment_time = 0
        self.previous_vehicle_assignments = {}  # Track previous assignments for comparison
        
    def load_scheduled_assignments(self, vehicles_data: List[Dict], scheduled_bookings: List[Dict]):
        """Load pre-assigned scheduled bookings from home_oriented_main.py"""
        logger.info("Loading scheduled assignments from home_oriented_main.py...")
        
        # Store scheduled bookings for later reference
        self.scheduled_bookings = scheduled_bookings.copy()
        
        # Initialize the assigner with vehicles
        self.assigner.initialize_vehicles(vehicles_data)
        
        # Process scheduled bookings using home-oriented logic
        assigned_bookings = self.assigner.process_bookings_home_oriented(scheduled_bookings)
        
        # Store scheduled assignments and print initial state
        self.scheduled_assignments = self.assigner.assignments.copy()
        self.print_assignment_table("SCHEDULED BOOKING ASSIGNMENTS", scheduled_bookings)
        
        # Store initial assignments for comparison
        self._store_current_assignments_for_comparison()
        
        # Calculate and print metrics
        self.calculate_and_print_metrics("SCHEDULED", scheduled_bookings)
        
        # Reset vehicle availability times for real-time simulation
        for vehicle in self.assigner.vehicles:
            vehicle.available_time = self.start_time
            vehicle.is_routed = False
        
        logger.info(f"Loaded scheduled assignments: {len(assigned_bookings)} bookings assigned to vehicles")
        logger.info("Reset vehicle availability times for real-time instant booking assignment")
    
    def load_instant_booking_dataset(self, instant_bookings_file: str):
        """Load instant bookings dataset and calculate their load times"""
        try:
            with open(instant_bookings_file, 'r') as f:
                instant_bookings_data = json.load(f)
            
            self.instant_bookings = []
            for booking in instant_bookings_data:
                pickup_time = _get_pickup_time_minutes(booking["pickup_time"])
                
                # Calculate random load time: between 2 hours and 1 hour before pickup
                earliest_load = max(self.start_time, pickup_time - 120)  # 2 hours before
                latest_load = pickup_time - 60  # 1 hour before
                
                if latest_load > earliest_load:
                    load_time = random.randint(earliest_load, latest_load)
                else:
                    load_time = earliest_load
                
                instant_booking = InstantBooking(
                    booking_id=booking["booking_id"],
                    pickup_time=pickup_time,
                    load_time=load_time,
                    booking_data=booking,
                    is_loaded=False,
                    is_assigned=False
                )
                
                self.instant_bookings.append(instant_booking)
            
            logger.info(f"Loaded {len(self.instant_bookings)} instant bookings for simulation")
            
        except FileNotFoundError:
            logger.warning(f"Instant bookings file {instant_bookings_file} not found")
            self.instant_bookings = []
        except json.JSONDecodeError:
            logger.error(f"Error parsing instant bookings file {instant_bookings_file}")
            self.instant_bookings = []
    
    def check_and_load_instant_bookings(self):
        """Check if any instant bookings should be loaded at current sim time"""
        newly_loaded_bookings = []
        for instant_booking in self.instant_bookings:
            if not instant_booking.is_loaded and instant_booking.load_time <= self.current_sim_time:
                self.loaded_instant_bookings.append(instant_booking.booking_data)
                instant_booking.is_loaded = True
               
                newly_loaded_bookings.append(instant_booking.booking_data)
          
                current_datetime = self._sim_time_to_datetime(self.current_sim_time)
                pickup_datetime = self._sim_time_to_datetime(instant_booking.pickup_time)
                
                logger.info(f"🚨 INSTANT BOOKING LOADED: Booking {instant_booking.booking_id} "
                          f"loaded at {current_datetime.strftime('%H:%M')} for pickup at {pickup_datetime.strftime('%H:%M')}")
        
        return newly_loaded_bookings
    
    def update_locked_assignments(self):
        """Update locked bookings and calculate vehicle availability times based on locked bookings"""
        self.locked_bookings.clear()
        
        lock_window = self.current_sim_time + 120
        
        for vehicle in self.assigner.vehicles:
            vehicle_last_locked_completion = self.current_sim_time
            
            for booking_id in vehicle.assigned_bookings:
                pickup_time = self._get_booking_pickup_time(booking_id)
                if pickup_time and (pickup_time <= self.current_sim_time or pickup_time <= lock_window):
                    self.locked_bookings.add(booking_id)
                    
                    completion_time = self._get_booking_completion_time(booking_id)
                    if completion_time:
                        vehicle_last_locked_completion = max(vehicle_last_locked_completion, completion_time)
            
            # For instant booking assignment, vehicles should be available immediately if current time
            # has passed their locked booking completion times
            vehicle.available_time = max(self.current_sim_time, vehicle_last_locked_completion)
        
        logger.info(f"🔒 Locked {len(self.locked_bookings)} bookings (past + next 2 hours, vehicles availability updated based on locked booking completion times)")
        logger.info(f"📦 Instant bookings are NEVER locked - {len(self.loaded_instant_bookings)} instant bookings remain available for assignment")
        
        for vehicle in self.assigner.vehicles:
            if vehicle.assigned_bookings:
                available_datetime = self._sim_time_to_datetime(vehicle.available_time)
                logger.info(f"Vehicle {vehicle.vehicle_id} available from {available_datetime.strftime('%H:%M')} (last locked booking completion)")
    
    def get_unlocked_bookings(self) -> List[Dict]:
        """Get ALL unlocked bookings (both scheduled and instant) that need reassignment"""
        unlocked_bookings = []
        
        for vehicle in self.assigner.vehicles:
            for booking_id in vehicle.assigned_bookings:
                if booking_id not in self.locked_bookings:
                    booking_data = self._find_booking_data(booking_id)
                    if booking_data:
                        unlocked_bookings.append(booking_data)
        
        for booking in self.loaded_instant_bookings:
            booking_id = booking["booking_id"]
            is_locked = booking_id in self.locked_bookings
            is_assigned = self._is_booking_assigned(booking_id)
            
            if not is_locked and not is_assigned:
                unlocked_bookings.append(booking)
        
        for booking in self.assigner.unassigned_bookings:
            if booking["booking_id"] not in self.locked_bookings:
                unlocked_bookings.append(booking)
        
        logger.info(f"Total unlocked bookings found: {len(unlocked_bookings)}")
        return unlocked_bookings
    
    def get_available_vehicles(self) -> List[VehicleState]:
        """Get all vehicles with their updated availability times"""
        return self.assigner.vehicles
    
    def _find_booking_data(self, booking_id: int) -> Dict:
        """Find booking data by booking_id from either scheduled or instant bookings"""
        for booking in self.scheduled_bookings:
            if booking["booking_id"] == booking_id:
                return booking
        
        for booking in self.loaded_instant_bookings:
            if booking["booking_id"] == booking_id:
                return booking
        
        return None
    
    def reassign_unlocked_bookings(self):
        """Reassign ONLY truly unlocked bookings while preserving locked booking assignments"""
        unlocked_bookings = self.get_unlocked_bookings()
        
        if not unlocked_bookings:
            logger.info("No unlocked bookings to reassign")
            return
        
        logger.info(f"🔄 Reassigning {len(unlocked_bookings)} unlocked bookings while preserving {len(self.locked_bookings)} locked assignments...")
        
        for vehicle in self.assigner.vehicles:
            locked_bookings_for_vehicle = [bid for bid in vehicle.assigned_bookings 
                                         if bid in self.locked_bookings]
            # Store the current availability time before removing unlocked bookings
            original_availability = vehicle.available_time
            
            vehicle.assigned_bookings = locked_bookings_for_vehicle
            self._recalculate_vehicle_metrics_for_locked_bookings(vehicle, locked_bookings_for_vehicle)
            
            # Ensure availability time doesn't go backwards due to removing unlocked bookings
            if vehicle.available_time < original_availability:
                vehicle.available_time = original_availability
        
        temp_assigner = HomeOrientedBookingAssigner()
        temp_assigner.vehicles = [self._copy_vehicle_state(v) for v in self.assigner.vehicles]
        temp_assigner.assignments = {v.vehicle_id: v.assigned_bookings.copy() for v in self.assigner.vehicles}
        
        ascending_bookings, descending_bookings = temp_assigner.sort_bookings_by_time(unlocked_bookings)
        assigned_booking_ids = set(self.locked_bookings)
        
        assigned_count = 0
        unassigned_bookings = []
        
        # First pass: Try normal assignment
        for booking in ascending_bookings:
            if booking["booking_id"] in assigned_booking_ids:
                continue
            
            assigned = False
            booking_type = booking.get("vehicle_type", "class1")
            
            suitable_vehicles = temp_assigner.get_suitable_vehicles(booking)
            
            if suitable_vehicles:
                best_vehicle = None
                best_difference = float('inf')
                
                for vehicle in suitable_vehicles:
                    available = temp_assigner.is_vehicle_available_for_booking(vehicle, booking)
                    if available:
                        pickup_location = (booking["pickup_lat"], booking["pickup_lon"])
                        drop_location = (booking["drop_lat"], booking["drop_lon"])
                        test_route = vehicle.route + [pickup_location, drop_location]
                        
                        from service import _calculate_ddm, _calculate_active_km
                        test_dead_km = _calculate_ddm(test_route, vehicle.home_lat, vehicle.home_lng)
                        test_active_km = _calculate_active_km(test_route, unlocked_bookings)
                        
                        difference = test_dead_km - test_active_km
                        if difference < best_difference:
                            best_difference = difference
                            best_vehicle = vehicle
                
                if best_vehicle:
                    temp_assigner.assign_booking_to_vehicle(booking, best_vehicle)
                    assigned = True
                    assigned_count += 1
                    assigned_booking_ids.add(booking["booking_id"])
                    
                    available_bookings = [b for b in unlocked_bookings if b["booking_id"] not in assigned_booking_ids]
                    additional_assigned = temp_assigner.complete_vehicle_route(
                        best_vehicle, available_bookings, descending_bookings, unlocked_bookings, assigned_booking_ids
                    )
                    
                    if additional_assigned:
                        assigned_booking_ids.update(additional_assigned)
                        assigned_count += len(additional_assigned)
            
            if not assigned:
                unassigned_bookings.append(booking)
        
        # Second pass: Try higher class vehicles for unassigned bookings
        remaining_unassigned = []
        for booking in unassigned_bookings:
            assigned = False
            booking_type = booking.get("vehicle_type", "class1")
            class_num = int(booking_type.replace("class", ""))
            
            if class_num < 9:
                higher_class = f"class{class_num + 1}"
                higher_class_booking = booking.copy()
                higher_class_booking["vehicle_type"] = higher_class
                higher_class_vehicles = temp_assigner.get_suitable_vehicles(higher_class_booking)
                
                if higher_class_vehicles:
                    best_vehicle = None
                    best_difference = float('inf')
                    
                    for vehicle in higher_class_vehicles:
                        if temp_assigner.is_vehicle_available_for_booking(vehicle, booking):
                            pickup_location = (booking["pickup_lat"], booking["pickup_lon"])
                            drop_location = (booking["drop_lat"], booking["drop_lon"])
                            test_route = vehicle.route + [pickup_location, drop_location]
                            
                            from service import _calculate_ddm, _calculate_active_km
                            test_dead_km = _calculate_ddm(test_route, vehicle.home_lat, vehicle.home_lng)
                            test_active_km = _calculate_active_km(test_route, unlocked_bookings)
                            
                            difference = test_dead_km - test_active_km
                            if difference < best_difference:
                                best_difference = difference
                                best_vehicle = vehicle
                    
                    if best_vehicle:
                        temp_assigner.assign_booking_to_vehicle(booking, best_vehicle)
                        assigned = True
                        assigned_count += 1
                        assigned_booking_ids.add(booking["booking_id"])
                        
                        available_bookings = [b for b in unlocked_bookings if b["booking_id"] not in assigned_booking_ids]
                        additional_assigned = temp_assigner.complete_vehicle_route(
                            best_vehicle, available_bookings, descending_bookings, unlocked_bookings, assigned_booking_ids
                        )
                        
                        if additional_assigned:
                            assigned_booking_ids.update(additional_assigned)
                            assigned_count += len(additional_assigned)
            
            if not assigned:
                remaining_unassigned.append(booking)
        
        # Third pass: Relax time constraints for very urgent instant bookings
        for booking in remaining_unassigned:
            assigned = False
            booking_pickup_time = _get_pickup_time_minutes(booking["pickup_time"])
            
            # If this is an urgent instant booking (within 1 hour), try any available vehicle
            if booking_pickup_time <= self.current_sim_time + 60:
                all_vehicles = [v for v in temp_assigner.vehicles if len(v.assigned_bookings) < 8]  # Not overloaded
                
                if all_vehicles:
                    best_vehicle = None
                    best_difference = float('inf')
                    
                    for vehicle in all_vehicles:
                        # Relax availability constraint for urgent bookings
                        pickup_location = (booking["pickup_lat"], booking["pickup_lon"])
                        drop_location = (booking["drop_lat"], booking["drop_lon"])
                        test_route = vehicle.route + [pickup_location, drop_location]
                        
                        from service import _calculate_ddm, _calculate_active_km
                        test_dead_km = _calculate_ddm(test_route, vehicle.home_lat, vehicle.home_lng)
                        test_active_km = _calculate_active_km(test_route, unlocked_bookings)
                        
                        difference = test_dead_km - test_active_km
                        if difference < best_difference:
                            best_difference = difference
                            best_vehicle = vehicle
                    
                    if best_vehicle:
                        temp_assigner.assign_booking_to_vehicle(booking, best_vehicle)
                        assigned = True
                        assigned_count += 1
                        assigned_booking_ids.add(booking["booking_id"])
                        logger.info(f"Urgent assignment: Booking {booking['booking_id']} assigned to vehicle {best_vehicle.vehicle_id} with relaxed constraints")
            
            if not assigned:
                logger.warning(f"Could not reassign unlocked booking {booking['booking_id']} - all strategies failed")
        
        self.assigner.vehicles = temp_assigner.vehicles
        self.assigner.assignments = temp_assigner.assignments
        self._finalize_combined_metrics()
        
        logger.info(f"✅ Reassignment complete: {assigned_count} unlocked bookings assigned, {len(self.locked_bookings)} locked bookings preserved")
    
    def _fix_vehicle_availability_times_after_reassignment(self):
        """Fix vehicle availability times to reflect completion of ALL bookings (locked + newly assigned)"""
        for vehicle in self.assigner.vehicles:
            if not vehicle.assigned_bookings:
                # No bookings - vehicle available from start time
                vehicle.available_time = self.start_time
                continue
                
            # Find the latest completion time among ALL assigned bookings
            latest_completion_time = self.start_time
            
            for booking_id in vehicle.assigned_bookings:
                completion_time = self._get_booking_completion_time(booking_id)
                if completion_time and completion_time > latest_completion_time:
                    latest_completion_time = completion_time
            
            vehicle.available_time = latest_completion_time
            
            # Log the update for debugging
            available_datetime = self._sim_time_to_datetime(vehicle.available_time)
            logger.info(f"Vehicle {vehicle.vehicle_id} availability updated to {available_datetime.strftime('%H:%M')} "
                       f"based on {len(vehicle.assigned_bookings)} total bookings (locked + instant)")
    
    def _store_current_assignments_for_comparison(self):
        """Store current vehicle assignments for comparison in next step"""
        self.previous_vehicle_assignments = {}
        for vehicle in self.assigner.vehicles:
            self.previous_vehicle_assignments[vehicle.vehicle_id] = {
                'assigned_bookings': vehicle.assigned_bookings.copy(),
                'route': vehicle.route.copy() if vehicle.route else [],
                'active_km': vehicle.active_km,
                'dead_km': vehicle.dead_km,
                'total_driver_pay': vehicle.total_driver_pay
            }
    
    def print_assignment_changes(self, newly_loaded_bookings: List[Dict], step_count: int):
        """Print what changed compared to previous step"""
        if not self.previous_vehicle_assignments:
            return
        
        print(f"\n{'='*120}")
        print(f"🔄 STEP {step_count} ASSIGNMENT CHANGES SUMMARY")
        print(f"{'='*120}")
        
        if newly_loaded_bookings:
            print(f"\n📦 NEWLY LOADED INSTANT BOOKINGS ({len(newly_loaded_bookings)}):")
            for booking in newly_loaded_bookings:
                pickup_time = booking.get("pickup_time", "N/A")
                print(f"  • Booking {booking['booking_id']}: {booking.get('vehicle_type', 'N/A')} - Pickup at {pickup_time}")
        
        vehicles_with_changes = []
        vehicles_with_new_bookings = []
        vehicles_unchanged = []
        
        for vehicle in self.assigner.vehicles:
            vehicle_id = vehicle.vehicle_id
            current_bookings = set(vehicle.assigned_bookings)
            previous_bookings = set(self.previous_vehicle_assignments.get(vehicle_id, {}).get('assigned_bookings', []))
            
            if current_bookings != previous_bookings:
                new_bookings = current_bookings - previous_bookings
                removed_bookings = previous_bookings - current_bookings
                
                if new_bookings or removed_bookings:
                    vehicles_with_changes.append({
                        'vehicle_id': vehicle_id,
                        'vehicle_type': vehicle.vehicle_type,
                        'new_bookings': list(new_bookings),
                        'removed_bookings': list(removed_bookings),
                        'total_bookings': len(current_bookings),
                        'active_km_change': vehicle.active_km - self.previous_vehicle_assignments.get(vehicle_id, {}).get('active_km', 0),
                        'dead_km_change': vehicle.dead_km - self.previous_vehicle_assignments.get(vehicle_id, {}).get('dead_km', 0)
                    })
                    
                    if new_bookings:
                        vehicles_with_new_bookings.append(vehicle_id)
            else:
                vehicles_unchanged.append(vehicle_id)
        
        print(f"\n🚗 VEHICLE ASSIGNMENT CHANGES:")
        print(f"{'Vehicle ID':<10} {'Type':<7} {'Change Type':<15} {'New':<5} {'Removed':<8} {'Total':<6} {'ΔActive KM':<12} {'ΔDead KM':<10}")
        print("-"*80)
        
        for change in vehicles_with_changes:
            change_type = "NEW BOOKINGS" if change['new_bookings'] and not change['removed_bookings'] else \
                         "REASSIGNMENT" if change['new_bookings'] and change['removed_bookings'] else \
                         "REMOVED ONLY"
            
            print(f"{change['vehicle_id']:<10} {change['vehicle_type']:<7} {change_type:<15} "
                  f"{len(change['new_bookings']):<5} {len(change['removed_bookings']):<8} "
                  f"{change['total_bookings']:<6} {change['active_km_change']:<12.2f} {change['dead_km_change']:<10.2f}")
            
            if change['new_bookings']:
                new_booking_types = []
                for booking_id in change['new_bookings']:
                    is_instant = any(ib.booking_id == booking_id for ib in self.instant_bookings if ib.is_loaded)
                    booking_type = "INSTANT" if is_instant else "SCHEDULED"
                    new_booking_types.append(f"{booking_id}({booking_type})")
                print(f"           Added: {', '.join(new_booking_types)}")
            
            if change['removed_bookings']:
                print(f"           Removed: {', '.join(map(str, change['removed_bookings']))}")
        
        if vehicles_unchanged:
            print(f"\n✅ VEHICLES UNCHANGED ({len(vehicles_unchanged)}): {', '.join(map(str, vehicles_unchanged))}")
        
        total_new_assignments = sum(len(change['new_bookings']) for change in vehicles_with_changes)
        total_reassignments = sum(len(change['removed_bookings']) for change in vehicles_with_changes)
        
        print(f"\n📊 STEP {step_count} CHANGE SUMMARY:")
        print(f"  • Vehicles with changes: {len(vehicles_with_changes)}")
        print(f"  • Vehicles unchanged: {len(vehicles_unchanged)}")
        print(f"  • New instant bookings loaded: {len(newly_loaded_bookings)}")
        print(f"  • Total new assignments: {total_new_assignments}")
        print(f"  • Total reassignments: {total_reassignments}")
        
        print(f"{'='*120}\n")
    
    def _get_booking_pickup_time(self, booking_id: int) -> Optional[int]:
        """Get pickup time for a booking ID"""
        for booking in self.scheduled_bookings:
            if booking["booking_id"] == booking_id:
                return _get_pickup_time_minutes(booking["pickup_time"])
        
        for booking in self.loaded_instant_bookings:
            if booking["booking_id"] == booking_id:
                return _get_pickup_time_minutes(booking["pickup_time"])
        
        return None
    
    def _get_booking_completion_time(self, booking_id: int) -> Optional[int]:
        """Get completion time for a booking (pickup + travel + service time)"""
        for booking in self.loaded_instant_bookings:
            if booking["booking_id"] == booking_id:
                pickup_time = _get_pickup_time_minutes(booking["pickup_time"])
                travel_time = booking.get("travel_time", 30)
                service_time = 30
                return pickup_time + travel_time + service_time
        
        for booking in self.scheduled_bookings:
            if booking["booking_id"] == booking_id:
                pickup_time = _get_pickup_time_minutes(booking["pickup_time"])
                travel_time = booking.get("travel_time", 30)
                service_time = 30
                return pickup_time + travel_time + service_time
        
        return None
    
    @staticmethod
    def _calculate_driver_pay(active_km, dead_km, vehicle_type, assigner):
        """Calculate total driver pay for a vehicle based on active and dead km and vehicle type."""
        # Use the same pay rates as in HomeOrientedBookingAssigner
        active_pay = assigner.active_driver_pay.get(vehicle_type, 16)
        dead_pay = assigner.dead_driver_pay.get(vehicle_type, 10)
        return (active_km * active_pay) + (dead_km * dead_pay)
    
    def _recalculate_vehicle_metrics_for_locked_bookings(self, vehicle: VehicleState, locked_booking_ids: List[int]):
        """Recalculate vehicle metrics for locked bookings only - WITHOUT final home return"""
        if not locked_booking_ids:
            vehicle.route = []
            vehicle.active_km = 0.0
            vehicle.dead_km = 0.0
            vehicle.total_driver_pay = 0.0
            vehicle.is_routed = False
            vehicle.available_time = 6 * 60  # Reset to 6 AM
            return
        
        locked_bookings_data = []
        for booking_id in locked_booking_ids:
            booking_data = self._find_booking_data(booking_id)
            if booking_data:
                locked_bookings_data.append(booking_data)
        
        if not locked_bookings_data:
            vehicle.route = []
            vehicle.active_km = 0.0
            vehicle.dead_km = 0.0
            vehicle.total_driver_pay = 0.0
            vehicle.is_routed = False
            vehicle.available_time = self.start_time  # Reset to start time
            return
        
        locked_bookings_data.sort(key=lambda x: _get_pickup_time_minutes(x["pickup_time"]))
        
        vehicle.route = []
        for booking in locked_bookings_data:
            pickup_location = (booking["pickup_lat"], booking["pickup_lon"])
            drop_location = (booking["drop_lat"], booking["drop_lon"])
            vehicle.route.extend([pickup_location, drop_location])
        
        try:
            from service import _calculate_active_km
            vehicle.dead_km = self._calculate_locked_dead_km(vehicle, locked_bookings_data)
            vehicle.active_km = _calculate_active_km(vehicle.route, locked_bookings_data)
            vehicle.total_driver_pay = self._calculate_driver_pay(
                vehicle.active_km, vehicle.dead_km, vehicle.vehicle_type, self.assigner
            )
            vehicle.is_routed = True
            
            last_booking = locked_bookings_data[-1]
            last_pickup_time = _get_pickup_time_minutes(last_booking["pickup_time"])
            travel_time = last_booking.get("travel_time", 30)
            service_time = 30
            vehicle.available_time = last_pickup_time + travel_time + service_time
            
            logger.info(f"Vehicle {vehicle.vehicle_id} locked metrics: "
                       f"Active KM: {vehicle.active_km:.2f}, Dead KM (no final home): {vehicle.dead_km:.2f}, "
                       f"Driver Pay: ₹{vehicle.total_driver_pay:.2f}, Available at: {self._sim_time_to_datetime(vehicle.available_time).strftime('%H:%M')}")
                       
        except ImportError:
            total_distance = sum(booking.get("distance_km", 0) for booking in locked_bookings_data)
            vehicle.active_km = total_distance
            vehicle.dead_km = self._calculate_locked_dead_km_fallback(vehicle, locked_bookings_data)
            vehicle.total_driver_pay = (vehicle.active_km + vehicle.dead_km) * 15
            vehicle.is_routed = True
            
            if locked_bookings_data:
                last_booking = locked_bookings_data[-1]
                last_pickup_time = _get_pickup_time_minutes(last_booking["pickup_time"])
                vehicle.available_time = last_pickup_time + 60
    
    def _finalize_combined_metrics(self):
        """Finalize metrics for all vehicles by adding final return home distance"""
        from Helper_func import get_distance
        
        for vehicle in self.assigner.vehicles:
            if vehicle.assigned_bookings and vehicle.route:
                if len(vehicle.route) >= 2:
                    last_lat, last_lng = vehicle.route[-1]
                    home_location = (vehicle.home_lat, vehicle.home_lng)
                    final_home_distance = get_distance((last_lat, last_lng), home_location)
                    
                    vehicle.dead_km += final_home_distance
                    dead_pay_rate = self.assigner.dead_driver_pay.get(vehicle.vehicle_type, 10)
                    vehicle.total_driver_pay += final_home_distance * dead_pay_rate
                    
                    logger.info(f"Vehicle {vehicle.vehicle_id} final metrics: "
                               f"Added {final_home_distance:.2f}km return home distance. "
                               f"Total Dead KM: {vehicle.dead_km:.2f}, Total Driver Pay: ₹{vehicle.total_driver_pay:.2f}")
                else:
                    logger.warning(f"Vehicle {vehicle.vehicle_id} has assigned bookings but insufficient route data")
    
    def _calculate_locked_dead_km(self, vehicle: VehicleState, locked_bookings_data: List[Dict]) -> float:
        """Calculate dead km for locked bookings WITHOUT final return to home"""
        from Helper_func import get_distance
        
        total_dead_km = 0.0
        current_lat, current_lng = vehicle.home_lat, vehicle.home_lng
        
        for i, booking in enumerate(locked_bookings_data):
            pickup_lat, pickup_lng = booking["pickup_lat"], booking["pickup_lon"]
            dead_km_to_pickup = get_distance((current_lat, current_lng), (pickup_lat, pickup_lng))
            total_dead_km += dead_km_to_pickup
            current_lat, current_lng = booking["drop_lat"], booking["drop_lon"]
        
        return total_dead_km
    
    def _calculate_locked_dead_km_fallback(self, vehicle: VehicleState, locked_bookings_data: List[Dict]) -> float:
        """Fallback calculation for locked dead km without home return"""
        total_active = sum(booking.get("distance_km", 0) for booking in locked_bookings_data)
        return total_active * 0.35
    
    def _copy_vehicle_state(self, vehicle: VehicleState) -> VehicleState:
        """Create a copy of vehicle state for temporary assignment"""
        from copy import deepcopy
        return deepcopy(vehicle)
    
    def print_assignment_table(self, title: str, all_bookings: List[Dict], current_time_hours: float = None):
        """Print detailed assignment table with vehicle and booking information"""
        print(f"\n{'='*150}")
        if current_time_hours is not None:
            print(f"{title} (Simulation Time: {current_time_hours:.1f} hours)")
        else:
            print(f"{title}")
        print(f"{'='*150}")
        
        print(f"\n🚗 VEHICLE ASSIGNMENTS:")
        print(f"{'Vehicle ID':<10} {'Type':<7} {'Status':<12} {'Bookings':<9} {'Active KM':<10} {'Dead KM':<9} {'Customer Fare':<13} {'Driver Pay':<11} {'Profit':<10} {'Efficiency':<10}")
        print("-"*150)
        
        total_vehicles_with_bookings = 0
        total_vehicles_with_locked_bookings = 0
        total_available_vehicles = 0
        
        for vehicle in self.assigner.vehicles:
            if vehicle.assigned_bookings:
                total_vehicles_with_bookings += 1
                if hasattr(vehicle, 'last_drop_time_minutes') and vehicle.last_drop_time_minutes:
                    is_available = self.current_sim_time >= vehicle.last_drop_time_minutes
                    status = "AVAILABLE" if is_available else "BUSY"
                else:
                    status = "AVAILABLE"
                
                has_locked_bookings = any(bid in self.locked_bookings for bid in vehicle.assigned_bookings)
                if has_locked_bookings:
                    total_vehicles_with_locked_bookings += 1
                
                vehicle_customer_fare = 0
                for booking_id in vehicle.assigned_bookings:
                    booking = next((b for b in all_bookings if b["booking_id"] == booking_id), None)
                    if booking:
                        active_distance = booking.get("distance_km", 0)
                        vehicle_type = vehicle.vehicle_type
                        dead_km_factor = self.assigner.dead_km_percentage.get(vehicle_type, 0.40)
                        customer_price = self.assigner.customer_price_per_km.get(vehicle_type, 20)
                        booking_fare = (active_distance + active_distance * dead_km_factor) * customer_price
                        vehicle_customer_fare += booking_fare
                
                vehicle_profit = vehicle_customer_fare - vehicle.total_driver_pay
                efficiency = (vehicle.active_km/(vehicle.active_km + vehicle.dead_km)*100) if (vehicle.active_km + vehicle.dead_km) > 0 else 0
                
                print(f"{vehicle.vehicle_id:<10} {vehicle.vehicle_type:<7} {status:<12} {len(vehicle.assigned_bookings):<9} {vehicle.active_km:<10.2f} {vehicle.dead_km:<9.2f} ₹{vehicle_customer_fare:<12.2f} ₹{vehicle.total_driver_pay:<10.2f} ₹{vehicle_profit:<9.2f} {efficiency:<9.1f}%")
            else:
                total_available_vehicles += 1
        
        print(f"\n📊 Vehicle Status Summary:")
        print(f"  • Vehicles with bookings: {total_vehicles_with_bookings}")
        print(f"  • Vehicles with locked bookings: {total_vehicles_with_locked_bookings}")
        print(f"  • Available vehicles: {total_available_vehicles}")
        
        print(f"\n📋 BOOKING ASSIGNMENTS:")
        print(f"{'Booking ID':<10} {'Type':<7} {'Distance':<8} {'Pickup Time':<12} {'Vehicle ID':<10} {'Vehicle Type':<12} {'Assignment':<12} {'Lock Status':<11}")
        print("-"*120)
        
        booking_to_vehicle = {}
        for vehicle in self.assigner.vehicles:
            for booking_id in vehicle.assigned_bookings:
                booking_to_vehicle[booking_id] = {
                    'vehicle_id': vehicle.vehicle_id,
                    'vehicle_type': vehicle.vehicle_type
                }
        
        sorted_bookings = sorted(all_bookings, key=lambda x: _get_pickup_time_minutes(x["pickup_time"]))
        
        assigned_count = 0
        locked_count = 0
        unlocked_count = 0
        unassigned_count = 0
        
        for booking in sorted_bookings:
            booking_id = booking["booking_id"]
            if booking_id in booking_to_vehicle:
                assignment = booking_to_vehicle[booking_id]
                pickup_time = booking.get("pickup_time", "N/A")
                
                if booking_id in self.locked_bookings:
                    lock_status = "LOCKED"
                    locked_count += 1
                else:
                    lock_status = "UNLOCKED"
                    unlocked_count += 1
                
                is_instant = any(ib.booking_id == booking_id for ib in self.instant_bookings if ib.is_loaded)
                assignment_type = "INSTANT" if is_instant else "SCHEDULED"
                
                assigned_count += 1
                print(f"{booking_id:<10} {booking.get('vehicle_type', 'N/A'):<7} {booking.get('distance_km', 0):<8.1f} {pickup_time:<12} {assignment['vehicle_id']:<10} {assignment['vehicle_type']:<12} {assignment_type:<12} {lock_status:<11}")
            else:
                is_instant = any(ib.booking_id == booking_id for ib in self.instant_bookings if ib.is_loaded)
                lock_status = "UNLOCKED" if is_instant else "N/A"
                assignment_type = "INSTANT" if is_instant else "UNASSIGNED"
                
                unassigned_count += 1
                print(f"{booking_id:<10} {booking.get('vehicle_type', 'N/A'):<7} {booking.get('distance_km', 0):<8.1f} {booking.get('pickup_time', 'N/A'):<12} {'UNASSIGNED':<10} {'N/A':<12} {assignment_type:<12} {lock_status:<11}")
        
        print(f"\n📈 Booking Status Summary:")
        print(f"  • Total bookings: {len(all_bookings)}")
        print(f"  • Assigned: {assigned_count}")
        print(f"  • Locked: {locked_count}")
        print(f"  • Unlocked: {unlocked_count}")
        print(f"  • Unassigned: {unassigned_count}")
    
    def calculate_and_print_metrics(self, title: str, all_bookings: List[Dict]):
        """Calculate and print comprehensive metrics"""
        metrics = self.assigner.calculate_final_metrics(all_bookings)
        
        print(f"\n💰 {title} FINANCIAL METRICS:")
        print(f"Total Customer Fare: ₹{metrics['total_customer_fare']:.2f}")
        print(f"Total Driver Pay: ₹{metrics['total_driver_pay']:.2f}")
        print(f"Total Profit: ₹{metrics['total_profit']:.2f}")
        print(f"Overall Efficiency: {metrics['overall_efficiency']:.1f}%")
        print(f"Total Active KM: {metrics['total_active_km']:.2f}")
        print(f"Total Dead KM: {metrics['total_dead_km']:.2f}")
        print(f"Assigned Bookings: {metrics['assigned_bookings']}")
        print(f"Unassigned Bookings: {metrics['unassigned_bookings']}")
        
        return metrics
    
    def _is_booking_assigned(self, booking_id: int) -> bool:
        """Check if a booking is already assigned to any vehicle"""
        for vehicle in self.assigner.vehicles:
            if booking_id in vehicle.assigned_bookings:
                return True
        return False
    
    def _sim_time_to_datetime(self, sim_time_minutes: int) -> datetime:
        """Convert simulation time (minutes from midnight) to datetime for logging"""
        hours = sim_time_minutes // 60
        minutes = sim_time_minutes % 60
        return datetime.now().replace(hour=hours, minute=minutes, second=0, microsecond=0)
    
    def print_simulation_status(self):
        """Print current simulation status"""
        current_datetime = self._sim_time_to_datetime(self.current_sim_time)
        
        loaded_count = len(self.loaded_instant_bookings)
        assigned_instant = sum(1 for b in self.loaded_instant_bookings if self._is_booking_assigned(b["booking_id"]))
        
        print(f"\n{'='*80}")
        print(f"SIMULATION STATUS - {current_datetime.strftime('%H:%M')}")
        print(f"{'='*80}")
        print(f"Loaded Instant Bookings: {loaded_count}")
        print(f"Assigned Instant Bookings: {assigned_instant}")
        print(f"Locked Bookings: {len(self.locked_bookings)}")
        
        next_bookings = [ib for ib in self.instant_bookings if not ib.is_loaded and ib.load_time > self.current_sim_time]
        if next_bookings:
            next_booking = min(next_bookings, key=lambda x: x.load_time)
            next_load_time = self._sim_time_to_datetime(next_booking.load_time)
            print(f"Next Instant Booking: ID {next_booking.booking_id} loads at {next_load_time.strftime('%H:%M')}")
        
        print(f"{'='*80}\n")
    
    def run_simulation(self):
        """Run the main simulation loop"""
        logger.info("🚀 Starting Real-Time Dispatch Simulation...")
        logger.info(f"Simulation: 1 hour = {self.real_time_step} seconds, Step interval = {self.step_interval} minutes")
        
        self.current_sim_time = self.start_time
        step_count = 0
        
        while self.current_sim_time < self.end_time:
            step_count += 1
            current_datetime = self._sim_time_to_datetime(self.current_sim_time)
            current_time_hours = self.current_sim_time / 60.0
            
            logger.info(f"\n⏰ SIMULATION STEP {step_count} - {current_datetime.strftime('%H:%M')}")
            
            # 1. Check and load instant bookings
            newly_loaded_bookings = self.check_and_load_instant_bookings()
            has_changes = len(newly_loaded_bookings) > 0
            
            # Get newly loaded bookings for comparison
            # newly_loaded_bookings = []
            # if new_bookings_loaded:
            #     newly_loaded_bookings = [booking for booking in self.loaded_instant_bookings 
            #                            if any(ib.booking_id == booking["booking_id"] and ib.is_loaded 
            #                                 for ib in self.instant_bookings 
            #                                 if ib.load_time == self.current_sim_time or ib.load_time <= self.current_sim_time)]
            
            # 2. Update locked assignments based on current time
            self.update_locked_assignments()
            
            # 3. Reassign if there are new bookings loaded in this step
            if newly_loaded_bookings:
                if step_count > 1:
                    self.print_assignment_changes(newly_loaded_bookings, step_count)
                
                logger.info(f"🔄 Reassigning with {len(newly_loaded_bookings)} new instant bookings...")
                self.reassign_unlocked_bookings()
                self.last_assignment_time = self.current_sim_time
                has_changes = True
            else:
                logger.info("No new instant bookings loaded - skipping reassignment")
            
            # 4. Print detailed tables and routes if there were changes or every 4 steps
            if has_changes or step_count <= 2:  # Always show first 2 steps and any step with changes
                all_current_bookings = []
                try:
                    with open('data/bookings.json', 'r') as f:
                        scheduled_bookings = json.load(f)
                    all_current_bookings.extend(scheduled_bookings)
                except:
                    pass
                
                all_current_bookings.extend(self.loaded_instant_bookings)
                
                step_title = f"STEP {step_count} ASSIGNMENTS"
                if has_changes:
                    step_title += f" (WITH {len(newly_loaded_bookings)} NEW INSTANT BOOKINGS)"
                else:
                    step_title += " (STATUS UPDATE)"
                
                self.print_assignment_table(step_title, all_current_bookings, current_time_hours)
                self.calculate_and_print_metrics(f"STEP {step_count}", all_current_bookings)
                # Add detailed vehicle route analysis
                self.assigner.print_detailed_vehicle_routes(all_current_bookings)
                
                self._store_current_assignments_for_comparison()
            
            # 5. Brief status for steps without major changes
            else:
                logger.info(f"📊 Step {step_count}: {len(self.locked_bookings)} locked bookings, "
                           f"{len(self.loaded_instant_bookings)} total instant bookings loaded")
            
            # 6. Advance simulation time
            self.current_sim_time += self.step_interval
            
            # 7. Sleep for real-time simulation
            if self.current_sim_time < self.end_time:
                logger.info(f"💤 Sleeping for {self.real_time_step} seconds (next step: {self._sim_time_to_datetime(self.current_sim_time).strftime('%H:%M')})")
                time.sleep(self.real_time_step)
        
        logger.info("🏁 Simulation completed!")
        # Final assignment attempt for any remaining unassigned bookings
        self._final_assignment_attempt()
        self.print_final_results()
    
    def _final_assignment_attempt(self):
        """Final attempt to assign any remaining unassigned bookings"""
        logger.info("🎯 Final assignment attempt for any unassigned bookings...")
        
        # Get all bookings (scheduled + instant)
        all_bookings = []
        try:
            with open('data/bookings.json', 'r') as f:
                scheduled_bookings = json.load(f)
            all_bookings.extend(scheduled_bookings)
        except:
            pass
        all_bookings.extend(self.loaded_instant_bookings)
        
        # Find unassigned bookings
        unassigned_bookings = []
        for booking in all_bookings:
            if not self._is_booking_assigned(booking["booking_id"]):
                unassigned_bookings.append(booking)
        
        if not unassigned_bookings:
            logger.info("✅ All bookings are already assigned!")
            return
        
        logger.info(f"🔄 Found {len(unassigned_bookings)} unassigned bookings, attempting final assignment...")
        
        assigned_count = 0
        
        # Try to assign to vehicles with least bookings first
        vehicles_by_load = sorted(self.assigner.vehicles, key=lambda v: len(v.assigned_bookings))
        
        for booking in unassigned_bookings:
            assigned = False
            
            # Try each vehicle starting with least loaded
            for vehicle in vehicles_by_load:
                # Skip if vehicle is overloaded
                if len(vehicle.assigned_bookings) >= 10:
                    continue
                
                # Check basic vehicle type compatibility (allow upgrades)
                booking_type = booking.get("vehicle_type", "class1")
                booking_class = int(booking_type.replace("class", ""))
                vehicle_class = int(vehicle.vehicle_type.replace("class", ""))
                
                if vehicle_class >= booking_class:
                    # Assign directly without strict availability checks
                    self.assigner.assign_booking_to_vehicle(booking, vehicle)
                    assigned = True
                    assigned_count += 1
                    logger.info(f"Final assignment: Booking {booking['booking_id']} assigned to vehicle {vehicle.vehicle_id}")
                    break
            
            if not assigned:
                logger.warning(f"Final assignment failed for booking {booking['booking_id']}")
        
        if assigned_count > 0:
            # Recalculate metrics after final assignments
            self._finalize_combined_metrics()
            logger.info(f"✅ Final assignment complete: {assigned_count} additional bookings assigned")
        else:
            logger.warning("❌ Final assignment: No additional bookings could be assigned")
    
    def print_final_results(self):
        """Print final simulation results"""
        print(f"\n{'='*100}")
        print("FINAL SIMULATION RESULTS")
        print(f"{'='*100}")
        
        total_instant_bookings = len(self.instant_bookings)
        loaded_instant_bookings = len(self.loaded_instant_bookings)
        assigned_instant = sum(1 for b in self.loaded_instant_bookings if self._is_booking_assigned(b["booking_id"]))
        
        print(f"\n📈 INSTANT BOOKING SUMMARY:")
        print(f"Total Instant Bookings: {total_instant_bookings}")
        print(f"Loaded Instant Bookings: {loaded_instant_bookings}")
        print(f"Assigned Instant Bookings: {assigned_instant}")
        print(f"Assignment Rate: {(assigned_instant/loaded_instant_bookings*100) if loaded_instant_bookings > 0 else 0:.1f}%")
        
        all_final_bookings = []
        try:
            with open('data/bookings.json', 'r') as f:
                scheduled_bookings = json.load(f)
            all_final_bookings.extend(scheduled_bookings)
        except:
            pass
        
        all_final_bookings.extend(self.loaded_instant_bookings)
        
        self.print_assignment_table("FINAL COMPREHENSIVE ASSIGNMENTS", all_final_bookings)
        final_metrics = self.calculate_and_print_metrics("FINAL", all_final_bookings)
        
        print(f"\n🎯 SIMULATION COMPLETION SUMMARY:")
        print(f"  • Total bookings processed: {len(all_final_bookings)}")
        print(f"  • Scheduled bookings: {len(scheduled_bookings) if 'scheduled_bookings' in locals() else 0}")
        print(f"  • Instant bookings loaded: {loaded_instant_bookings}")
        print(f"  • Overall assignment rate: {(final_metrics['assigned_bookings']/len(all_final_bookings)*100) if len(all_final_bookings) > 0 else 0:.1f}%")
        
        print(f"{'='*100}")

import sys

class TeeOutput:
    """Class to write output to both terminal and file"""
    def __init__(self, *files):
        self.files = files
    
    def write(self, text):
        for file in self.files:
            file.write(text)
            file.flush()
    
    def flush(self):
        for file in self.files:
            file.flush()

def main():
    with open('log.txt', 'w') as log_file:
        tee_stdout = TeeOutput(sys.stdout, log_file)
        tee_stderr = TeeOutput(sys.stderr, log_file)
        
        original_stdout = sys.stdout
        original_stderr = sys.stderr
        sys.stdout = tee_stdout
        sys.stderr = tee_stderr
        
        try:
            simulator = RealTimeDispatchSimulator()
            with open('data/vehicles.json', 'r') as f:
                vehicles_data = json.load(f)
            with open('data/bookings.json', 'r') as f:
                scheduled_bookings = json.load(f)
            simulator.load_instant_booking_dataset('data/instant_bookings.json')
            
            print(f"\n{'='*100}")
            print("🚀 REAL-TIME DISPATCH SIMULATION STARTING")
            print(f"{'='*100}")
            
            logger.info("STEP 1: Running scheduled booking assignment...")
            simulator.load_scheduled_assignments(vehicles_data, scheduled_bookings)
            
            print(f"\n📋 SCHEDULED ASSIGNMENT SUMMARY:")
            total_scheduled_assigned = sum(len(bookings) for bookings in simulator.scheduled_assignments.values())
            print(f"  • Assigned scheduled bookings: {total_scheduled_assigned}")
            print(f"  • Total instant bookings to simulate: {len(simulator.instant_bookings)}")
            
            logger.info("\nSTEP 2: Starting real-time simulation...")
            simulator.run_simulation()
            
        except Exception as e:
            logger.error(f"Error in simulation: {e}")
            import traceback
            traceback.print_exc()
        finally:
            sys.stdout = original_stdout
            sys.stderr = original_stderr

if __name__ == "__main__":
    main()