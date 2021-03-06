"""
Setup data structures used for model training.
"""
import datetime as dt
from collections import defaultdict

import numpy as np
import pandas as pd
import pytz
import xarray as xr

from src.helper import date_util as du
from src.helper import multidata_wrapper as mdw
from src.visualization import mapping as vmap

fill_n_days = 5


def fill_missing_value(data, date_ind):
    """
    Try to replace with closest prev day in range [1, fill_n_days].

    If no non-nan value is found, replaces with mean of all values at the given lat/lon.
    """
    for day_offset in range(1, fill_n_days + 1):
        new_date_ind = date_ind - day_offset

        if new_date_ind < 0:
            break

        val = data[:, :, new_date_ind]

        if not np.any(np.isnan(val)):
            return val

    return np.nanmean(data[:, :, :], axis=2)


def get_date_index(weather_dates, target_datetime):
    date_ind = np.searchsorted(weather_dates, target_datetime, side='left')

    if date_ind >= len(weather_dates):
        return None

    # Check if left or right element is closer
    if date_ind != 0:
        date_ind_left, date_ind_curr = date_ind - 1, date_ind

        dist_left = abs((weather_dates[date_ind_left] - target_datetime).total_seconds())
        dist_curr = abs((weather_dates[date_ind_curr] - target_datetime).total_seconds())

        if dist_left < dist_curr:
            date_ind = date_ind_left

    return date_ind


def get_weather_variables(values, weather_data, target_datetime, covariates):
    # Get date index
    date_ind = get_date_index(weather_data.dates, target_datetime)

    for key in covariates:
        data = weather_data[key].values
        val = data[:, :, date_ind]

        if np.any(np.isnan(val)):
            val = fill_missing_value(data, date_ind)

        values[key].append(val)


def create_cell_encoding(group_size, shape):
    data = []

    num_rows = int(np.ceil(shape[0] / (1. * group_size)))
    num_cols = int(np.ceil(shape[1] / (1. * group_size)))

    enc_all = np.zeros((num_rows, num_cols, shape[0], shape[1], shape[2]))

    # fill enc for each cell
    for i in range(shape[0]):
        for j in range(shape[1]):
            enc_all[int(i / group_size), int(j / group_size), i, j, :] = 1

    # split into columns
    for i in range(num_rows):
        for j in range(num_cols):
            enc = enc_all[i, j]
            data.append(('cell_%d_%d' % (i, j), enc))
    return data


def build_y(y, t_k_arr, years=None):
    y_dict = {}
    for t_k in t_k_arr:
        # Shift y by t_k days
        shape = np.shape(y.values)[:2] + (t_k,)
        y_new = np.concatenate((y.values, np.zeros(shape)), axis=2)
        y_new = y_new[:, :, t_k:]

        # Convert y_new to an xarray dataset
        y_ds = xr.DataArray(y_new, coords={'time': pd.to_datetime(y.dates)}, dims=('y', 'x', 'time'))

        if years:
            y_ds = y_ds.sel(time=np.isin(y_ds.time.dt.year, years))

        y_dict[t_k] = y_ds

    return y_dict


def build_y_nw(y, dates, t_k_arr, years=None):
    y_dict = {}
    for t_k in t_k_arr:
        # Shift y by t_k days
        shape = np.shape(y)[:2] + (t_k,)
        y_new = np.concatenate((y, np.zeros(shape)), axis=2)
        y_new = y_new[:, :, t_k:]

        # Convert y_new to an xarray dataset
        y_ds = xr.DataArray(y_new, coords={'time': pd.to_datetime(dates)}, dims=('y', 'x', 'time'))

        if years:
            y_ds = y_ds.sel(time=np.isin(y_ds.time.dt.year, years))

        y_dict[t_k] = y_ds

    return y_dict


def build_x_active(X, t_k_arr, years=None):
    X_dict = {}
    for t_k in t_k_arr:
        dates = pd.to_datetime(np.array(X[t_k]['date_local']))
        X_ds = xr.Dataset({'num_det': ('time', X[t_k]['num_det']),
                           'temperature': ('time', X[t_k]['temperature']),
                           'humidity': ('time', X[t_k]['humidity']),
                           'wind': ('time', X[t_k]['wind']),
                           'rain': ('time', X[t_k]['rain']),
                           'lat_centroid': ('time', X[t_k]['lat_centroid']),
                           'lon_centroid': ('time', X[t_k]['lon_centroid']),
                           'num_det_target': ('time', X[t_k]['num_det_target'])},
                          {'time': dates})

        if years:
            X_ds = X_ds.sel(time=np.isin(X_ds.time.dt.year, years))

        X_dict[t_k] = mdw.MultidataWrapper((X_ds, None))
    return X_dict


