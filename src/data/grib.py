import numpy as np
import gribapi
import logging

class LatLonBoundingBox(object):
    """
    Stores lat/lon bounding-box info.

    Lat in [-90,90], Lon in [-180, 180].
    """
    def __init__(self, lat_min=-90.0, lat_max=90.0, lon_min=-180.0, lon_max=180.0):
        self.lat_min = float(lat_min)
        self.lat_max = float(lat_max)
        self.lon_min = float(lon_min)
        self.lon_max = float(lon_max)

        if lat_max < lat_min:
            raise ValueError('lat_max (%f) less than lat_min (%f)' % (lat_max, lat_min))
        if lon_max < lon_min:
            raise ValueError('lon_max (%f) less than lon_min (%f)' % (lon_max, lon_min))

    def get(self):
        return self.lat_min, self.lat_max, self.lon_min, self.lon_max

    def get_min_max_indexes(self, dlat, dlon):
        """
        Get indices for dlat and dlon where min/max lon/lat can be found.
        """
        lat_min_ind = self.get_index(dlat, self.lat_min, np.argmin)
        lat_max_ind = self.get_index(dlat, self.lat_max, np.argmax)
        lon_min_ind = self.get_index(dlon, self.lon_min, np.argmin)
        lon_max_ind = self.get_index(dlon, self.lon_max, np.argmax)

        return lat_min_ind, lat_max_ind, lon_min_ind, lon_max_ind

    def get_index(self, distinct, val, default_func):
        try:
            ind = np.where(distinct == val)[0][0]
        except:
            ind = default_func(distinct)

        return int(ind)

    def __str__(self):
        return str({'lat': (self.lat_min, self.lat_max), 'lon': (self.lon_min, self.lon_max)})

    def __eq__(self, other):
        return (self.lat_min, self.lat_max, self.lon_min, self.lon_max) == (other.lat_min, other.lat_max, other.lon_min, other.lon_max)

    def __neq__(self, other):
        return not self.__eq__(other)


def latlonrange(bounding_box, inc_lat=1., inc_lon=1.):
    lat_min, lat_max, lon_min, lon_max = bounding_box.get()

    lat_min -= inc_lat
    lon_max += inc_lon

    lon_min_orig = lon_min

    while lat_min < lat_max:
        while lon_min < lon_max:
            yield lat_max, lon_min
            lon_min += inc_lon

        lat_max -= inc_lat
        lon_min = lon_min_orig


GRIB_ARRAY_TOO_SMALL = -6

class GribMessage(object):
    """
    Interface for getting values from a grib message.
    """
    def __init__(self, gid, lon_offset):
        self.gid = gid
        self.lon_offset = lon_offset

    def get(self, key):
        """
        Get value for key from message.

        First assumes key is non-array and retries as array if get fails.
        """
        try:
            return gribapi.grib_get(self.gid, key)
        except gribapi.GribInternalError as e:
            if e.args[0] == GRIB_ARRAY_TOO_SMALL:
                return gribapi.grib_get_array(self.gid, key)
            else:
                raise e

    def get_values(self, bounding_box=None):
        """
        Get the "value" key from message. Optionally applies a bounding-box to lat/lon of values.
        """
        dlat, dlon = gribapi.grib_get_array(self.gid, 'distinctLatitudes'), gribapi.grib_get_array(self.gid, 'distinctLongitudes')

        if self.lon_offset:
            dlon -= 180.

        values =  np.reshape(gribapi.grib_get_values(self.gid), newshape=(len(dlat),len(dlon)))

        if bounding_box:
            lat_min_ind, lat_max_ind, lon_min_ind, lon_max_ind = bounding_box.get_min_max_indexes(dlat, dlon)

            # Lat is typically ordered from highest to lowest
            return values[lat_max_ind:lat_min_ind+1, lon_min_ind:lon_max_ind+1], LatLonBoundingBox(dlat[lat_min_ind], dlat[lat_max_ind], dlon[lon_min_ind], dlon[lon_max_ind])

    def release(self):
        """
        Release the gribapi gid.
        """
        gribapi.grib_release(self.gid)


class GribFile(object):
    """
    Interface for selecting message(s) from grib files using key/value matching.
    """
    def __init__(self, file_object, multi_field=True, lon_offset=True):
        self.file_object = file_object
        self.lon_offset = lon_offset

        if multi_field:
            gribapi.grib_multi_support_on()
        else:
            gribapi.grib_multi_support_off()


    def select(self, **key_val_dict):
        """
        Return a list of matching GribMessages.
        """
        selected = []
        self.file_object.seek(0)
        while 1:
            gid = gribapi.grib_new_from_file(self.file_object)
            if gid is None: break

            message = GribMessage(gid, self.lon_offset)

            if self.grib_message_is_match(message, key_val_dict):
                selected.append(message)
            else:
                message.release()

        return selected

    def grib_message_is_match(self, message, key_val_dict):
        """
        Check if grib message matches on all key/value pairs.
        """
        for k,v in key_val_dict.iteritems():
            mval = message.get(k)
            if message.get(k) != v:
                return False
        return True


class GribSelection(object):
    """
    Stores key/values for selecting a message from a grib file.

    Supports "backup" selections if primary cannot be found in file.
    """
    def __init__(self):
        self.primary = None
        self.backups = []

    def add_selection(self, **sel):
        if not self.primary:
            self.primary = (sel['name'], sel)
        else:
            self.backups.append((sel['name'], sel))

        return self

    def __str__(self):
        return str((self.primary, self.backups))


class GribSelector(object):
    """
    Extracts data from grib_file that matches list of GribSelection.
    """
    def __init__(self, grib_selections, bounding_box):
        self.selections = grib_selections
        self.bounding_box = bounding_box

    def select(self, grib_file):
        """
        Get data (within bounding-box) from message in grib_file that matches selection criteria.

        If more than one message matches, uses "first" which is not garuanteed to relate to order in file.
        """
        data = {}

        for s in self.selections:
            name, selected_messages = self.select_message(grib_file, s)

            # Silently fail on empty selections
            if not selected_messages:
                logging.debug('No gribmessage matched selection for %s.' % s)
                continue

            if len(selected_messages) > 1:
                logging.debug('More than one gribmessage matched selection for %s. Found %d.' % (s, len(selected_messages)))

            message = selected_messages[0]

            values, bb = message.get_values(self.bounding_box)
            units = message.get('units')

            [m.release() for m in selected_messages]

            data[name] = {'values': values, 'bounding_box': bb, 'units': units}

        return data

    def select_message(self, grib_file, selection):
        """
        Return message from grib_file that matches selection.

        If no match, returns None.
        """
        name, sel = selection.primary
        selected = grib_file.select(**sel)

        # If primary selection fails, try backup selections
        if not selected:
            for name, sel in selection.backups:
                selected = grib_file.select(**sel)
                if selected:
                    break

        return name, selected


def get_default_selections():
    """
    Build a list of GribSelections for the default GFS measurements used.
    """
    temperature = GribSelection().add_selection(name='Temperature', typeOfLevel='surface')
    humidity = GribSelection().add_selection(name='Surface air relative humidity').add_selection(name='2 metre relative humidity').add_selection(name='Relative humidity', level=2)
    wind_u = GribSelection().add_selection(name='10 metre U wind component')
    wind_v = GribSelection().add_selection(name='10 metre V wind component')
    rain = GribSelection().add_selection(name='Total Precipitation')

    sel = [temperature, humidity, wind_u, wind_v, rain]

    return sel

def get_default_bounding_box():
    return LatLonBoundingBox(55, 71, -165, -138)

