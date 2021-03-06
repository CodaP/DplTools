#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
dplkit.frame.clock
~~~~~~~~~~~~~~~~~~

Time is represented as datetime.datetime objects, intervals as datetime.timedelta objects
If you have another preferred format, write your own translator, thanks!

Clocks are sequences of (datetime, timedelta) tuples. We use namedtuple objects so we can bind .start, .width equivalently.
end = start + width
Time is meted out such that start <= time < (start + width). 

FixedFrameRate: a datetime generator taking start, interval, and optional end
VariableFrameRate: datetime generator which uses a key from an incoming framestream to extract datetime objects


:copyright: 2012 by University of Wisconsin Regents, see AUTHORS for more details
:license: GPLv3, see LICENSE for more details
"""

import logging
from datetime import datetime, timedelta
from dplkit.role.filter import aFilter
from dplkit.role.narrator import aNarrator
import dplkit.frame.keys as dplkeys

LOG = logging.getLogger(__name__)


class FixedFrameRate(aNarrator):
    """
    A FixedFrameRate is a source of simple frames having only start and width.
    """
    provides = {'start': dplkeys.start, 'width': dplkeys.width}

    def __init__(self, start, interval, stop=None, width = None, **kwargs):
        """
        generate timeframes at a regular framerate
        :param start: datetime object
        :param interval: timedelta, time distance between frames
        :param end: datetime, stop iteration after this time
        :param width: timedelta, optional, width of a timeframe (defaults to interval size)
        """
        super(self.__class__, self).__init__(None)
        self._start = start
        self._stop = stop
        self._width = interval if width is None else width
        self._interval = interval
        assert(isinstance(start, datetime))
        assert(isinstance(interval, timedelta))
        assert(stop is None or isinstance(stop, datetime))
        assert(width is None or isinstance(width, timedelta))

    def read(self, *args, **kwargs):
        now = self._start
        while (self._stop is None) or (now < self._stop):
            yield {'start': now, 'width': self._width}
            now += self._interval


class VariableFrameRate(aFilter):
    def __init__(self, source, time, width, **kwargs):
        """
        time and width can be 
            - callable which obtains datetime or timedelta from frame 
            - attribute name string for datetime / timedelta
            - constant value (not recommended for time)
        """
        super(self.__class__, self).__init__(source)
        self._source = source
        self._time = time
        self._width = width
        assert(callable(time) or isinstance(time, str))
        assert(callable(width) or isinstance(width, str) or isinstance(width,timedelta))
        self._get_time = self._getter(time)
        self._get_width = self._getter(width)

    def _getter(self, item):
        if callable(item): 
            return item
        elif isinstance(item, str): 
            return lambda frame: getattr(frame, item)
        else:
            return lambda frame: item

    def process(self, *args, **kwargs):
        for frame in self._source:
            t = self._get_time(frame)
            w = self._get_width(frame)
            assert(isinstance(t, datetime))
            assert(isinstance(w, timedelta))
            yield {'start': t, 'width': w}


def test():
    """ """
    pass


if __name__=='__main__':
    test()
