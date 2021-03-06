"""
Fetches GFS (Global Forecasting System) data.
"""

import logging
import os
import pickle
from ftplib import FTP

import click

from src.helper import date_util as du
from .ftp_async import AsyncFTP

alaska_bb = [55, 71, -165, -138]

server_name = "nomads.ncdc.noaa.gov"  # Server from which to pull the data
username = "anonymous"
password = "graffc@uci.edu"
gfs_loc = "GFS/analysis_only/"  # location on server of GFS data

year_month_dir_fmt = "%d%.2d"
year_month_day_dir_fmt = "%d%.2d%.2d"
grib_file_fmt_half_deg = "gfsanl_4_%s_%.4d_%.3d.grb2"
grib_file_fmt_one_deg = "gfsanl_3_%s_%.4d_%.3d.grb"

SCALE_HALF_DEG = '4'
SCALE_ONE_DEG = '3'

times = [0, 600, 1200, 1800]
offsets = [0, 3, 6, ]
time_offset_list = [(t, o) for t in times for o in offsets]


class GfsFetch(object):
    def __init__(self, dest_dir, start_year, end_year, scale_sel, available_files_path=None, files_to_fetch_path=None):
        self.dest_dir = dest_dir
        self.year_range = (start_year, end_year)
        self.async_ftp = AsyncFTP(server_name, username, password, pool_size=4, queue_size=50)

        self.available_files_path = available_files_path
        self.files_to_fetch_path = files_to_fetch_path

        # Choose scale to download
        if scale_sel == SCALE_HALF_DEG:
            self.grib_file_fmt = grib_file_fmt_half_deg
        elif scale_sel == SCALE_ONE_DEG:
            self.grib_file_fmt = grib_file_fmt_one_deg
        else:
            raise ValueError('Scale selection "%s" is invalid.' % scale_sel)

    def src_to_dest_path(self, path):
        path = path.split(gfs_loc)[1]
        return os.path.join(self.dest_dir, path.lstrip('/'))

    def fetch(self):
        """
        Fetch raw GFS data within year range.
        """
        # Find all available files with year range
        available_files = []
        if not self.available_files_path and not self.files_to_fetch_path:
            logging.debug('Fetching available files')

            available_files = self.fetch_available_files()
            with open(os.path.join(self.dest_dir, 'available_files.pkl'), 'wb') as f_out:
                pickle.dump(available_files, f_out, protocol=pickle.HIGHEST_PROTOCOL)

            logging.debug('Finished fetching available files list')

        elif not self.files_to_fetch_path:
            with open(self.available_files_path, 'rb') as fin:
                available_files = pickle.load(fin)

        # Filter out already downloaded files
        if not self.files_to_fetch_path:
            files_to_fetch = self.filter_existing_files(available_files)
            with open(os.path.join(self.dest_dir, 'files_to_fetch.pkl'), 'wb') as f_out:
                pickle.dump(files_to_fetch, f_out, protocol=pickle.HIGHEST_PROTOCOL)
            logging.debug('Finished filtering downloaded files')
        else:
            with open(self.files_to_fetch_path, 'rb') as fin:
                files_to_fetch = pickle.load(fin)

        if not files_to_fetch:
            logging.debug('No files to fetch')
            return

        self.make_dirs(files_to_fetch)

        self.async_ftp.start()
        for f in files_to_fetch:
            self.async_ftp.fetch(f, self.src_to_dest_path(f))

        self.async_ftp.join()

    def fetch_available_files(self):
        """
        Fetch list of all available files (within year_range).
        """
        available_files = []

        ftp = FTP(server_name)
        ftp.login(username, password)
        ftp.cwd(gfs_loc)

        for year in range(self.year_range[0], self.year_range[1] + 1):
            for month in range(1, 13):
                year_month = year_month_dir_fmt % (year, month)

                # Get list of all days in this month on server
                days_in_month_dir = map(lambda x: x.split("/")[-1], ftp.nlst(year_month))

                for day in range(1, du.days_per_month(month, du.is_leap_year(year)) + 1):

                    year_month_day = year_month_day_dir_fmt % (year, month, day)

                    # Check if day not on server
                    if year_month_day not in days_in_month_dir:
                        logging.debug('Missing Day: year %d month %d day %d not on server' % (year, month, day))
                        continue

                    dir_list_with_fluff = ftp.nlst('/'.join([year_month, year_month_day]))
                    grib_dir_list = map(lambda x: x.split('/')[-1], dir_list_with_fluff)

                    # Retrieve each grib file from server and save in day dir
                    today_grib_files = [self.grib_file_fmt % (year_month_day, t, offset) for (t, offset) in
                                        time_offset_list]
                    for grib_file in today_grib_files:
                        # Check if grib file not on server
                        if grib_file not in grib_dir_list:
                            logging.debug('Missing Grib: grib %s not on server' % grib_file)
                            continue

                        path = os.path.join(gfs_loc, year_month, year_month_day, grib_file)
                        available_files.append(path)

        # Clean-up
        ftp.quit()

        return available_files

    def filter_existing_files(self, files):
        filtered_files = []

        for f in files:
            local_f = self.src_to_dest_path(f)
            if not os.path.isfile(local_f):
                filtered_files.append(f)

        return filtered_files

    def make_dirs(self, files):
        dirs = set(map(lambda x: os.path.dirname(self.src_to_dest_path(x)), files))
        for d in dirs:
            if not os.path.exists(d):
                os.makedirs(d)


@click.command()
@click.argument('dest_dir', type=click.Path(exists=True))
@click.option('--start', default=2007, type=click.INT)
@click.option('--end', default=2016, type=click.INT)
@click.option('--log', default='INFO')
@click.option('--avail', default=None, type=click.Path(exists=True))
@click.option('--fetch', default=None, type=click.Path(exists=True))
@click.option('--scale', default='4', type=click.Choice([SCALE_HALF_DEG, SCALE_ONE_DEG]))
def main(dest_dir, start, end, log, avail, fetch, scale):
    log_fmt = '%(asctime)s - %(levelname)s - %(message)s'
    logging.basicConfig(level=getattr(logging, log.upper()), format=log_fmt)

    logging.info('Storing data in "%s". Range is [%d, %d]. Downloading scale %s.' % (dest_dir, start, end, scale))

    logging.info('Starting fetch for GFS')
    GfsFetch(dest_dir, start, end, scale, avail, fetch).fetch()
    logging.info('End fetch for GFS')


if __name__ == '__main__':
    main()
