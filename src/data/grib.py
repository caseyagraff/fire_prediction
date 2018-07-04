import numpy as np
#import gribapi
import pygrib
import json

import logging

from helper.geometry import LatLonBoundingBox

GRIB_ARRAY_TOO_SMALL = -6

class GribMessage(object):
    """
    Interface for getting values from a grib message.
    """
    def __init__(self, gid, lon_offset):
        """
        :param lon_offset: indicates grib file uses 0 to 360 for longitude (instead of -180 to 180)
        """
        self.gid = gid
        self.lon_offset = lon_offset

        self.lon_rot = 0

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
            dlon = np.remainder((dlon+180),360)-180 # Convert from 0 to 360 longitude notation to -180 to 180
            self.lon_rot = -np.argmin(dlon)
            dlon = np.roll(dlon, self.lon_rot) # Rotate so min lon comes first

        values = gribapi.grib_get_values(self.gid)
        values =  np.reshape(values, newshape=(len(dlat),len(dlon)))

        # If needed, roll values along longitude axis to match rolled dlon values
        if self.lon_rot != 0:
            values = np.roll(values, self.lon_rot, axis=1)


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
        """
        :param lon_offset: indicates grib file uses 0 to 360 for longitude (instead of -180 to 180)
        """
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
            try:
                gid = gribapi.grib_new_from_file(self.file_object)
            except gribapi.GribInternalError as e:
                logging.error('GRIB API Error: "%s" on file "%s"' % (str(e), self.file_object.name))
                raise e

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
        for k,v in key_val_dict.items():
            mval = message.get(k)
            if message.get(k) != v:
                return False
        return True


class GribSelection(object):
    """
    Stores key/values for selecting a message from a grib file.

    Supports "backup" selections if primary cannot be found in file.
    """
    def __init__(self, name, dtype=np.float64):
        self.name = name
        self.dtype = dtype
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

            #values, bb = message.get_values(self.bounding_box)

            dlat,dlon = message.latlons()
            dlon = np.remainder((dlon+180),360)-180 # Convert from 0 to 360 longitude notation to -180 to 180
            self.lon_rot = -np.argmin(dlon)
            dlon = np.roll(dlon, self.lon_rot) # Rotate so min lon comes first

            values = message.values.astype(s.dtype)
            values = np.roll(values, self.lon_rot, axis=1) # Rotate values to match rotated lons

            lat_min_ind, lat_max_ind, lon_min_ind, lon_max_ind = self.bounding_box.get_min_max_indexes(dlat, dlon)

            values = values[lat_max_ind:lat_min_ind+1, lon_min_ind:lon_max_ind+1]

            units = message['units']

            #[m.release() for m in selected_messages]

            data[s.name] = {'values': values, 'bounding_box': self.bounding_box, 'units': units}

        return data

    def select_message(self, grib_file, selection):
        """
        Return message from grib_file that matches selection.

        If no match, returns None.
        """
        name, sel = selection.primary
        selected = grib_file_select_handle_exception(grib_file, sel)

        # If primary selection fails, try backup selections
        if not selected:
            for name, sel in selection.backups:
                selected = grib_file_select_handle_exception(grib_file, sel)
                if selected:
                    break

        return name, selected

def grib_file_select_handle_exception(grib_file, sel):
    try:
        return grib_file.select(**sel)
    except ValueError:
        return None


