"""
Converts raw MODIS data to a data frame.
"""
import click
import cPickle as pickle
import pandas
import logging
import gzip
import os

from helper.geometry import get_default_bounding_box, filter_bounding_box_df
from base.converter import Converter

class ModisToDfConverter(Converter):

    def __init__(self, bounding_box=get_default_bounding_box()):
        super(ModisToDfConverter, self).__init__()
        self.bounding_box = bounding_box

    def load(self, src_dir):
        logging.info('Loading files from %s' % src_dir)
        files = [f for f in os.listdir(src_dir) if os.path.isfile(os.path.join(src_dir, f))]

        frames = []
        for file_name in files:
            logging.debug('Reading %s' % file_name)

            # Open one month of MODIS and create a df
            with gzip.open(os.path.join(src_dir,file_name), 'rb') as fin:
                df = pandas.read_csv(fin, sep=' ', skipinitialspace=True, index_col=0, parse_dates=[['YYYYMMDD', 'HHMM']], infer_datetime_format=True)
                df.index.names = ['datetime_utc'] # Make index the UTC datetime
                frames.append(df)

        # Concat all months together to make a single df
        logging.debug('Concatenating %d data frames' % len(frames))
        return pandas.concat(frames)

    def save(self, dest, data):
        logging.info('Saving data frame to %s' % dest)
        with open(dest, 'wb') as fout: 
            pickle.dump(data, fout, pickle.HIGHEST_PROTOCOL)

    def transform(self, data):
        logging.debug('Applying transforms to data frame')


        df = data[data['type']==0] # Include only vegetation fires
        df = filter_bounding_box_df(df, self.bounding_box) # Only use fires in bounding box

        # Localize datetime to UTC
        df = df.tz_localize('utc')

        logging.debug('Done applying transforms to data frame')
        return df

@click.command()
@click.argument('src_dir', type=click.Path(exists=True))
@click.argument('dest_path')
@click.option('--log', default='INFO')
def main(src_dir, dest_path, log):
    """
    Load MODIS data and create a data frame.
    """
    log_fmt = '%(asctime)s - %(levelname)s - %(message)s'
    logging.basicConfig(level=getattr(logging, log.upper()), format=log_fmt)

    logging.info('Starting MODIS to data frame conversion')
    ModisToDfConverter().convert(src_dir, dest_path)
    logging.info('Finished MODIS to data frame conversion')


if __name__ == '__main__':
    main()
