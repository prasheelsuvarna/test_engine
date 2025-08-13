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
        self.end_time = 11 * 60    # 11 AM in minutes (changed from 8 PM for easier testing)
        self.step_interval = 30    # 30 minutes per step
        self.real_time_step = 6    # 6 seconds real time per step
        
        # Booking management
        self.scheduled_assignments = {}  # From home_oriented_main.py results
        self.scheduled_bookings: List[Dict] = []  # Store scheduled booking data
        self.instant_bookings: List[InstantBooking] = []
        self.loaded_instant_bookings: List[Dict] = []
        self.locked_bookings: Set[int] = set()
        # NOTE: No more locked_vehicles - vehicles are never locked, only bookings are
        
        # Track simulation state
        self.last_assignment_time = 0
        self.new_bookings_since_last_run = False
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
        
        # CRITICAL FIX: Reset vehicle availability times for real-time simulation
        # Don't let scheduled routes block instant bookings
        for vehicle in self.assigner.vehicles:
            # Reset to start of simulation time (6 AM = 360 minutes)
            vehicle.available_time = self.start_time
            # Keep assigned bookings but reset route state for fresh assignment
            vehicle.is_routed = False
        
        logger.info(f"Loaded scheduled assignments: {len(assigned_bookings)} bookings assigned to vehicles")
        logger.info("Reset vehicle availability times for real-time instant booking assignment")
    
    # this function will be modified to make query from the booking table for live instant bookings 
    # the instant bookings will be be stored as list of dic tionaries but will be dirctly processed from the booking table as they arrive
    def load_instant_booking_dataset(self, instant_bookings_file: str):
        """Load instant bookings dataset and calculate their load times"""
        try:
            with open(instant_bookings_file, 'r') as f:
                instant_bookings_data = json.load(f)
            
            self.instant_bookings = []
            for booking in instant_bookings_data:
                pickup_time = _get_pickup_time_minutes(booking["pickup_time"])
                
                # Calculate random load time: between 2 hours and 1 hour before pickup
                # e.g., for 12 PM pickup, load between 10-11 AM
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
        new_bookings_loaded = False
        
        for instant_booking in self.instant_bookings:
            if not instant_booking.is_loaded and instant_booking.load_time <= self.current_sim_time:
                # Load this instant booking into the system
                self.loaded_instant_bookings.append(instant_booking.booking_data)
                instant_booking.is_loaded = True
                new_bookings_loaded = True
                
                current_datetime = self._sim_time_to_datetime(self.current_sim_time)
                pickup_datetime = self._sim_time_to_datetime(instant_booking.pickup_time)
                
                logger.info(f"🚨 INSTANT BOOKING LOADED: Booking {instant_booking.booking_id} "
                          f"loaded at {current_datetime.strftime('%H:%M')} for pickup at {pickup_datetime.strftime('%H:%M')}")
        
        if new_bookings_loaded:
            self.new_bookings_since_last_run = True
        
        return new_bookings_loaded
    
    def update_locked_assignments(self):
        """Update locked bookings and calculate vehicle availability times based on locked bookings"""
        # Clear previous locks
        self.locked_bookings.clear()
        # NOTE: NO MORE VEHICLE LOCKING - vehicles are never locked, only bookings
        
        # Lock bookings whose pickup time is PAST current time OR within 2 hours of current sim time
        lock_window = self.current_sim_time + 120  # 2 hours ahead
        
        # Update each vehicle's availability time based on their last locked booking completion
        for vehicle in self.assigner.vehicles:
            vehicle_last_locked_completion = self.current_sim_time  # Default to current time if no locked bookings
            
            for booking_id in vehicle.assigned_bookings:
                # Find booking data to get pickup time
                pickup_time = self._get_booking_pickup_time(booking_id)
                if pickup_time and (pickup_time <= self.current_sim_time or pickup_time <= lock_window):
                    # This booking is locked (pickup in past OR pickup within 2 hours)
                    self.locked_bookings.add(booking_id)
                    
                    # Calculate completion time of this locked booking
                    completion_time = self._get_booking_completion_time(booking_id)
                    if completion_time:
                        vehicle_last_locked_completion = max(vehicle_last_locked_completion, completion_time)
            
            # Update vehicle's available time to after last locked booking completion
            # This is when the vehicle becomes free for new assignments
            vehicle.available_time = vehicle_last_locked_completion
        
        # Check loaded instant bookings for locking (but don't lock vehicles)
        # NOTE: INSTANT BOOKINGS SHOULD NEVER BE LOCKED - they should always be available for assignment
        # The original logic incorrectly locked instant bookings within 2-hour window
        # for booking in self.loaded_instant_bookings:
        #     pickup_time = _get_pickup_time_minutes(booking["pickup_time"])
        #     if pickup_time <= self.current_sim_time or pickup_time <= lock_window:
        #         self.locked_bookings.add(booking["booking_id"])
        
        logger.info(f"🔒 Locked {len(self.locked_bookings)} bookings (past + next 2 hours, vehicles availability updated based on locked booking completion times)")
        logger.info(f"📦 Instant bookings are NEVER locked - {len(self.loaded_instant_bookings)} instant bookings remain available for assignment")
        
        # Log vehicle availability times for debugging
        for vehicle in self.assigner.vehicles:
            if vehicle.assigned_bookings:
                available_datetime = self._sim_time_to_datetime(vehicle.available_time)
                logger.info(f"Vehicle {vehicle.vehicle_id} available from {available_datetime.strftime('%H:%M')} (last locked booking completion)")
    
    def get_unlocked_bookings(self) -> List[Dict]:
        """Get ALL unlocked bookings (both scheduled and instant) that need reassignment"""
        unlocked_bookings = []
        
        # Get ALL assigned bookings from all vehicles and check if they're unlocked
        for vehicle in self.assigner.vehicles:
            for booking_id in vehicle.assigned_bookings:
                if booking_id not in self.locked_bookings:
                    # This booking is unlocked - find its data and add to reassignment list
                    booking_data = self._find_booking_data(booking_id)
                    if booking_data:
                        unlocked_bookings.append(booking_data)
        
        # Get unassigned loaded instant bookings (that are also unlocked)
        logger.info(f"Checking {len(self.loaded_instant_bookings)} loaded instant bookings for unlocked status")
        for booking in self.loaded_instant_bookings:
            booking_id = booking["booking_id"]
            is_locked = booking_id in self.locked_bookings
            is_assigned = self._is_booking_assigned(booking_id)
            logger.info(f"Instant booking {booking_id}: locked={is_locked}, assigned={is_assigned}")
            
            if not is_locked and not is_assigned:
                logger.info(f"✅ Adding instant booking {booking_id} to unlocked bookings list")
                unlocked_bookings.append(booking)
            else:
                logger.info(f"❌ Skipping instant booking {booking_id} - locked={is_locked}, assigned={is_assigned}")
        
        # Get any unassigned scheduled bookings (if they exist and are unlocked)
        for booking in self.assigner.unassigned_bookings:
            if booking["booking_id"] not in self.locked_bookings:
                unlocked_bookings.append(booking)
        
        logger.info(f"Total unlocked bookings found: {len(unlocked_bookings)}")
        return unlocked_bookings
    
    def get_available_vehicles(self) -> List[VehicleState]:
        """Get all vehicles with their updated availability times"""
        # All vehicles are available after their locked bookings complete
        # Their availability times are already updated in update_locked_assignments
        return self.assigner.vehicles
    
    def _find_booking_data(self, booking_id: int) -> Dict:
        """Find booking data by booking_id from either scheduled or instant bookings"""
        # Check in scheduled bookings first
        for booking in self.scheduled_bookings:
            if booking["booking_id"] == booking_id:
                return booking
        
        # Check in loaded instant bookings
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
        
        # PRESERVE LOCKED BOOKINGS: Only remove unlocked bookings from vehicles
        for vehicle in self.assigner.vehicles:
            # Separate locked and unlocked bookings for this vehicle
            locked_bookings_for_vehicle = [bid for bid in vehicle.assigned_bookings 
                                         if bid in self.locked_bookings]
            unlocked_bookings_for_vehicle = [bid for bid in vehicle.assigned_bookings 
                                           if bid not in self.locked_bookings]
            
            # Keep only locked bookings (preserve their assignments)
            vehicle.assigned_bookings = locked_bookings_for_vehicle
            
            # RECALCULATE vehicle metrics for locked bookings only
            # This ensures vehicle state (route, active_km, dead_km, driver_pay, available_time) 
            # reflects only the locked bookings, which is essential for proper assignment of unlocked bookings
            logger.info(f"Vehicle {vehicle.vehicle_id}: Preserving {len(locked_bookings_for_vehicle)} locked bookings, removing {len(unlocked_bookings_for_vehicle)} unlocked bookings")
            self._recalculate_vehicle_metrics_for_locked_bookings(vehicle, locked_bookings_for_vehicle)
        
        # Create a fresh assigner for unlocked bookings only
        temp_assigner = HomeOrientedBookingAssigner()
        temp_assigner.vehicles = [self._copy_vehicle_state(v) for v in self.assigner.vehicles]
        temp_assigner.assignments = {v.vehicle_id: v.assigned_bookings.copy() for v in self.assigner.vehicles}
        
        # Process ONLY unlocked bookings using the same logic as scheduled assignment
        ascending_bookings, descending_bookings = temp_assigner.sort_bookings_by_time(unlocked_bookings)
        assigned_booking_ids = set(self.locked_bookings)  # Start with locked bookings as already assigned
        
        assigned_count = 0
        for booking in ascending_bookings:
            if booking["booking_id"] in assigned_booking_ids:
                continue
            
            assigned = False
            booking_type = booking.get("vehicle_type", "class1")
            logger.info(f"🔍 Attempting to assign booking {booking['booking_id']} (type: {booking_type}) at {booking.get('pickup_time')}")
            
            # First, try to find suitable vehicles of the same class
            suitable_vehicles = temp_assigner.get_suitable_vehicles(booking)
            logger.info(f"Found {len(suitable_vehicles)} suitable vehicles for booking {booking['booking_id']}")
            
            if suitable_vehicles:
                logger.info(f"Testing {len(suitable_vehicles)} suitable vehicles for booking {booking['booking_id']}")
                # Select the best vehicle based on dead km - active km comparison
                best_vehicle = None
                best_difference = float('inf')
                
                for vehicle in suitable_vehicles:
                    # Check if vehicle is available for this booking considering its availability time
                    available = temp_assigner.is_vehicle_available_for_booking(vehicle, booking)
                    logger.info(f"Vehicle {vehicle.vehicle_id} ({vehicle.vehicle_type}): available={available}")
                    
                    if available:
                        # Test assignment: create a test route with this booking added
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
                    logger.info(f"✅ Assigning booking {booking['booking_id']} to vehicle {best_vehicle.vehicle_id}")
                    # Assign the unlocked booking to the best vehicle
                    temp_assigner.assign_booking_to_vehicle(booking, best_vehicle)
                    assigned = True
                    assigned_count += 1
                    assigned_booking_ids.add(booking["booking_id"])
                else:
                    logger.warning(f"❌ No suitable vehicle found for booking {booking['booking_id']} despite {len(suitable_vehicles)} candidates")
                    
                    # Complete vehicle route with remaining unlocked bookings
                    available_bookings = [b for b in unlocked_bookings if b["booking_id"] not in assigned_booking_ids]
                    additional_assigned = temp_assigner.complete_vehicle_route(
                        best_vehicle, available_bookings, descending_bookings, unlocked_bookings, assigned_booking_ids
                    )
                    
                    if additional_assigned:
                        assigned_booking_ids.update(additional_assigned)
                        assigned_count += len(additional_assigned)
            
            # Try higher class if not assigned
            if not assigned:
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
            else:
                logger.warning(f"❌ No suitable vehicles found for booking {booking['booking_id']} (type: {booking_type})")
            
            if not assigned:
                logger.warning(f"Could not reassign unlocked booking {booking['booking_id']}")
        
        # Update the main assigner with new assignments (locked + newly assigned unlocked)
        self.assigner.vehicles = temp_assigner.vehicles
        self.assigner.assignments = temp_assigner.assignments
        
        # CRITICAL: Now recalculate final metrics for all vehicles to include final home return
        self._finalize_combined_metrics()
        
        logger.info(f"✅ Reassignment complete: {assigned_count} unlocked bookings assigned, {len(self.locked_bookings)} locked bookings preserved")
    
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
        
        # Show newly loaded instant bookings
        if newly_loaded_bookings:
            print(f"\n📦 NEWLY LOADED INSTANT BOOKINGS ({len(newly_loaded_bookings)}):")
            for booking in newly_loaded_bookings:
                pickup_time = booking.get("pickup_time", "N/A")
                print(f"  • Booking {booking['booking_id']}: {booking.get('vehicle_type', 'N/A')} - Pickup at {pickup_time}")
        
        # Track changes for each vehicle
        vehicles_with_changes = []
        vehicles_with_new_bookings = []
        vehicles_unchanged = []
        
        for vehicle in self.assigner.vehicles:
            vehicle_id = vehicle.vehicle_id
            current_bookings = set(vehicle.assigned_bookings)
            previous_bookings = set(self.previous_vehicle_assignments.get(vehicle_id, {}).get('assigned_bookings', []))
            
            if current_bookings != previous_bookings:
                # Vehicle assignments changed
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
        
        # Print vehicle changes summary
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
            
            # Show specific booking changes
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
        
        # Summary statistics
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
        # Check in loaded instant bookings
        for booking in self.loaded_instant_bookings:
            if booking["booking_id"] == booking_id:
                return _get_pickup_time_minutes(booking["pickup_time"])
        
        # Check in scheduled bookings
        for booking in self.scheduled_bookings:
            if booking["booking_id"] == booking_id:
                return _get_pickup_time_minutes(booking["pickup_time"])
        
        return None
    
    def _get_booking_completion_time(self, booking_id: int) -> Optional[int]:
        """Get completion time for a booking (pickup + travel + service time)"""
        # Check in loaded instant bookings
        for booking in self.loaded_instant_bookings:
            if booking["booking_id"] == booking_id:
                pickup_time = _get_pickup_time_minutes(booking["pickup_time"])
                travel_time = booking.get("travel_time", 30)  # Default 30 minutes
                service_time = 30  # 30 minutes service time
                return pickup_time + travel_time + service_time
        
        # Check in scheduled bookings
        for booking in self.scheduled_bookings:
            if booking["booking_id"] == booking_id:
                pickup_time = _get_pickup_time_minutes(booking["pickup_time"])
                travel_time = booking.get("travel_time", 30)  # Default 30 minutes
                service_time = 30  # 30 minutes service time
                return pickup_time + travel_time + service_time
        
        return None
    
    def _recalculate_vehicle_metrics_for_locked_bookings(self, vehicle: VehicleState, locked_booking_ids: List[int]):
        """Recalculate vehicle metrics for locked bookings only - WITHOUT final home return"""
        if not locked_booking_ids:
            # No locked bookings - reset everything
            vehicle.route = []
            vehicle.active_km = 0.0
            vehicle.dead_km = 0.0
            vehicle.total_driver_pay = 0.0
            vehicle.is_routed = False
            vehicle.available_time = self.current_sim_time
            return
        
        # Get locked booking data
        locked_bookings_data = []
        for booking_id in locked_booking_ids:
            booking_data = self._find_booking_data(booking_id)
            if booking_data:
                locked_bookings_data.append(booking_data)
        
        if not locked_bookings_data:
            # No booking data found - reset everything
            vehicle.route = []
            vehicle.active_km = 0.0
            vehicle.dead_km = 0.0
            vehicle.total_driver_pay = 0.0
            vehicle.is_routed = False
            vehicle.available_time = self.current_sim_time
            return
        
        # Sort locked bookings by pickup time to reconstruct route
        locked_bookings_data.sort(key=lambda x: _get_pickup_time_minutes(x["pickup_time"]))
        
        # Reconstruct route with locked bookings only
        vehicle.route = []
        for booking in locked_bookings_data:
            pickup_location = (booking["pickup_lat"], booking["pickup_lon"])
            drop_location = (booking["drop_lat"], booking["drop_lon"])
            vehicle.route.extend([pickup_location, drop_location])
        
        # Recalculate metrics using service functions
        try:
            from service import _calculate_ddm_without_final_home, _calculate_active_km, _calculate_driver_pay
            
            # Calculate dead km for locked bookings WITHOUT final return to home
            # This includes: home-to-first + inter-booking dead km (NO final-to-home)
            vehicle.dead_km = self._calculate_locked_dead_km(vehicle, locked_bookings_data)
            vehicle.active_km = _calculate_active_km(vehicle.route, locked_bookings_data)
            
            # Calculate driver pay for locked bookings only
            vehicle.total_driver_pay = _calculate_driver_pay(
                vehicle.active_km, vehicle.dead_km, vehicle.vehicle_type, self.assigner
            )
            
            # Set vehicle as routed
            vehicle.is_routed = True
            
            # Update available time to completion of last locked booking
            last_booking = locked_bookings_data[-1]  # Last booking by pickup time
            last_pickup_time = _get_pickup_time_minutes(last_booking["pickup_time"])
            travel_time = last_booking.get("travel_time", 30)  # Default 30 minutes
            service_time = 30  # 30 minutes service time
            vehicle.available_time = last_pickup_time + travel_time + service_time
            
            logger.info(f"Vehicle {vehicle.vehicle_id} locked metrics: "
                       f"Active KM: {vehicle.active_km:.2f}, Dead KM (no final home): {vehicle.dead_km:.2f}, "
                       f"Driver Pay: ₹{vehicle.total_driver_pay:.2f}, Available at: {self._sim_time_to_datetime(vehicle.available_time).strftime('%H:%M')}")
                       
        except ImportError:
            logger.warning("Service functions not available - using simplified calculation")
            # Fallback calculation
            total_distance = sum(booking.get("distance_km", 0) for booking in locked_bookings_data)
            vehicle.active_km = total_distance
            # Calculate dead km without final home return
            vehicle.dead_km = self._calculate_locked_dead_km_fallback(vehicle, locked_bookings_data)
            vehicle.total_driver_pay = (vehicle.active_km + vehicle.dead_km) * 15  # Assume ₹15 per km
            vehicle.is_routed = True
            
            # Update available time
            if locked_bookings_data:
                last_booking = locked_bookings_data[-1]
                last_pickup_time = _get_pickup_time_minutes(last_booking["pickup_time"])
                vehicle.available_time = last_pickup_time + 60  # Add 1 hour buffer
    
    def _finalize_combined_metrics(self):
        """Finalize metrics for all vehicles by adding final return home distance"""
        from Helper_func import get_distance
        
        for vehicle in self.assigner.vehicles:
            if vehicle.assigned_bookings and vehicle.route:
                # Get the last location from the route
                if len(vehicle.route) >= 2:
                    # Last location is the last drop-off point
                    last_lat, last_lng = vehicle.route[-1]  # Last point in route (should be a drop location)
                    
                    # Calculate distance from last drop to home
                    home_location = (vehicle.home_lat, vehicle.home_lng)
                    final_home_distance = get_distance((last_lat, last_lng), home_location)
                    
                    # Add final home return distance to dead km
                    vehicle.dead_km += final_home_distance
                    
                    # Add final home return to driver pay
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
            # Dead km to pickup location
            pickup_lat, pickup_lng = booking["pickup_lat"], booking["pickup_lon"]
            dead_km_to_pickup = get_distance((current_lat, current_lng), (pickup_lat, pickup_lng))
            total_dead_km += dead_km_to_pickup
            
            # Update current location to drop location
            current_lat, current_lng = booking["drop_lat"], booking["drop_lon"]
        
        # NOTE: Do NOT add final return to home distance here
        # This will be added later when combining with unlocked bookings
        
        return total_dead_km
    
    def _calculate_locked_dead_km_fallback(self, vehicle: VehicleState, locked_bookings_data: List[Dict]) -> float:
        """Fallback calculation for locked dead km without home return"""
        # Simple estimation: 40% of active distance without final home return
        total_active = sum(booking.get("distance_km", 0) for booking in locked_bookings_data)
        return total_active * 0.35  # Slightly less since no final home return
    
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
        
        # Vehicle Summary Table
        print(f"\n🚗 VEHICLE ASSIGNMENTS:")
        print(f"{'Vehicle ID':<10} {'Type':<7} {'Status':<12} {'Bookings':<9} {'Active KM':<10} {'Dead KM':<9} {'Customer Fare':<13} {'Driver Pay':<11} {'Profit':<10} {'Efficiency':<10}")
        print("-"*150)
        
        total_vehicles_with_bookings = 0
        total_vehicles_with_locked_bookings = 0
        total_available_vehicles = 0
        
        for vehicle in self.assigner.vehicles:
            if vehicle.assigned_bookings:
                total_vehicles_with_bookings += 1
                
                # Determine vehicle status - vehicles are NEVER locked, only time-based availability
                # Check if vehicle is available based on time, not booking status
                if hasattr(vehicle, 'last_drop_time_minutes') and vehicle.last_drop_time_minutes:
                    is_available = self.current_sim_time >= vehicle.last_drop_time_minutes
                    status = "AVAILABLE" if is_available else "BUSY"
                else:
                    status = "AVAILABLE"  # Default to available if no time info
                
                # Count locked bookings for statistics (but vehicle status is time-based)
                has_locked_bookings = any(bid in self.locked_bookings for bid in vehicle.assigned_bookings)
                if has_locked_bookings:
                    total_vehicles_with_locked_bookings += 1
                
                # Calculate customer fare for this vehicle
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
        
        # Booking Assignment Details
        print(f"\n📋 BOOKING ASSIGNMENTS:")
        print(f"{'Booking ID':<10} {'Type':<7} {'Distance':<8} {'Pickup Time':<12} {'Vehicle ID':<10} {'Vehicle Type':<12} {'Assignment':<12} {'Lock Status':<11}")
        print("-"*120)
        
        # Create booking to vehicle mapping
        booking_to_vehicle = {}
        for vehicle in self.assigner.vehicles:
            for booking_id in vehicle.assigned_bookings:
                booking_to_vehicle[booking_id] = {
                    'vehicle_id': vehicle.vehicle_id,
                    'vehicle_type': vehicle.vehicle_type
                }
        
        # Sort bookings by pickup time
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
                
                # Determine assignment type and lock status
                if booking_id in self.locked_bookings:
                    lock_status = "LOCKED"
                    locked_count += 1
                else:
                    lock_status = "UNLOCKED"
                    unlocked_count += 1
                
                # Check if this is an instant booking
                is_instant = any(ib.booking_id == booking_id for ib in self.instant_bookings if ib.is_loaded)
                assignment_type = "INSTANT" if is_instant else "SCHEDULED"
                
                assigned_count += 1
                print(f"{booking_id:<10} {booking.get('vehicle_type', 'N/A'):<7} {booking.get('distance_km', 0):<8.1f} {pickup_time:<12} {assignment['vehicle_id']:<10} {assignment['vehicle_type']:<12} {assignment_type:<12} {lock_status:<11}")
            else:
                # For unassigned bookings, check if they're instant bookings (should be UNLOCKED)
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
        
        # Count assigned vs unassigned instant bookings
        loaded_count = len(self.loaded_instant_bookings)
        assigned_instant = sum(1 for b in self.loaded_instant_bookings if self._is_booking_assigned(b["booking_id"]))
        
        print(f"\n{'='*80}")
        print(f"SIMULATION STATUS - {current_datetime.strftime('%H:%M')}")
        print(f"{'='*80}")
        print(f"Loaded Instant Bookings: {loaded_count}")
        print(f"Assigned Instant Bookings: {assigned_instant}")
        print(f"Locked Bookings: {len(self.locked_bookings)}")
        # NOTE: No more locked vehicles - vehicles are never locked, only bookings
        
        # Show next instant bookings to be loaded
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
        
        # Start simulation at 6 AM
        self.current_sim_time = self.start_time
        step_count = 0
        
        while self.current_sim_time < self.end_time:
            step_count += 1
            current_datetime = self._sim_time_to_datetime(self.current_sim_time)
            current_time_hours = self.current_sim_time / 60.0
            
            logger.info(f"\n⏰ SIMULATION STEP {step_count} - {current_datetime.strftime('%H:%M')}")
            
            # 1. Check and load instant bookings
            new_bookings_loaded = self.check_and_load_instant_bookings()
            has_changes = new_bookings_loaded > 0
            
            # Get newly loaded bookings for comparison
            newly_loaded_bookings = []
            if new_bookings_loaded:
                # Get the bookings that were just loaded
                newly_loaded_bookings = [booking for booking in self.loaded_instant_bookings 
                                       if any(ib.booking_id == booking["booking_id"] and ib.is_loaded 
                                            for ib in self.instant_bookings 
                                            if ib.load_time == self.current_sim_time or ib.load_time <= self.current_sim_time)]
            
            # 2. Update locked assignments based on current time
            self.update_locked_assignments()
            
            # 3. Only reassign if there are new bookings since last run
            if self.new_bookings_since_last_run:
                # Print changes before reassignment if this is not the first step
                if step_count > 1 and newly_loaded_bookings:
                    self.print_assignment_changes(newly_loaded_bookings, step_count)
                
                logger.info(f"🔄 Reassigning with {new_bookings_loaded} new instant bookings...")
                self.reassign_unlocked_bookings()
                self.new_bookings_since_last_run = False
                self.last_assignment_time = self.current_sim_time
                has_changes = True
            else:
                logger.info("No new instant bookings loaded - skipping reassignment")
            
            # 4. Print detailed tables if there were changes or every 4 steps (2 hours)
            if has_changes or step_count % 4 == 0:
                # Combine all bookings (scheduled + instant)
                all_current_bookings = []
                
                # Add all original scheduled bookings
                try:
                    with open('data/bookings.json', 'r') as f:
                        scheduled_bookings = json.load(f)
                    all_current_bookings.extend(scheduled_bookings)
                except:
                    pass
                
                # Add loaded instant bookings
                all_current_bookings.extend(self.loaded_instant_bookings)
                
                # Print assignment table with current state
                step_title = f"STEP {step_count} ASSIGNMENTS"
                if has_changes:
                    step_title += f" (WITH {new_bookings_loaded} NEW INSTANT BOOKINGS)"
                else:
                    step_title += " (STATUS UPDATE)"
                
                self.print_assignment_table(step_title, all_current_bookings, current_time_hours)
                self.calculate_and_print_metrics(f"STEP {step_count}", all_current_bookings)
                
                # Store current assignments for next step comparison
                self._store_current_assignments_for_comparison()
            
            # 5. Brief status for steps without major changes
            else:
                logger.info(f"📊 Step {step_count}: {len(self.locked_bookings)} locked bookings, "
                           f"{len(self.loaded_instant_bookings)} total instant bookings loaded")
            
            # 6. Advance simulation time
            self.current_sim_time += self.step_interval
            
            # 6. Sleep for real-time simulation (6 seconds = 30 minutes)
            if self.current_sim_time < self.end_time:
                logger.info(f"💤 Sleeping for {self.real_time_step} seconds (next step: {self._sim_time_to_datetime(self.current_sim_time).strftime('%H:%M')})")
                time.sleep(self.real_time_step)
        
        logger.info("🏁 Simulation completed!")
        self.print_final_results()
    
    def print_final_results(self):
        """Print final simulation results"""
        print(f"\n{'='*100}")
        print("FINAL SIMULATION RESULTS")
        print(f"{'='*100}")
        
        # Calculate metrics
        total_instant_bookings = len(self.instant_bookings)
        loaded_instant_bookings = len(self.loaded_instant_bookings)
        assigned_instant = sum(1 for b in self.loaded_instant_bookings if self._is_booking_assigned(b["booking_id"]))
        
        print(f"\n📈 INSTANT BOOKING SUMMARY:")
        print(f"Total Instant Bookings: {total_instant_bookings}")
        print(f"Loaded Instant Bookings: {loaded_instant_bookings}")
        print(f"Assigned Instant Bookings: {assigned_instant}")
        print(f"Assignment Rate: {(assigned_instant/loaded_instant_bookings*100) if loaded_instant_bookings > 0 else 0:.1f}%")
        
        # Create final comprehensive table with all bookings
        all_final_bookings = []
        
        # Add all original scheduled bookings
        try:
            with open('data/bookings.json', 'r') as f:
                scheduled_bookings = json.load(f)
            all_final_bookings.extend(scheduled_bookings)
        except:
            pass
        
        # Add all loaded instant bookings
        all_final_bookings.extend(self.loaded_instant_bookings)
        
        # Print final comprehensive table
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
    # Create tee output to write to both terminal and log file
    with open('log.txt', 'w') as log_file:
        # Create tee objects for stdout and stderr
        tee_stdout = TeeOutput(sys.stdout, log_file)
        tee_stderr = TeeOutput(sys.stderr, log_file)
        
        # Redirect to tee outputs
        original_stdout = sys.stdout
        original_stderr = sys.stderr
        sys.stdout = tee_stdout
        sys.stderr = tee_stderr

        try:
            # Initialize simulator
            simulator = RealTimeDispatchSimulator()
            
            # Load vehicles data
            with open('data/vehicles.json', 'r') as f:
                vehicles_data = json.load(f)
            
            # Load scheduled bookings data
            with open('data/bookings.json', 'r') as f:
                scheduled_bookings = json.load(f)
            
            # Load instant bookings data
            simulator.load_instant_booking_dataset('data/instant_bookings.json')
            
            # Step 1: Run scheduled assignment first
            print(f"\n{'='*100}")
            print("🚀 REAL-TIME DISPATCH SIMULATION STARTING")
            print(f"{'='*100}")
            
            logger.info("STEP 1: Running scheduled booking assignment...")
            simulator.load_scheduled_assignments(vehicles_data, scheduled_bookings)
            
            print(f"\n📋 SCHEDULED ASSIGNMENT SUMMARY:")
            print(f"  • Assigned scheduled bookings: {len(simulator.scheduled_assignments)}")
            print(f"  • Total instant bookings to simulate: {len(simulator.instant_bookings)}")
            
            # Step 2: Start real-time simulation
            logger.info("\nSTEP 2: Starting real-time simulation...")
            simulator.run_simulation()
            
        except Exception as e:
            logger.error(f"Error in simulation: {e}")
            import traceback
            traceback.print_exc()
        finally:
            # Restore stdout and stderr
            sys.stdout = original_stdout
            sys.stderr = original_stderr

if __name__ == "__main__":
    main()