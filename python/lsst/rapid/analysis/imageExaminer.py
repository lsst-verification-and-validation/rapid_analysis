#
# LSST Data Management System
#
# Copyright 2008-2018  AURA/LSST.
#
# This product includes software developed by the
# LSST Project (http://www.lsst.org/).
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope hat it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the LSST License Statement and
# the GNU General Public License along with this program.  If not,
# see <https://www.lsstcorp.org/LegalNotices/>.
#

__all__ = ['ImageExaminer']

import matplotlib.pyplot as plt
import numpy as np
from numpy.linalg import norm

from scipy.ndimage.filters import gaussian_filter
import scipy.ndimage as ndImage

from matplotlib import cm
from matplotlib.ticker import LinearLocator
from matplotlib.colors import LogNorm
from matplotlib.offsetbox import AnchoredText
import matplotlib.patches as patches

import lsst.geom as geom
from scipy.optimize import curve_fit
from lsst.pipe.tasks.quickFrameMeasurement import QuickFrameMeasurementTask, QuickFrameMeasurementTaskConfig
from lsst.rapid.analysis.utils import getImageStats, argMax2d, countPixels


SIGMATOFWHM = 2.0*np.sqrt(2.0*np.log(2.0))


def gauss(x, a, x0, sigma):
    return a*np.exp(-(x-x0)**2/(2*sigma**2))