def shift_in_time(arr, dates, num_days, fill_func):
    if num_days == 0:
        return arr
    years_all = np.array(list(map(lambda x: x.year, dates)))
    years = list(set(years_all))
    years.sort()

    y_new = fill_func(arr.shape)

    for year in years:
        inds = np.where(years_all == year)
        year_start_ind, year_end_ind = inds[0][0], inds[0][-1]
        if num_days > 0:
            y_new[:, :, year_start_ind:(year_end_ind + 1 - num_days)] = \
                arr[:, :, year_start_ind + num_days:year_end_ind + 1]
        elif num_days < 0:
            y_new[:, :, year_start_ind - num_days:year_end_ind + 1] = \
                arr[:, :, year_start_ind:(year_end_ind + 1 + num_days)]
        else:
            raise ValueError('num_days can not be zero')

    return y_new


def build_x_grid(X, y, land_cover, t_k_arr, num_auto_memory=0, num_weather_mem=0, actives=(2, 5, 10),
                 exponential_decay=(.25, .5, .75), exponential_decay_int=(5, 10, 15), years=None):
    X_dict = {}
    for t_k in t_k_arr:
        # Shift y by t_k days
        # shape = np.shape(y.values)[:2]+(t_k,)
        # y_new_ = np.concatenate((y.values, np.zeros(shape)), axis=2)
        # y_new_ = y_new_[:,:,t_k:]

        y_new = shift_in_time(y.values, y.dates, t_k, np.zeros)

        # Build grid of weather
        values = defaultdict(list)
        for date in y.dates:
            time = 14
            date += du.INC_ONE_DAY * t_k  # For row t, store weather(t+k)
            tzinfo = du.TrulyLocalTzInfo(153, du.round_to_nearest_quarter_hour)
            target_datetime = dt.datetime.combine(date, dt.time(time, 0, 0, tzinfo=tzinfo))

            get_weather_variables(values, X, target_datetime, ['temperature', 'humidity', 'wind', 'rain'])

        for k, v in values.items():
            values[k] = list(np.rollaxis(np.array(v), 0, 3))

        dates = pd.to_datetime(np.array(y.dates))
        X_ds = xr.Dataset({'num_det': (('y', 'x', 'time'), y.values),
                           'num_det_target': (('y', 'x', 'time'), y_new),
                           'active': (('y', 'x', 'time'), y.values != 0),
                           'temperature': (('y', 'x', 'time'), values['temperature']),
                           'humidity': (('y', 'x', 'time'), values['humidity']),
                           'wind': (('y', 'x', 'time'), values['wind']),
                           'rain': (('y', 'x', 'time'), values['rain'])},
                          {'time': dates})

        # Add land cover
        land_cover = np.array(land_cover, dtype=np.int8)
        num_land_cover_types = land_cover.shape[2]
        for i in range(num_land_cover_types):
            land_cover_t = land_cover[:, :, i, None].repeat(len(dates), axis=2)
            name = 'land_cover_%d' % i
            X_ds.update({name: (('y', 'x', 'time'), land_cover_t)})

        # Add autoregressive memory
        for i in range(1, num_auto_memory + 1):
            # shape = np.shape(y.values)[:2]+(i,)
            # y_mem = np.concatenate((np.zeros(shape), y.values), axis=2)
            # y_mem = y_mem[:,:,:-i]
            y_mem = shift_in_time(y.values, y.dates, -i, np.zeros)

            # Add one and apply log
            # y_mem = np.log(y_mem+1)

            name = 'num_det_' + str(i)
            X_ds.update({name: (('y', 'x', 'time'), y_mem)})

        # Convert values to np.ndarray
        values = {k: np.array(v) for k, v in values.items()}

        # Add weather memory (rain)
        rain_mean = np.mean(values['rain'])
        for i in range(1, num_weather_mem + 1):
            # shape = np.shape(y.values)[:2]+(i,)
            # x_mem = np.concatenate((np.zeros(shape)+rain_mean, values['rain']), axis=2)
            # x_mem = x_mem[:,:,:-i]

            x_mem = shift_in_time(values['rain'], y.dates, -i, lambda x: np.zeros(x) + rain_mean)

            name = 'rain_' + str(i)
            X_ds.update({name: (('y', 'x', 'time'), x_mem)})

        # Add weather memory (temp)
        temp_mean = np.mean(values['temperature'])
        for i in range(1, num_weather_mem + 1):
            # shape = np.shape(y.values)[:2]+(i,)
            # x_mem = np.concatenate((np.zeros(shape)+temp_mean, values['temperature']), axis=2)
            # x_mem = x_mem[:,:,:-i]

            x_mem = shift_in_time(values['temperature'], y.dates, -i, lambda x: np.zeros(x) + temp_mean)

            name = 'temperature_' + str(i)
            X_ds.update({name: (('y', 'x', 'time'), x_mem)})

        # Add alternative active definitions
        # TODO: Fix shifting
        for act in actives:
            shape = np.shape(y.values)[:2] + (act,)
            y_new = np.concatenate((np.zeros(shape), y.values), axis=2)
            is_act = y.values != 0
            for i in range(1, act):
                is_act = np.logical_or(is_act, y_new[:, :, (act - i):-i])

            name = 'active_' + str(act)
            X_ds.update({name: (('y', 'x', 'time'), is_act)})

        # Compute exponential decay
        for num, decay in enumerate(exponential_decay):
            for interval in exponential_decay_int:
                values = np.power(1 - decay, range(1, interval))
                for cov in ['num_det', 'rain', 'temperature']:
                    new = np.zeros(np.shape(y.values))
                    for i in range(1, interval):
                        new += X_ds[cov + '_' + str(i)] * values[i - 1]

                    name = '{0}_expon_{1}_{2}'.format(cov, str(num), str(interval))
                    X_ds.update({name: (('y', 'x', 'time'), new)})

        if years:
            X_ds = X_ds.sel(time=np.isin(X_ds.time.dt.year, years))

        X_dict[t_k] = mdw.MultidataWrapper((X_ds, X_ds))

        print('T_k=%d' % t_k, end='')
    print()
    return X_dict


