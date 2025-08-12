
import sys
import os

# Add current directory to path to import local modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from service import get_distanceapi

# Google Maps API Key
GOOGLE_MAPS_API_KEY = "AIzaSyDol9iNMMCDov7A-Sg6wAngbqhLbY9-MMM"

def test_distance_api():
    """Test the get_distanceapi function with sample coordinates"""
    
    print("Testing get_distanceapi function...")
    print("=" * 50)
    
    # Test coordinates (Bangalore to Mysore approximately)
    origin_lat = 12.9716
    origin_lng = 77.5946
    dest_lat = 12.2958
    dest_lng = 76.6394
    
    print(f"Origin: {origin_lat}, {origin_lng} (Bangalore)")
    print(f"Destination: {dest_lat}, {dest_lng} (Mysore)")
    print()
    
    try:
        # Call the get_distanceapi function
        distance = get_distanceapi(origin_lat, origin_lng, dest_lat, dest_lng, GOOGLE_MAPS_API_KEY)
        
        print(f"✅ SUCCESS!")
        print(f"Distance calculated: {distance:.2f} km")
        print(f"Expected distance: ~150 km (Bangalore to Mysore)")
        
        # Test with shorter distance (within same city)
        print("\n" + "=" * 50)
        print("Testing shorter distance...")
        
        # Coordinates within Bangalore
        short_origin_lat = 12.9716
        short_origin_lng = 77.5946
        short_dest_lat = 12.9352
        short_dest_lng = 77.6245
        
        print(f"Origin: {short_origin_lat}, {short_origin_lng}")
        print(f"Destination: {short_dest_lat}, {short_dest_lng}")
        
        short_distance = get_distanceapi(short_origin_lat, short_origin_lng, 
                                       short_dest_lat, short_dest_lng, GOOGLE_MAPS_API_KEY)
        
        print(f"✅ SUCCESS!")
        print(f"Short distance calculated: {short_distance:.2f} km")
        
    except Exception as e:
        print(f"❌ ERROR: {e}")
        print("Check your API key and internet connection")
        return False
    
    print("\n" + "=" * 50)
    print("All tests completed successfully! 🎉")
    return True

if __name__ == "__main__":
    test_distance_api()