class ImageExaminer():
    """Class for the reproducing the functionality of imexam.
    """
    astroMappings = {"object": "Object name",
                     "mjd": "MJD",
                     "expTime": "Exp Time",
                     "filter": "Filter",
                     "grating": "grating",
                     "airmass": "Airmass",
                     "rotangle": "Rotation Angle",
                     "az": "Azimuth (deg)",
                     "el": "Elevation (deg)"}

    imageMappings = {"centroid": "Centroid",
                     "maxValue": "Max pixel value",
                     "maxPixelLocation": "Max pixel location",
                     "multipleMaxPixels": "Mutiple max pixels?",
                     "nBadPixels": "Num bad pixels",
                     "nSatPixels": "Num saturated pixels",
                     "percentile99": "99th percentile",
                     "percentile9999": "99.99th percentile",
                     "clippedMean": "Clipped mean",
                     "clippedStddev": "Clipped stddev"}

    cutoutMappings = {"nStatPixInBox": "nSat in cutout",
                      "fitAmp": "Radial fitted amp",
                      "fitGausMean": "Radial fitted position",
                      "fitFwhm": "Radial fitted FWHM",
                      "eeRadius50": "50% flux radius",
                      "eeRadius80": "80% flux radius",
                      "eeRadius90": "90% flux radius"}

    def __init__(self, exp, doTweakCentroid=True, savePlots=None, centroid=None, boxHalfSize=50):

        self.exp = exp
        self.savePlots = savePlots
        self.doTweakCentroid = doTweakCentroid

        self.boxHalfSize = boxHalfSize
        if centroid is None:
            qfmTaskConfig = QuickFrameMeasurementTaskConfig()
            qfmTask = QuickFrameMeasurementTask(config=qfmTaskConfig)
            result = qfmTask.run(exp)
            if not result.success:
                msg = ("Failed to automatically find source in image. "
                       "Either provide a centroid manually or use a new image")
                raise RuntimeError(msg)
            self.centroid = result.brightestObjCentroid
        else:
            self.centroid = centroid

        self.data = self.getStarBoxData()
        if self.doTweakCentroid:
            self.tweakCentroid()
            self.data = self.getStarBoxData()

        self.xx, self.yy = self.getMeshGrid(self.data)

        self.imStats = getImageStats(self.exp)
        self.imStats.centroid = self.centroid
        self.imStats.intCentroid = self.intCoords(self.centroid)
        self.imStats.intCentroidRounded = self.intRoundCoords(self.centroid)
        self.imStats.nStatPixInBox = self.nSatPixInBox

        self.radialAverageAndFit()

    def intCoords(self, coords):
        return np.asarray(coords, dtype=int)

    def intRoundCoords(self, coords):
        return (int(round(coords[0])), int(round(coords[1])))

    def tweakCentroid(self):
        peak, uniquePeak, otherPeaks = argMax2d(self.data)
        # saturated stars don't tend to have ambiguous max pixels
        # due to the bunny ears left after interpolation
        nSatPix = self.nSatPixInBox

        if not uniquePeak or nSatPix:
            print('Found multiple max pixels or star is saturated, usign CoM for centroid')
            peak = ndImage.center_of_mass(self.data)

        offset = np.asarray(peak) - np.array((self.boxHalfSize, self.boxHalfSize))
        print(f"Centroid adjusted by {offset} pixels")
        x = self.centroid[0] + offset[1]  # yes, really, centroid is x,y offset is y,x
        y = self.centroid[1] + offset[0]
        self.centroid = (x, y)

    def getStats(self):
        return self.imStats

    @staticmethod
    def _calcMaxBoxHalfSize(centroid, chipBbox):
        """Calc the minimum distance between the centroid and chip edge."""
        ll = chipBbox.getBeginX()
        r = chipBbox.getEndX()
        d = chipBbox.getBeginY()
        u = chipBbox.getEndY()

        x, y = np.array(centroid, dtype=int)
        maxSize = np.min([(x-ll), (r-x-1), (u-y-1), (y-d)])  # extar -1 in x because [)
        assert maxSize >= 0, "Box calculation went wrong"
        return maxSize

    def _calcBbox(self, centroid):
        centroidPoint = geom.Point2I(centroid)
        extent = geom.Extent2I(1, 1)
        bbox = geom.Box2I(centroidPoint, extent)
        bbox = bbox.dilatedBy(self.boxHalfSize)
        bbox = bbox.clippedTo(self.exp.getBBox())
        if bbox.getDimensions()[0] != bbox.getDimensions()[1]:
            # TODO: one day support clipped, nonsquare regions
            # but it's nontrivial due to all the plotting options

            maxsize = self._calcMaxBoxHalfSize(centroid, self.exp.getBBox())
            msg = (f"With centroid at {centroid} and boxHalfSize {self.boxHalfSize} "
                   "the selection runs off the edge of the chip. Boxsize has been "
                   f"automatically shrunk to {maxsize} (only square selections are "
                   "currently supported)")
            print(msg)
            self.boxHalfSize = maxsize
            return self._calcBbox(centroid)

        return bbox

    def getStarBoxData(self):
        bbox = self._calcBbox(self.centroid)
        self.starBbox = bbox  # needed elsewhere, so always set when calculated
        self.nSatPixInBox = countPixels(self.exp.maskedImage[self.starBbox], 'SAT')
        return self.exp.image[bbox].array

    def getMeshGrid(self, data):
        xlen, ylen = data.shape
        xx = np.arange(-1*xlen/2, xlen/2, 1)
        yy = np.arange(-1*ylen/2, ylen/2, 1)
        xx, yy = np.meshgrid(xx, yy)
        return xx, yy

    @staticmethod
    def quickSmooth(data, sigma=2):
        kernel = [sigma, sigma]
        smoothData = gaussian_filter(data, kernel, mode='constant')
        return smoothData

    def radialAverageAndFit(self):
        xlen, ylen = self.data.shape
        center = np.array([xlen/2, ylen/2])
        # TODO: add option to move centroid to max pixel for radial (argmax 2d)

        distances = []
        values = []

        # could be much faster, but the array is tiny so its fine
        for i in range(xlen):
            for j in range(ylen):
                value = self.data[i, j]
                dist = norm((i, j) - center)
                if dist > xlen//2:
                    continue  # clip to box size, we don't need a factor of sqrt(2) extra
                values.append(value)
                distances.append(dist)

        peakPos = 0
        amplitude = np.max(values)
        width = 10

        bounds = ((0, 0, 0), (np.inf, np.inf, np.inf))

        try:
            pars, pCov = curve_fit(gauss, distances, values, [amplitude, peakPos, width], bounds=bounds)
            pars[0] = np.abs(pars[0])
            pars[2] = np.abs(pars[2])
        except RuntimeError:
            pars = None
            self.imStats.fitAmp = np.nan
            self.imStats.fitMean = np.nan
            self.imStats.fitFwhm = np.nan

        if pars is not None:
            self.imStats.fitAmp = pars[0]
            self.imStats.fitGausMean = pars[1]
            self.imStats.fitFwhm = pars[2] * SIGMATOFWHM

        self.radialDistances = distances
        self.radialValues = values

        # calculate encircled energy metric too
        # sort distances and values in step by distance
        d = np.array([(r, v) for (r, v) in sorted(zip(self.radialDistances, self.radialValues))])
        self.radii = d[:, 0]
        values = d[:, 1]
        self.cumFluxes = np.cumsum(values)
        self.cumFluxesNorm = self.cumFluxes/np.max(self.cumFluxes)

        self.imStats.eeRadius50 = self.getEncircledEnergyRadius(50)
        self.imStats.eeRadius80 = self.getEncircledEnergyRadius(80)
        self.imStats.eeRadius90 = self.getEncircledEnergyRadius(90)

        return

    def getEncircledEnergyRadius(self, percentage):
        """Radius in pixels with the given percentage of encircled energy.

        100% is at the boxHalfWidth dy definition.
        """
        return self.radii[np.argmin(np.abs((percentage/100)-self.cumFluxesNorm))]

    def plotRadialAverage(self, ax=None):
        plotDirect = False
        if not ax:
            ax = plt.subplot(111)
            plotDirect = True

        distances = self.radialDistances
        values = self.radialValues
        pars = (self.imStats.fitAmp,
                self.imStats.fitGausMean,
                self.imStats.fitFwhm / SIGMATOFWHM)

        fitFailed = np.isnan(pars).any()

        ax.plot(distances, values, 'x', label='Radial average')
        if not fitFailed:
            fitline = gauss(distances, *pars)
            ax.plot(distances, fitline, label="Gaussian fit")

        ax.set_ylabel('Flux (ADU)')
        ax.set_xlabel('Radius (pix)')
        ax.set_aspect(1.0/ax.get_data_ratio(), adjustable='box')  # equal aspect for non-images
        ax.legend()

        if plotDirect:
            plt.show()

    def plotContours(self, ax=None, nContours=10):
        plotDirect = False
        if not ax:
            fig = plt.figure(figsize=(8, 8))  # noqa F841
            ax = plt.subplot(111)
            plotDirect = True

        vmin = np.percentile(self.data, 0.1)
        vmax = np.percentile(self.data, 99.9)
        lvls = np.linspace(vmin, vmax, nContours)
        intervalSize = (lvls[1]-lvls[0])
        contourPlot = ax.contour(self.xx, self.yy, self.data, levels=lvls)  # noqa F841
        print(f"Contoured from {vmin:,.0f} to {vmax:,.0f} using {nContours} contours of {intervalSize:.1f}")

        ax.tick_params(which="both", direction="in", top=True, right=True, labelsize=8)
        ax.set_aspect("equal")

        if plotDirect:
            plt.show()

    def plotSurface(self, ax=None, useColor=True):
        plotDirect = False
        if not ax:
            fig, ax = plt.subplots(subplot_kw={"projection": "3d"}, figsize=(10, 10))
            plotDirect = True

        if useColor:
            surf = ax.plot_surface(self.xx, self.yy, self.data, cmap=cm.plasma,
                                   linewidth=1, antialiased=True, color='k', alpha=0.9)
        else:
            surf = ax.plot_wireframe(self.xx, self.yy, self.data, cmap=cm.gray,  # noqa F841
                                     linewidth=1, antialiased=True, color='k')

        ax.zaxis.set_major_locator(LinearLocator(10))
        ax.zaxis.set_major_formatter('{x:,.0f}')

        if plotDirect:
            plt.show()

    def plotStar(self, ax=None, logScale=False):
        # TODO: display centroid in use
        plotDirect = False
        if not ax:
            ax = plt.subplot(111)
            plotDirect = True

        interp = 'none'
        if logScale:
            ax.imshow(self.data, norm=LogNorm(), origin='lower', interpolation=interp)
        else:
            ax.imshow(self.data, origin='lower', interpolation=interp)
        ax.tick_params(which="major", direction="in", top=True, right=True, labelsize=8)

        xlen, ylen = self.data.shape
        center = np.array([xlen/2, ylen/2])
        ax.plot(*center, 'r+', markersize=10)
        ax.plot(*center, 'rx', markersize=10)

        if plotDirect:
            plt.show()

    def plotFullExp(self, ax=None):
        plotDirect = False
        if not ax:
            fig = plt.figure(figsize=(10, 10))
            ax = fig.add_subplot(111)
            plotDirect = True

        imData = self.quickSmooth(self.exp.image.array, 2.5)
        vmin = np.percentile(imData, 10)
        vmax = np.percentile(imData, 99.9)
        ax.imshow(imData, norm=LogNorm(vmin=vmin, vmax=vmax),
                  origin='lower', cmap='gray_r', interpolation='bicubic')
        ax.tick_params(which="major", direction="in", top=True, right=True, labelsize=8)

        xy0 = self.starBbox.getCorners()[0].x, self.starBbox.getCorners()[0].y
        width, height = self.starBbox.getWidth(), self.starBbox.getHeight()
        rect = patches.Rectangle(xy0, width, height, linewidth=1, edgecolor='r', facecolor='none')
        ax.add_patch(rect)

        if plotDirect:
            plt.show()

    def plotRowColSlices(self, ax=None, logScale=False):
        # TODO: display centroid in use

        # slice through self.boxHalfSize because it's always the point being
        # used by definition
        rowSlice = self.data[self.boxHalfSize, :]
        colSlice = self.data[:, self.boxHalfSize]

        plotDirect = False
        if not ax:
            ax = plt.subplot(111)
            plotDirect = True

        xs = range(-1*self.boxHalfSize, self.boxHalfSize+1)
        ax.plot(xs, rowSlice, label='Row plot')
        ax.plot(xs, colSlice, label='Column plot')
        if logScale:
            pass
            # TODO: set yscale as log here also protect against negatives

        ax.set_ylabel('Flux (ADU)')
        ax.set_xlabel('Radius (pix)')
        ax.set_aspect(1.0/ax.get_data_ratio(), adjustable='box')  # equal aspect for non-images

        ax.legend()
        if plotDirect:
            plt.show()

    def plotStats(self, ax, lines):

        text = "\n".join([line for line in lines])

        stats_text = AnchoredText(text, loc="center", pad=0.5,
                                  prop=dict(size=14, ma="left", backgroundcolor="white",
                                            color="black", family='monospace'))
        ax.add_artist(stats_text)
        ax.axis('off')

    def plotCurveOfGrowth(self, ax=None):
        plotDirect = False
        if not ax:
            ax = plt.subplot(111)
            plotDirect = True

        ax.plot(self.radii, self.cumFluxesNorm, markersize=10)
        ax.set_ylabel('Encircled flux (%)')
        ax.set_xlabel('Radius (pix)')

        ax.set_aspect(1.0/ax.get_data_ratio(), adjustable='box')  # equal aspect for non-images

        if plotDirect:
            plt.show()

    def plot(self):
        figsize = 6
        fig = plt.figure(figsize=(figsize*3, figsize*2))

        ax1 = fig.add_subplot(331)
        ax2 = fig.add_subplot(332)
        ax3 = fig.add_subplot(333)
        ax4 = fig.add_subplot(334, projection='3d')
        ax5 = fig.add_subplot(335)
        ax6 = fig.add_subplot(336)
        ax7 = fig.add_subplot(337)
        ax8 = fig.add_subplot(338)
        ax9 = fig.add_subplot(339)

        axExp = ax1
        axStar = ax2
        axStats1 = ax3  # noqa F841 - overwritten
        axSurf = ax4
        axCont = ax5
        axStats2 = ax6  # noqa F841 - overwritten
        axSlices = ax7
        axRadial = ax8
        axCoG = ax9  # noqa F841 - overwritten

        self.plotFullExp(axExp)
        self.plotStar(axStar)
        self.plotSurface(axSurf)
        self.plotContours(axCont)
        self.plotRowColSlices(axSlices)
        self.plotRadialAverage(axRadial)

        # overwrite three axes with this one spanning 3 rows
        axStats = plt.subplot2grid((3, 3), (0, 2), rowspan=2)

        lines = []
        lines.append("     ---- Astro ----")
        lines.extend(self.translateStats(self.imStats, self.astroMappings))
        lines.append("\n     ---- Image ----")
        lines.extend(self.translateStats(self.imStats, self.imageMappings))
        lines.append("\n     ---- Cutout ----")
        lines.extend(self.translateStats(self.imStats, self.cutoutMappings))
        self.plotStats(axStats, lines)

        self.plotCurveOfGrowth(axCoG)

        plt.tight_layout()
        if self.savePlots:
            print(f'Plot saved to {self.savePlots}')
            fig.savefig(self.savePlots)
        plt.show()

    @staticmethod
    def translateStats(imStats, mappingDict):
        lines = []
        for k, v in mappingDict.items():
            try:
                value = getattr(imStats, k)
            except Exception:
                lines.append("")
                continue

            if type(value) == float or isinstance(value, np.floating):
                value = f"{value:,.3f}"
            if k == 'centroid':  # special case the only tuple
                value = f"{value[0]:.1f}, {value[1]:.1f}"
            lines.append(f"{v} = {value}")
        return lines

    def plotAll(self):
        self.plotStar()
        self.plotRadialAverage()
        self.plotContours()
        self.plotSurface()
        self.plotStar()
        self.plotRowColSlices()
