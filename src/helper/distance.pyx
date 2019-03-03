"""
Distance metrics.
"""

from math import sin, cos, sqrt, atan2, radians

EARTH_RADIUS_KM = 6371.0

def dist_latlon_spherical(p1, p2):
    lat1_rad = radians(p1[0])
    lat2_rad = radians(p2[0])

    diff_lat_rad = lat2_rad - lat1_rad
    diff_lon_rad = radians(p2[1] - p1[1])

    a = sin(diff_lat_rad / 2) ** 2 + cos(lat1_rad) * cos(lat2_rad) * sin(diff_lon_rad / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))

    return EARTH_RADIUS_KM * c
