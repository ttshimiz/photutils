# Licensed under a 3-clause BSD style license - see LICENSE.rst
from __future__ import (absolute_import, division, print_function,
                        unicode_literals)
import numpy as np
from astropy.stats import sigma_clip
from astropy.utils import lazyproperty


__all__ = ['Background']


__doctest_requires__ = {('Background'): ['scipy']}


class Background(object):
    def __init__(self, data, box_shape, filter_shape, mask=None,
                 method='sextractor', sigclip_sigma=3., sigclip_iters=None):
        """
        filter_shape == (1, 1) -> no filtering
        """
        if mask is not None:
            if mask.shape != data.shape:
                raise ValueError('mask shape must match data shape')
        self.box_shape = box_shape
        self.filter_shape = filter_shape
        self.mask = mask
        self.method = method
        self.sigclip_sigma = sigclip_sigma
        self.sigclip_iters = sigclip_iters
        self.yextra = data.shape[0] % box_shape[0]
        self.xextra = data.shape[1] % box_shape[1]
        self.data_shape_orig = data.shape
        if (self.yextra > 0) or (self.xextra > 0):
            self.padded = True
            self.data = self._pad_data(data, mask)
        else:
            self.padded = False
            self.data = np.ma.masked_array(data, mask=mask)
        self._sigclip_data()

    def _pad_data(self, data, mask=None):
        """
        Pad the ``data`` and ``mask`` on the right and top with zeros if
        necessary to have a integer number of background meshes of size
        ``box_shape``.
        """
        ypad, xpad = 0, 0
        if self.yextra > 0:
            ypad = self.box_shape[0] - self.yextra
        if self.xextra > 0:
            xpad = self.box_shape[1] - self.xextra
        pad_width = ((0, ypad), (0, xpad))
        mode = str('constant')
        padded_data = np.pad(data, pad_width, mode=mode,
                             constant_values=[np.nan])
        padded_mask = np.isnan(padded_data)
        if mask is not None:
            mask_pad = np.pad(mask, pad_width, mode=mode,
                              constant_values=[False])
            padded_mask = np.logical_or(padded_mask, mask_pad)
        return np.ma.masked_array(padded_data, mask=padded_mask)

    def _sigclip_data(self):
        """
        Perform sigma clipping on the data in regions of size
        ``box_shape``.
        """
        ny, nx = self.data.shape
        ny_box, nx_box = self.box_shape
        y_nbins = ny / ny_box     # always integer because data were padded
        x_nbins = nx / nx_box     # always integer because data were padded
        data_rebin = np.ma.swapaxes(self.data.reshape(
            y_nbins, ny_box, x_nbins, nx_box), 1, 2).reshape(y_nbins, x_nbins,
                                                             ny_box * nx_box)
        self.data_sigclip = sigma_clip(
            data_rebin, sig=self.sigclip_sigma, axis=2,
            iters=self.sigclip_iters, cenfunc=np.ma.median, varfunc=np.ma.var)

    def _filter_meshes(self, mesh):
        """
        Apply a 2d median filter to the background meshes, including
        only pixels inside the image at the borders.
        """
        from scipy.ndimage import generic_filter
        return generic_filter(mesh, np.nanmedian, size=self.filter_shape,
                              mode='constant', cval=np.nan)

    def _resize_meshes(self, mesh):
        """
        Resize the background meshes to the original data size using
        bicubic interpolation.
        """
        from scipy.interpolate import RectBivariateSpline
        ny, nx = mesh.shape
        x = np.arange(nx)
        y = np.arange(ny)
        xx = np.linspace(x.min() - 0.5, x.max() + 0.5, self.data.shape[1])
        yy = np.linspace(y.min() - 0.5, y.max() + 0.5, self.data.shape[0])
        return RectBivariateSpline(y, x, mesh, kx=3, ky=3, s=0)(yy, xx)

    @lazyproperty
    def background_mesh(self):
        if self.method == 'mean':
            bkg_mesh = np.ma.mean(self.data_sigclip, axis=2)
        elif self.method == 'median':
            bkg_mesh = np.ma.median(self.data_sigclip, axis=2)
        elif self.method == 'sextractor':
            box_mean = np.ma.mean(self.data_sigclip, axis=2)
            box_median = np.ma.median(self.data_sigclip, axis=2)
            box_std = np.ma.std(self.data_sigclip, axis=2)
            condition = (np.abs(box_mean - box_median) / box_std) < 0.3
            bkg_est = (2.5 * box_median) - (1.5 * box_mean)
            bkg_mesh = np.where(condition, bkg_est, box_median)
        elif self.method == 'mode_estimate':
            bkg_mesh = (3. * np.ma.median(self.data_sigclip, axis=2) -
                        2. * np.ma.mean(self.data_sigclip, axis=2))
        else:
            raise ValueError('method "{0}" is not '
                             'defined'.format(self.method))
        if self.filter_shape != (1, 1):
            bkg_mesh = self._filter_meshes(bkg_mesh)
        return bkg_mesh

    @lazyproperty
    def background_rms_mesh(self):
        bkgrms_mesh = np.ma.std(self.data_sigclip, axis=2)
        if self.filter_shape != (1, 1):
            bkgrms_mesh = self._filter_meshes(bkgrms_mesh)
        return bkgrms_mesh

    @lazyproperty
    def background(self):
        bkg = self._resize_meshes(self.background_mesh)
        if self.padded:
            y0 = self.data_shape_orig[0]
            x0 = self.data_shape_orig[1]
            bkg = bkg[0:y0, 0:x0]
        if self.mask is not None:
            bkg[self.mask] = 0.
        return bkg

    @lazyproperty
    def background_rms(self):
        bkgrms = self._resize_meshes(self.background_rms_mesh)
        if self.padded:
            y0 = self.data_shape_orig[0]
            x0 = self.data_shape_orig[1]
            bkgrms = bkgrms[0:y0, 0:x0]
        if self.mask is not None:
            bkgrms[self.mask] = 0.
        return bkgrms

    @lazyproperty
    def background_median(self):
        if self.mask is not None:
            return np.median(self.background[~self.mask])
        else:
            return np.median(self.background)

    @lazyproperty
    def background_rms_median(self):
        if self.mask is not None:
            return np.median(self.background_rms[~self.mask])
        else:
            return np.median(self.background_rms)
