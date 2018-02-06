"""
Converts model output to a grid.
"""
import numpy as np

from base.model import Model

import datetime as dt

import helper.date_util as du
import helper.df_util as dfu
import helper.geometry as geo

class GridPredictorModel(Model):
    def __init__(self, model, bounding_box, fire_season=((5,14), (8,31))):
        """
        :param model: model to change prediction of
        """
        super(GridPredictorModel, self).__init__()

        self.model = model
        self.bounding_box = bounding_box
        self.fire_season = fire_season

    def fit(self, X, y=None):
        return self.model.fit(X, y)

    def pred_to_grid(self, preds, X, shape=None):
        years = list(set(X.time.dt.year.values))
        years.sort()
        years = dict(zip(years,range(0,len(years))))
        year_size = shape[2]/len(years)

        # Create grid
        # TODO: add dynamic sizing (bounds and increment)
        self.bounding_box = geo.LatLonBoundingBox(55, 71, -165, -138)
        spatial_size = np.shape(self.bounding_box.make_grid()[0])
        #dates = list(du.daterange(dt.date(year, self.fire_season[0][0], self.fire_season[0][1]),
        #    dt.date(year, self.fire_season[1][0], self.fire_season[1][1]) + du.INC_ONE_DAY))
        #grid = np.zeros(spatial_size + (len(set(X.time.values)),))
        grid = np.zeros(shape)

        # Assign each detection to a cell in time and space
        for pred, row in zip(preds,X.to_dataframe().itertuples()):
            lat,lon,date = row.lat_centroid, row.lon_centroid, row.Index.date()
            lat_ind,lon_ind = self.bounding_box.latlon_to_indices(lat,lon,spatial_size[0]) # TODO: add dynamic sizing
            start = dt.date(date.year, self.fire_season[0][0], self.fire_season[0][1])
            date_ind = (date-start).days + years[date.year] * year_size

            grid[lat_ind,lon_ind,date_ind] += pred

        return grid

    def predict(self, X, shape=None):
        pred = self.model.predict(X, shape) 
        return self.pred_to_grid(pred, X, shape)
