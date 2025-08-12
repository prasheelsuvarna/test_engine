from datetime import datetime, timedelta
from math import radians, sin, cos, atan2, sqrt
import logging
from Config import Config
from typing import Dict, List, Optional, Tuple
from collections import defaultdict
from H3_utils import latlng_to_h3, get_h3_distance, h3_to_latlng, get_nearest_vehicle_h3
import time
import requests



logger = logging.getLogger(__name__)


def _safe_datetime_parse(time_str: str) -> datetime:
    """Safely parse datetime string with fallback to current system time"""
    try:
        return datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError) as e:
        logger.warning(f"Failed to parse time string '{time_str}': {e}")
        return datetime.now()  # Use current system time as fallback

def _get_time_minutes(time_str: str) -> float:
    """Convert time string to minutes with error handling"""
    time_obj = _safe_datetime_parse(time_str)
    return time_obj.hour * 60 + time_obj.minute

def _get_pickup_time_minutes(pickup_time_str: str) -> float:
    """Get pickup time in minutes with error handling"""
    return _get_time_minutes(pickup_time_str)

def get_distance(point1, point2, factor=None):
    """Calculate approximate road distance using Haversine and a road factor"""
    try:
        lat1, lng1 = point1
        lat2, lng2 = point2
        R = 6371.0  # Earth radius in km

        dlat = radians(lat2 - lat1)
        dlng = radians(lng2 - lng1)
        a = sin(dlat / 2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2)**2
        c = 2 * atan2(sqrt(a), sqrt(1 - a))
        bird_eye_km = R * c

        if factor is None:
            factor = Config.DISTANCE_FACTOR
        
        return round(bird_eye_km * factor, 2)
    except Exception as e:
        logger.warning(f"Distance calculation failed: {e}")
        return 0.0