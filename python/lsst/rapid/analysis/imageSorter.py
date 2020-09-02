# This file is part of rapid_analysis.
#
# Developed for the LSST Data Management System.
# This product includes software developed by the LSST Project
# (https://www.lsst.org).
# See the COPYRIGHT file at the top-level directory of this distribution
# for details of code ownership.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import pickle
from PIL import Image
import matplotlib.pyplot as plt
import os
from os import system

TIPS = """
G - Good centroid
B - Bad centroid

L - median per row lines
C - very visible crosstalk
F - Poor focus
D - Donut image
N - No target star marked!
A - Bad amp offsets!
V - No back bias?
P - Bad PSF (rotation/pointing/tracking error, earthquake, etc)
= - apply the same annotations as the previous image
"""


class ImageSorter():
    """Take a list on png files, as created by lsst.rapid.analysis.animator
    and tag each dataId with a number of attributes. Some suggestions:

    F - focus is poor, or part of a focus sweep
    V - potential lack of bias voltage
    D - donut image
    G - Significant ghosting or ghoulies present
    X - significant crosstalk

    ! - something is totally borked (pointing error, earthquake-PSF, etc)

    Returns a dict of dataId dictionaries with values being the corresponding
    """

    def __init__(self, fileList, outputFilename):
        self.fileList = fileList
        self.outputFilename = outputFilename

    @staticmethod
    def _getDataIdFromFilename(filename):
        dayIndex = filename.index('dayObs-') + len('dayObs-')
        dayObs = filename[dayIndex:dayIndex + 10]  # YYYY-MM-DD == len 10
        seqIndex = filename.index('seqNum-') + len('seqNum-')
        seqNum = int(filename[seqIndex:].split('-')[0])
        return (dayObs, seqNum)

    def getPreviousAnnotation(self, info, imNum):
        if imNum == 0:
            raise RuntimeError("There is no previous annotation for the first image.")

        previousFilename = self.fileList[imNum-1]
        previousDataId = self._getDataIdFromFilename(previousFilename)
        previousAnnotation = info[previousDataId]
        return previousAnnotation

    def addData(self, dataId, info, answer, mode, imNum):
        """Modes = O(verwrite), S(kip), A(ppend)"""
        if '=' in answer:
            answer = self.getPreviousAnnotation(info, imNum)

        if dataId not in info:
            info[dataId] = answer
            return

        if mode == 'O':
            info[dataId] = answer
        elif mode in ['B', 'A']:
            oldAnswer = info[dataId]
            answer = "".join([oldAnswer, answer])
            info[dataId] = answer
        else:
            raise RuntimeError(f"Unrecognised mode {mode} - should be impossible")
        return

    @classmethod
    def loadAnnotations(cls, pickleFilename):
        """Load back and split up annotations for easy use.

        Anything after a space is returned as a whole string,
        anything before it is lower-cased and returned as tags.

        from lsst.rapid.analysis import ImageSorter
        tags, notes = ImageSorter.loadAnnotations(pickleFilename)
        """
        loaded = cls._load(pickleFilename)

        tags, notes = {}, {}

        for dataId, answerFull in loaded.items():
            answer = answerFull.lower()
            if " " in answer:
                answer = answerFull.split()[0]
                notes[dataId] = " ".join([_ for _ in answerFull.split()[1:]])
            tags[dataId] = answer

        return tags, notes

    @staticmethod
    def _load(filename):
        """Internal loading only.

        Not to be used by users for reading back annotations"""
        with open(filename, "rb") as pickleFile:
            info = pickle.load(pickleFile)
        return info

    @staticmethod
    def _save(info, filename):
        with open(filename, "wb") as dumpFile:
            pickle.dump(info, dumpFile)

    def sortImages(self):
        mode = 'A'
        info = {}
        if os.path.exists(self.outputFilename):
            info = self._load(self.outputFilename)

            print(f'Output file {self.outputFilename} exists with info on {len(info)} files:')
            print('Press A - view all images, appending info to existing entries')
            print('Press O - view all images, overwriting existing entries')
            print('Press S - skip all images with existing annotations, including blank annotations')
            print('Press B - skip all images with annotations that are not blank')
            print('Press D - just display existing data and exit')
            print('Press Q to quit')
            mode = input()
            mode = mode[0].upper()

            if mode == 'Q':
                exit()
            elif mode == 'D':
                for dataId, value in info.items():
                    print(f"{dataId[0]} - {dataId[1]}: {value}")
                exit()
            elif mode in 'AOSB':
                pass
            else:
                print("Unrecognised response - try again")
                self.sortImages()
                return  # don't run twice in this case!

        # need to write file first, even if empty, because _load and _save
        # are inside the loop to ensure that annotations aren't lost even on
        # full crash
        print(TIPS)
        self._save(info, self.outputFilename)

        plt.figure(figsize=(10, 10))
        for imNum, filename in enumerate(self.fileList):
            info = self._load(self.outputFilename)

            dataId = self._getDataIdFromFilename(filename)
            if dataId in info and mode in ['S', 'B']:  # always skip if found for S and if not blank for B
                if (mode == 'S') or (mode == 'B' and info[dataId] != ""):
                    continue

            with Image.open(filename) as pilImage:
                pilImage = Image.open(filename)
                width, height = pilImage.size
                cropLR, cropUD = 100, 180
                cropped = pilImage.crop((cropLR, cropUD, width-cropLR, height-cropUD))
                plt.clf()
                plt.imshow(cropped, interpolation="bicubic")
                plt.show(block=False)
                plt.draw()  # without this you get the same image each time
                plt.tight_layout()
                osascriptCall = '''/usr/bin/osascript -e 'tell app "Finder" to '''
                osascriptCall += '''set frontmost of process "Terminal" to true' '''
                system(osascriptCall)

            oldAnswer = None  # just so we can display existing info with the dataId
            if dataId in info:
                oldAnswer = info[dataId]
            inputStr = f"{dataId[0]} - {dataId[1]}: %s" % ("" if oldAnswer is None else oldAnswer)
            answer = input(inputStr)
            if 'exit' in answer:
                break  # break don't exit so data is written!

            self.addData(dataId, info, answer, mode, imNum)
            self._save(info, self.outputFilename)

        print(f'Info written to {self.outputFilename}')

        return info


if __name__ == '__main__':
    fileList = ['/Users/merlin/rsync/animatorOutput/pngs/dayObs-2020-02-17-seqNum-232-calexp.png',
                '/Users/merlin/rsync/animatorOutput/pngs/dayObs-2020-02-17-seqNum-233-calexp.png',
                '/Users/merlin/rsync/animatorOutput/pngs/dayObs-2020-02-17-seqNum-234-calexp.png',
                '/Users/merlin/rsync/animatorOutput/pngs/dayObs-2020-02-17-seqNum-235-calexp.png']

    sorter = ImageSorter(fileList, '/Users/merlin/scratchfile.txt')
    sorter.sortImages()