def get_weather_variables_nw(values, weather_data, weather_dates, target_datetime, covariates):
    # Get date index
    date_ind = get_date_index(weather_dates, target_datetime)
    # date_ind = weather_data.time == np.datetime64(target_datetime)
    # print(np.datetime64(target_datetime))
    # print(np.any(date_ind))
    # values = []
    for key in covariates:
        data = weather_data[key]
        if date_ind:
            val = np.array(data.isel(time=date_ind))
        else:
            val = np.full_like(data.isel(time=0), fill_value=np.nan)

        # values.append(val)
        values[key].append(val)


# def build_x_grid_nw(X, y, land_cover, t_k_arr, num_auto_memory=0, num_weather_mem=0, actives=[2,5,10],
# exponential_decay=[.25, .5, .75], expon_decay_int=[5,10,15], years=None):
def build_x_grid_nw(X, y, land_cover, t_k_arr, years=None):
    X_dict = {}

    # Load data from disk
    X.load()
    y.load()

    y_values = y['detections'].values
    y_dates = np.array(list(map(lambda x: pd.Timestamp(x).to_pydatetime().date(), y.time.values)))

    weather_dates = np.array(list(map(lambda x: pd.Timestamp(x, tz=pytz.UTC).to_pydatetime(), X.time.values)))

    for t_k in t_k_arr:
        # Shift y by t_k days
        y_new = shift_in_time(y_values, y_dates, t_k, np.zeros)

        # Build grid of weather
        values = defaultdict(list)
        for date in y_dates:
            time = 14
            date += du.INC_ONE_DAY * t_k  # For row t, store weather(t+k)
            tzinfo = du.TrulyLocalTzInfo(153, du.round_to_nearest_quarter_hour)
            target_datetime = dt.datetime.combine(date, dt.time(time, 0, 0, tzinfo=tzinfo))

            get_weather_variables_nw(values, X, weather_dates, target_datetime,
                                     ['temperature', 'humidity', 'wind_speed', 'precipitation_24hr', 'vpd', 'in_filled',
                                      'interpolated'])

        for k, v in values.items():
            values[k] = list(np.rollaxis(np.array(v), 0, 3))
            # Rename
        values['rain'] = values['precipitation_24hr']
        values['wind'] = values['wind_speed']

        dates = pd.to_datetime(np.array(y_dates))
        X_ds = xr.Dataset({'num_det': (('y', 'x', 'time'), y_values),
                           'num_det_target': (('y', 'x', 'time'), y_new),
                           'active': (('y', 'x', 'time'), y_values != 0),
                           'temperature': (('y', 'x', 'time'), values['temperature']),
                           'humidity': (('y', 'x', 'time'), values['humidity']),
                           'wind': (('y', 'x', 'time'), values['wind']),
                           'rain': (('y', 'x', 'time'), values['rain']),
                           'in_filled': (('y', 'x', 'time'), values['in_filled']),
                           'interpolated': (('y', 'x', 'time'), values['interpolated']),
                           'vpd': (('y', 'x', 'time'), values['vpd'])},
                          {'lat': (['y'], X.lat.values), 'lon': (['x'], X.lon.values), 'time': dates})

        # Add land cover
        if land_cover:
            land_cover = np.array(land_cover, dtype=np.int8)
            num_land_cover_types = land_cover.shape[2]
            for i in range(num_land_cover_types):
                land_cover_t = land_cover[:, :, i, None].repeat(len(dates), axis=2)
                name = 'land_cover_%d' % i
                X_ds.update({name: (('y', 'x', 'time'), land_cover_t)})

        """
        # Add autoregressive memory
        for i in range(1,num_auto_memory+1):
            #shape = np.shape(y.values)[:2]+(i,)
            #y_mem = np.concatenate((np.zeros(shape), y.values), axis=2)
            #y_mem = y_mem[:,:,:-i]
            y_mem = shift_in_time(y_values, y_dates, -i, np.zeros)

            # Add one and apply log
            #y_mem = np.log(y_mem+1)

            name = 'num_det_' + str(i)
            X_ds.update({name: (('y','x','time'), y_mem)})

        # Add weather memory (rain)
        rain_mean = np.mean(values['rain'])
        for i in range(1,num_weather_mem+1):
            #shape = np.shape(y.values)[:2]+(i,)
            #x_mem = np.concatenate((np.zeros(shape)+rain_mean, values['rain']), axis=2)
            #x_mem = x_mem[:,:,:-i]

            x_mem = shift_in_time(values['rain'], y_dates, -i, lambda x: np.zeros(x)+rain_mean)

            name = 'rain_' + str(i)
            X_ds.update({name: (('y','x','time'), x_mem)})

        # Add weather memory (temp)
        temp_mean = np.mean(values['temperature'])
        for i in range(1,num_weather_mem+1):
            #shape = np.shape(y.values)[:2]+(i,)
            #x_mem = np.concatenate((np.zeros(shape)+temp_mean, values['temperature']), axis=2)
            #x_mem = x_mem[:,:,:-i]

            x_mem = shift_in_time(values['temperature'], y_dates, -i, lambda x: np.zeros(x)+temp_mean)

            name = 'temperature_' + str(i)
            X_ds.update({name: (('y','x','time'), x_mem)})

        # Add alternative active definitions
        # TODO: Fix shifting
        for act in actives:
            shape = np.shape(y_values)[:2]+(act,)
            y_new = np.concatenate((np.zeros(shape), y_values), axis=2)
            is_act = y_values != 0
            for i in range(1,act):
                is_act = np.logical_or(is_act, y_new[:,:,(act-i):-i])

            name = 'active_' + str(act)
            X_ds.update({name: (('y','x','time'), is_act)})

        # Compute exponential decay
        for num, decay in enumerate(expon_decay):
            for interval in expon_decay_int:
                values = np.power(1-decay, range(1,interval))
                for cov in ['num_det', 'rain', 'temperature']:
                    new = np.zeros(np.shape(y_values))
                    for i in range(1, interval):
                        new += X_ds[cov + '_' + str(i)] * values[i-1]

                    name = cov + '_expon_' + str(num) + '_' + str(interval)
                    X_ds.update({name: (('y','x','time'), new)})
        """

        if years:
            X_ds = X_ds.sel(time=np.isin(X_ds.time.dt.year, years))

        # X_dict[t_k] = mdw.MultidataWrapper((X_ds,X_ds))
        X_dict[t_k] = X_ds

        print('T_k=%d' % t_k, end='')
    print()
    return X_dict


def add_region_biases(X_grid_dict, region_size):
    # Add encoding pairs (for cell/region biases)
    encoding_pairs = create_cell_encoding(region_size, X_grid_dict[1][0].num_det.shape)
    for k, v in X_grid_dict.items():
        for name, enc in encoding_pairs:
            v[0].update({name: (('y', 'x', 'time'), enc)})


def add_water_mask(X_grid_dict, bounding_box):
    mp = vmap.make_map(bounding_box)

    water_mask = np.zeros((33, 55))
    for i, lat in enumerate(np.arange(55, 71 + .5, .5)):
        for j, lon in enumerate(np.arange(-165, -138 + .5, .5)):
            water_mask[i, j] = not mp.is_land(*mp(lon, lat))

    water_mask = np.expand_dims(water_mask, axis=2)
    water_mask = np.tile(water_mask, (1, 1, X_grid_dict[1][0].time.shape[0]))

    for k, v in X_grid_dict.items():
        v[0].update({'water_mask': (('y', 'x', 'time'), water_mask)})
