import json
import logging
import time
import random
import sys
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Set
from dataclasses import dataclass

# Import the home-oriented assignment logic
from home_oriented_main import HomeOrientedBookingAssigner, VehicleState
from Helper_func import _get_pickup_time_minutes, _safe_datetime_parse, get_distance

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

class SimpleRealTimeDispatchSimulator:
    """Simple real-time dispatch simulator - rerun home_oriented_main every 6 seconds"""
    
    def __init__(self):
        self.current_sim_time = 0  # minutes from midnight (start from 6 AM = 360 minutes)
        self.start_time = 6 * 60   # 6 AM in minutes
        self.end_time = 20 * 60    # 10 AM in minutes
        self.step_interval = 30    # 30 minutes per step (simulated as 6 seconds real time)
        
        # Booking management
        self.scheduled_bookings: List[Dict] = []  # Original scheduled bookings
        self.instant_bookings: List[InstantBooking] = []  # All instant bookings with load times
        self.total_bookings: List[Dict] = []  # Combined bookings (scheduled + loaded instant)
        self.locked_booking_ids: Set[int] = set()  # Bookings locked (pickup_time < current_time + 2 hours)
        
        # Vehicle data
        self.vehicles_data: List[Dict] = []
        
        # Step tracking
        self.step_count = 0
        
        # Locked assignments storage (to maintain consistency across steps)
        self.previous_locked_assignments: Dict = {}  # booking_id -> vehicle assignment data
        self.previous_locked_vehicle_states: Dict = {}  # vehicle_id -> vehicle state data
        
    def load_data(self, vehicles_file: str, scheduled_bookings_file: str, instant_bookings_file: str):
        """Load all data files"""
        # Load vehicles
        with open(vehicles_file, 'r') as f:
            self.vehicles_data = json.load(f)
        logger.info(f"Loaded {len(self.vehicles_data)} vehicles")
        
        # Load scheduled bookings
        with open(scheduled_bookings_file, 'r') as f:
            self.scheduled_bookings = json.load(f)
        logger.info(f"Loaded {len(self.scheduled_bookings)} scheduled bookings")
        
        # Load instant bookings and assign random load times
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
                booking_data=booking
            )
            self.instant_bookings.append(instant_booking)
        
        logger.info(f"Loaded {len(self.instant_bookings)} instant bookings with random load times")
        
        # Initialize with scheduled bookings only
        self.total_bookings = self.scheduled_bookings.copy()
        
    def get_newly_loaded_instant_bookings(self) -> List[Dict]:
        """Get instant bookings that should be loaded at current simulation time"""
        newly_loaded = []
        
        for instant_booking in self.instant_bookings:
            if (instant_booking.load_time <= self.current_sim_time and 
                instant_booking.booking_data not in self.total_bookings):
                newly_loaded.append(instant_booking.booking_data)
                
        return newly_loaded
    
    def store_locked_assignments(self, assigner):
        """Store locked booking assignments and actual vehicle states for next step"""
        self.previous_locked_assignments = {}
        self.previous_locked_vehicle_states = {}
        
        # Store assignments for locked bookings
        for vehicle in assigner.vehicles:
            if vehicle.assigned_bookings:
                # Initialize vehicle state storage
                self.previous_locked_vehicle_states[vehicle.vehicle_id] = {
                    'assigned_bookings': [],
                    'active_km': 0.0,
                    'dead_km': 0.0,
                    'current_lat': vehicle.home_lat,  # Will be updated based on locked bookings
                    'current_lng': vehicle.home_lng,  # Will be updated based on locked bookings
                    'available_time': self.start_time,  # Will be updated based on locked bookings
                    'total_driver_pay': 0.0,
                    'route': [],
                    'is_routed': False
                }
                
                # Store assignments for locked bookings only
                locked_bookings_for_vehicle = []
                for booking_id in vehicle.assigned_bookings:
                    if booking_id in self.locked_booking_ids:
                        self.previous_locked_assignments[booking_id] = vehicle.vehicle_id
                        locked_bookings_for_vehicle.append(booking_id)
                        self.previous_locked_vehicle_states[vehicle.vehicle_id]['assigned_bookings'].append(booking_id)
                
                # Calculate actual vehicle state after completing locked bookings
                if locked_bookings_for_vehicle:
                    # Sort locked bookings by pickup time to get correct sequence
                    locked_booking_objects = []
                    for booking_id in locked_bookings_for_vehicle:
                        booking = next((b for b in self.total_bookings if b["booking_id"] == booking_id), None)
                        if booking:
                            locked_booking_objects.append(booking)
                    
                    # Sort by pickup time
                    locked_booking_objects.sort(key=lambda x: _get_pickup_time_minutes(x["pickup_time"]))
                    
                    # Calculate vehicle state after completing all locked bookings
                    current_lat = vehicle.home_lat
                    current_lng = vehicle.home_lng
                    current_time = self.start_time
                    locked_active_km = 0.0
                    locked_dead_km = 0.0
                    locked_driver_pay = 0.0
                    route_points = []
                    
                    # Process each locked booking in sequence
                    for booking in locked_booking_objects:
                        pickup_lat = booking["pickup_lat"]
                        pickup_lng = booking["pickup_lon"]
                        drop_lat = booking["drop_lat"]
                        drop_lng = booking["drop_lon"]
                        
                        # Add route points
                        route_points.extend([(pickup_lat, pickup_lng), (drop_lat, drop_lng)])
                        
                        # Calculate distances
                        travel_to_pickup = get_distance((current_lat, current_lng), (pickup_lat, pickup_lng))
                        active_distance = booking.get("distance_km", 0)
                        
                        # Update accumulated metrics
                        locked_dead_km += travel_to_pickup
                        locked_active_km += active_distance
                        
                        # Calculate timing
                        travel_time = self._calculate_travel_time(travel_to_pickup)
                        active_time = booking.get("travel_time", 30)  # Default 30 min
                        service_time = 30  # 30 minutes service time
                        
                        booking_pickup_time = _get_pickup_time_minutes(booking["pickup_time"])
                        earliest_arrival = current_time + travel_time
                        actual_pickup_start = max(earliest_arrival, booking_pickup_time)
                        
                        # Update current position and time
                        current_lat = drop_lat
                        current_lng = drop_lng
                        current_time = actual_pickup_start + active_time + service_time
                        
                        # Calculate driver pay for this booking
                        vehicle_type = vehicle.vehicle_type
                        active_pay_rate = self._get_active_driver_pay_rate(vehicle_type)
                        dead_pay_rate = self._get_dead_driver_pay_rate(vehicle_type)
                        locked_driver_pay += (active_distance * active_pay_rate) + (travel_to_pickup * dead_pay_rate)
                    
                    # Store the calculated state
                    self.previous_locked_vehicle_states[vehicle.vehicle_id].update({
                        'active_km': locked_active_km,
                        'dead_km': locked_dead_km,
                        'current_lat': current_lat,
                        'current_lng': current_lng,
                        'available_time': current_time,
                        'total_driver_pay': locked_driver_pay,
                        'route': route_points,
                        'is_routed': True if locked_bookings_for_vehicle else False
                    })
        
        logger.info(f"Stored {len(self.previous_locked_assignments)} locked assignments with accurate vehicle states for next step")

    def apply_previous_locked_assignments(self, assigner):
        """Apply previous locked assignments to maintain consistency"""
        if not self.previous_locked_assignments:
            return
        
        logger.info(f"Applying {len(self.previous_locked_assignments)} previous locked assignments")
        
        # First, restore vehicle states for locked bookings
        for vehicle_id, vehicle_state in self.previous_locked_vehicle_states.items():
            vehicle = next((v for v in assigner.vehicles if v.vehicle_id == vehicle_id), None)
            if vehicle and vehicle_state['assigned_bookings']:
                # Assign locked bookings to this vehicle
                for booking_id in vehicle_state['assigned_bookings']:
                    booking = next((b for b in self.total_bookings if b["booking_id"] == booking_id), None)
                    if booking:
                        # Use the assign_booking_to_vehicle method to properly update vehicle state
                        assigner.assign_booking_to_vehicle(booking, vehicle)
                        
                # IMPORTANT: Do NOT mark vehicle as is_routed = True
                # Vehicle should be available for new assignments after its locked bookings are complete
                # The available_time has been updated to reflect when vehicle becomes free
                vehicle.is_routed = False  # Allow vehicle to be considered for new bookings
                
                # Update assignments tracking
                if vehicle_id not in assigner.assignments:
                    assigner.assignments[vehicle_id] = []
                assigner.assignments[vehicle_id] = vehicle_state['assigned_bookings'].copy()
                
                logger.info(f"Vehicle {vehicle_id} locked bookings applied, available from {vehicle.available_time/60:.1f}h for new assignments")
        
        logger.info("Previous locked assignments applied successfully")
    
    def update_locked_bookings(self):
        """Update which bookings are locked (pickup time < current_time + 2 hours)"""
        self.locked_booking_ids.clear()
        lock_threshold = self.current_sim_time + 120  # 2 hours ahead
        
        for booking in self.total_bookings:
            pickup_time = _get_pickup_time_minutes(booking["pickup_time"])
            if pickup_time <= lock_threshold:
                self.locked_booking_ids.add(booking["booking_id"])
        
        logger.info(f"🔒 Locked {len(self.locked_booking_ids)} bookings (pickup within 2 hours)")

    def run_home_oriented_assignment(self) -> Dict:
        """Run home_oriented_main.py logic with improved consistency approach"""
        # Create new assigner instance
        assigner = HomeOrientedBookingAssigner()
        
        # Initialize vehicles
        assigner.initialize_vehicles(self.vehicles_data)
        
        if self.step_count == 0:
            # First step: Assign ALL bookings (scheduled only) without separation
            logger.info(f"STEP 0: Assigning all {len(self.total_bookings)} scheduled bookings")
            assigner.process_bookings_home_oriented(self.total_bookings)
            
            # After assignment, determine and store locked bookings
            self.update_locked_bookings()
            self.store_locked_assignments(assigner)
            
        else:
            # Subsequent steps: Apply previous locked assignments first, then assign remaining
            logger.info(f"STEP {self.step_count}: Applying previous locked assignments")
            self.apply_previous_locked_assignments(assigner)
            
            # Get remaining bookings (not in previous locked assignments)
            remaining_bookings = []
            for booking in self.total_bookings:
                if booking["booking_id"] not in self.previous_locked_assignments:
                    remaining_bookings.append(booking)
            
            # Assign remaining bookings
            if remaining_bookings:
                logger.info(f"Assigning {len(remaining_bookings)} remaining bookings")
                assigner.process_bookings_home_oriented(remaining_bookings)
            
            # Update locked bookings for current step and store for next step
            self.update_locked_bookings()
            self.store_locked_assignments(assigner)
        
        # Calculate totals for reporting
        total_assigned = sum(len(vehicle.assigned_bookings) for vehicle in assigner.vehicles)
        total_unassigned = len(assigner.unassigned_bookings)
        locked_count = len(self.locked_booking_ids)
        unlocked_count = len(self.total_bookings) - locked_count
        
        return {
            'assigner': assigner,
            'total_assigned': total_assigned,
            'total_unassigned': total_unassigned,
            'locked_count': locked_count,
            'unlocked_count': unlocked_count
        }
    
    def print_step_summary(self, step_num: int, newly_loaded: List[Dict], assignment_result: Dict):
        """Print detailed summary of what happened in this step"""
        print(f"\n" + "="*100)
        print(f"STEP {step_num} SUMMARY (Simulation Time: {self.current_sim_time/60:.1f} hours)")
        print("="*100)
        
        # Newly loaded instant bookings
        if newly_loaded:
            print(f"\n📦 NEWLY LOADED INSTANT BOOKINGS ({len(newly_loaded)}):")
            for booking in newly_loaded:
                pickup_time = _get_pickup_time_minutes(booking["pickup_time"])
                pickup_hours = pickup_time / 60
                print(f"  • Booking {booking['booking_id']}: {booking['vehicle_type']} - Pickup at {pickup_hours:.1f}h")
        else:
            print(f"\n📦 No new instant bookings loaded this step")
        
        # Assignment summary
        print(f"\n🚗 ASSIGNMENT SUMMARY:")
        print(f"  • Total bookings: {len(self.total_bookings)}")
        print(f"  • Locked bookings: {assignment_result['locked_count']}")
        print(f"  • Unlocked bookings: {assignment_result['unlocked_count']}")
        print(f"  • Successfully assigned: {assignment_result['total_assigned']}")
        print(f"  • Unassigned: {assignment_result['total_unassigned']}")
        
        # Calculate metrics using HomeOrientedBookingAssigner
        assigner = assignment_result['assigner']
        metrics = assigner.calculate_final_metrics(self.total_bookings)
        
        # Detailed vehicle assignments table with financial metrics
        print(f"\n🚗 VEHICLE ASSIGNMENTS:")
        print(f"{'Vehicle ID':<10} {'Type':<8} {'Status':<12} {'Bookings':<8} {'Active KM':<10} {'Dead KM':<10} {'Customer Fare':<15} {'Driver Pay':<12} {'Profit':<12} {'Efficiency':<10}")
        print("-" * 120)
        
        vehicles_with_bookings = 0
        
        for vehicle in assigner.vehicles:
            num_bookings = len(vehicle.assigned_bookings)
            if num_bookings > 0:
                vehicles_with_bookings += 1
            
            # Calculate per-vehicle financial metrics
            vehicle_customer_fare = 0
            for booking_id in vehicle.assigned_bookings:
                booking = next((b for b in self.total_bookings if b["booking_id"] == booking_id), None)
                if booking:
                    active_distance = booking.get("distance_km", 0)
                    vehicle_type = vehicle.vehicle_type
                    dead_km_factor = assigner.dead_km_percentage.get(vehicle_type, 0.40)
                    customer_price = assigner.customer_price_per_km.get(vehicle_type, 20)
                    booking_fare = (active_distance + active_distance * dead_km_factor) * customer_price
                    vehicle_customer_fare += booking_fare
            
            vehicle_profit = vehicle_customer_fare - vehicle.total_driver_pay
            
            efficiency = (vehicle.active_km / (vehicle.active_km + vehicle.dead_km) * 100) if (vehicle.active_km + vehicle.dead_km) > 0 else 0
            
            status = "ASSIGNED" if num_bookings > 0 else "AVAILABLE"
            print(f"{vehicle.vehicle_id:<10} {vehicle.vehicle_type:<8} {status:<12} {num_bookings:<8} {vehicle.active_km:<10.2f} {vehicle.dead_km:<10.2f} ₹{vehicle_customer_fare:<14.2f} ₹{vehicle.total_driver_pay:<11.2f} ₹{vehicle_profit:<11.2f} {efficiency:<9.1f}%")
        
        # Vehicle status summary
        available_vehicles = len([v for v in assigner.vehicles if len(v.assigned_bookings) == 0])
        print(f"\n📊 Vehicle Status Summary:")
        print(f"  • Vehicles with bookings: {vehicles_with_bookings}")
        print(f"  • Available vehicles: {available_vehicles}")
        
        # Booking assignments table
        print(f"\n📋 BOOKING ASSIGNMENTS:")
        print(f"{'Booking ID':<10} {'Type':<8} {'Distance':<8} {'Pickup Time':<19} {'Vehicle ID':<10} {'Vehicle Type':<12} {'Assignment':<12} {'Lock Status':<11}")
        print("-" * 120)
        
        # Collect all bookings with their assignment info
        booking_display_list = []
        
        # Show assigned bookings
        for vehicle in assigner.vehicles:
            for booking_id in vehicle.assigned_bookings:
                # Find booking data
                booking_data = None
                for booking in self.total_bookings:
                    if booking["booking_id"] == booking_id:
                        booking_data = booking
                        break
                
                if booking_data:
                    pickup_time = _safe_datetime_parse(booking_data["pickup_time"])
                    pickup_str = pickup_time.strftime("%Y-%m-%d %H:%M:%S") if pickup_time else "Unknown"
                    status = "LOCKED" if booking_id in self.locked_booking_ids else "UNLOCKED"
                    assignment_type = "INSTANT" if booking_data in [b.booking_data for b in self.instant_bookings] else "SCHEDULED"
                    distance = booking_data.get("distance_km", 0)
                    
                    booking_display_list.append({
                        'booking_id': booking_id,
                        'vehicle_type': booking_data['vehicle_type'],
                        'distance': distance,
                        'pickup_str': pickup_str,
                        'pickup_time': pickup_time,
                        'vehicle_id': vehicle.vehicle_id,
                        'vehicle_type_assigned': vehicle.vehicle_type,
                        'assignment_type': assignment_type,
                        'status': status
                    })
        
        # Show unassigned bookings
        for booking in assigner.unassigned_bookings:
            pickup_time = _safe_datetime_parse(booking["pickup_time"])
            pickup_str = pickup_time.strftime("%Y-%m-%d %H:%M:%S") if pickup_time else "Unknown"
            status = "LOCKED" if booking["booking_id"] in self.locked_booking_ids else "UNLOCKED"
            assignment_type = "INSTANT" if booking in [b.booking_data for b in self.instant_bookings] else "SCHEDULED"
            distance = booking.get("distance_km", 0)
            
            booking_display_list.append({
                'booking_id': booking['booking_id'],
                'vehicle_type': booking['vehicle_type'],
                'distance': distance,
                'pickup_str': pickup_str,
                'pickup_time': pickup_time,
                'vehicle_id': 'UNASSIGNED',
                'vehicle_type_assigned': 'N/A',
                'assignment_type': assignment_type,
                'status': 'N/A'
            })
        
        # Sort by booking_id
        booking_display_list.sort(key=lambda x: x['booking_id'])
        
        # Display sorted bookings
        for booking_info in booking_display_list:
            print(f"{booking_info['booking_id']:<10} {booking_info['vehicle_type']:<8} {booking_info['distance']:<8.1f} {booking_info['pickup_str']:<19} {booking_info['vehicle_id']:<10} {booking_info['vehicle_type_assigned']:<12} {booking_info['assignment_type']:<12} {booking_info['status']:<11}")
        
        # Booking status summary
        assigned_bookings = assignment_result['total_assigned']
        unassigned_bookings = assignment_result['total_unassigned']
        locked_bookings = len([b for b in self.total_bookings if b["booking_id"] in self.locked_booking_ids])
        unlocked_bookings = len(self.total_bookings) - locked_bookings
        
        print(f"\n📈 Booking Status Summary:")
        print(f"  • Total bookings: {len(self.total_bookings)}")
        print(f"  • Assigned: {assigned_bookings}")
        print(f"  • Locked: {locked_bookings}")
        print(f"  • Unlocked: {unlocked_bookings}")
        print(f"  • Unassigned: {unassigned_bookings}")
        
        # Financial summary using HomeOrientedBookingAssigner metrics
        print(f"\n💰 STEP {step_num} FINANCIAL METRICS:")
        print(f"Total Customer Fare (Original): ₹{metrics['total_customer_fare']:.2f}")
        print(f"Total Driver Pay (Original): ₹{metrics['total_driver_pay']:.2f}")
        print(f"Total Profit (Original): ₹{metrics['total_profit']:.2f}")
        print(f"Overall Efficiency: {metrics['overall_efficiency']:.1f}%")
        print(f"Total Active KM: {metrics['total_active_km']:.2f}")
        print(f"Total Dead KM: {metrics['total_dead_km']:.2f}")
        print(f"Assigned Bookings: {assigned_bookings}")
        print(f"Unassigned Bookings: {unassigned_bookings}")
    
    def print_detailed_vehicle_routes(self, assignment_result: Dict):
        """Print detailed vehicle route analysis using HomeOrientedBookingAssigner"""
        assigner = assignment_result['assigner']
        assigner.print_detailed_vehicle_routes(self.total_bookings)
    
    def print_final_summary(self, assignment_result: Dict):
        """Print final comprehensive summary of the simulation"""
        assigner = assignment_result['assigner']
        metrics = assigner.calculate_final_metrics(self.total_bookings)
        
        print(f"\n" + "="*100)
        print("FINAL SIMULATION SUMMARY")
        print("="*100)
        print(f"Total Customer Fare (Original): ₹{metrics['total_customer_fare']:.2f}")
        print(f"Total Driver Pay (Original): ₹{metrics['total_driver_pay']:.2f}")
        print(f"Total Profit (Original): ₹{metrics['total_profit']:.2f}")
        print(f"Overall Efficiency: {metrics['overall_efficiency']:.1f}%")
        print(f"Total Active KM: {metrics['total_active_km']:.2f}")
        print(f"Total Dead KM: {metrics['total_dead_km']:.2f}")
        print(f"Assigned Bookings: {metrics['assigned_bookings']}")
        print(f"Unassigned Bookings: {metrics['unassigned_bookings']}")
        
        # Call detailed tables from HomeOrientedBookingAssigner
        assigner.print_detailed_tables(self.total_bookings)
        assigner.print_booking_assignment_details(self.total_bookings)
    
    def sim_time_to_datetime(self, sim_minutes: int) -> datetime:
        """Convert simulation minutes to datetime for display"""
        base_date = datetime(2025, 8, 6)  # Base date for simulation
        return base_date + timedelta(minutes=sim_minutes)
    
    def _calculate_travel_time(self, distance_km: float) -> float:
        """Calculate travel time in minutes"""
        return (distance_km / 30) * 60  # Assuming 30 km/h average speed
    
    def _get_active_driver_pay_rate(self, vehicle_type: str) -> float:
        """Get active driver pay rate for vehicle type"""
        rates = {
            "class1": 16, "class2": 20, "class3": 22, "class4": 26, "class5": 32,
            "class6": 40, "class7": 50, "class8": 60, "class9": 70
        }
        return rates.get(vehicle_type, 20)
    
    def _get_dead_driver_pay_rate(self, vehicle_type: str) -> float:
        """Get dead driver pay rate for vehicle type"""
        rates = {
            "class1": 10, "class2": 15, "class3": 18, "class4": 22, "class5": 28,
            "class6": 32, "class7": 40, "class8": 50, "class9": 60
        }
        return rates.get(vehicle_type, 15)
    
    def run_simulation(self):
        """Run the complete real-time simulation with improved consistency"""
        print("="*100)
        print("🚀 SIMPLE REAL-TIME DISPATCH SIMULATION STARTING")
        print("="*100)
        
        # Initialize simulation time to 6 AM
        self.current_sim_time = self.start_time
        self.step_count = 0
        
        # Step 0: Initial assignment with scheduled bookings only (no separation)
        print(f"\n⏰ STEP 0: INITIAL SCHEDULED ASSIGNMENTS (NO SEPARATION)")
        print(f"Simulation Time: {self.current_sim_time/60:.1f} hours (6:00 AM)")
        
        initial_result = self.run_home_oriented_assignment()
        self.print_step_summary(0, [], initial_result)
        
        # Main simulation loop - every 30 minutes (6 seconds real time)
        while self.current_sim_time < self.end_time:
    # Countdown before advancing simulation time
            # Calculate next simulation time
            next_sim_minutes = self.current_sim_time + self.step_interval
            next_sim_dt = self.sim_time_to_datetime(next_sim_minutes)
            next_sim_str = next_sim_dt.strftime('%H:%M')
            print(f"\n⏳ Waiting for next step ({next_sim_str})...")
            for i in range(6, 0, -1):
                print(f"Next step will be starting in {i}...")
                time.sleep(1)  # 1 second per countdown tick

            # Advance simulation time
            self.current_sim_time += self.step_interval
            self.step_count += 1

            current_time_hours = self.current_sim_time / 60
            if current_time_hours >= 19:  # Stop at 7 PM
                break

            print(f"\n⏰ STEP {self.step_count}: Time {current_time_hours:.1f}h")
    # ...existing code...
            # Get newly loaded instant bookings
            newly_loaded = self.get_newly_loaded_instant_bookings()
            
            # Add newly loaded bookings to total
            for booking in newly_loaded:
                if booking not in self.total_bookings:
                    self.total_bookings.append(booking)
            
            if newly_loaded:
                logger.info(f"📦 Added {len(newly_loaded)} new instant bookings")
            else:
                logger.info(f"📦 No new instant bookings loaded")
            
            # Run assignment with improved approach:
            # 1. Apply previous locked assignments
            # 2. Assign remaining bookings 
            # 3. Update and store locked bookings for next step
            assignment_result = self.run_home_oriented_assignment()
            
            # Print step summary
            self.print_step_summary(self.step_count, newly_loaded, assignment_result)
            
            # Print detailed vehicle routes every few steps or on final step
            # if self.step_count % 5 == 0 or current_time_hours >= 9.5:
            # self.print_detailed_vehicle_routes(assignment_result)
            
        # Final comprehensive summary
        final_assignment_result = self.run_home_oriented_assignment()
        self.print_final_summary(final_assignment_result)
        
        
        # Final summary
        print(f"\n" + "="*100)
        print("🎯 SIMULATION COMPLETED")
        print("="*100)
        print(f"Total simulation time: {(self.current_sim_time - self.start_time)/60:.1f} hours")
        print(f"Total steps: {self.step_count}")
        print(f"Total bookings processed: {len(self.total_bookings)}")
        print(f"Scheduled bookings: {len(self.scheduled_bookings)}")
        print(f"Instant bookings loaded: {len([b for b in self.total_bookings if b not in self.scheduled_bookings])}")
        
        # Print final consistency check
        print(f"\n🔍 CONSISTENCY CHECK:")
        print(f"Previous locked assignments: {len(self.previous_locked_assignments)}")
        print(f"Final locked bookings: {len(self.locked_booking_ids)}")
        locked_preserved = all(bid in self.locked_booking_ids for bid in self.previous_locked_assignments.keys())
        print(f"Locked assignments preserved: {'✅ YES' if locked_preserved else '❌ NO'}")

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
    """Main function to run the simple real-time dispatch simulation"""
    with open('log.txt', 'w') as log_file:
        tee_stdout = TeeOutput(sys.stdout, log_file)
        tee_stderr = TeeOutput(sys.stderr, log_file)
        
        original_stdout = sys.stdout
        original_stderr = sys.stderr
        sys.stdout = tee_stdout
        sys.stderr = tee_stderr
        
        try:
            # File paths
            vehicles_file = "data/vehicles.json"
            scheduled_bookings_file = "data/bookings.json"
            instant_bookings_file = "data/instant_bookings.json"
            
            # Create and run simulator
            simulator = SimpleRealTimeDispatchSimulator()
            
            # Load data
            simulator.load_data(vehicles_file, scheduled_bookings_file, instant_bookings_file)
            
            # Run simulation
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