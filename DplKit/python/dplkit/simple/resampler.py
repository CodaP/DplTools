#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
dplkit.simple.resampler
~~~~~~~~~~~~~~~~~~~~~~~


Useful examples of resampling filters


:copyright: 2012 by University of Wisconsin Regents, see AUTHORS for more details
:license: GPLv3, see LICENSE for more details
"""


__author__ = 'R.K.Garcia <rayg@ssec.wisc.edu>'
__revision__ = '$Id:$'
__docformat__ = 'reStructuredText'




import os, sys
import logging, unittest
import bisect
from collections import defaultdict, namedtuple

from ..role.resampler import aResampler # abstract base class for resampler role
from ..frame.struct import as_struct    # allow both dictionary-style and struct-style frames
from ..util.interp import TimeSeriesPolyInterp


LOG = logging.getLogger(__name__)



def center_time(data):
    "extract start-time, time-width, and center-time from a struct-like frame; or start,None,start if no width provided"
    start = data.start   # ref dplkit.frame.keys
    width = getattr(data, 'width', None)
    t = start if (width is None) else (start + width/2)
    return start, t, width



class TimeInterpolatedMerge(aResampler):
    """
    Using the time frame information from the primary stream, 
    time-interpolate channels from the other streams into a composite stream.

    This will yield all frames from the primary.
    In the case that one or more of the secondaries cannot provide data, their channels are None.
    """
    provides = None 
    requires = None

    _schedule = None  # schedule of what data comes from where, [(channel, source), ...]
    _tsips = None   # interpolator dictionary { channel: TimeSeriesPolyInter}
    _primary = None

    @staticmethod
    def _generate_meta_from_sequence(channel_seq, *sources):
        schedule = list(channel_seq)
        meta = dict( (chn,source.meta[chn]) for (chn,source) in schedule )
        return meta, schedule

    @staticmethod
    def _generate_meta_from_set(channels, *sources):
        schedule = []
        meta = {}
        for source in sources:
            assert(hasattr(source, 'meta'))
            for chn,nfo in source.meta.items():
                if chn in meta:
                    continue
                if (channels is not None) and (chn not in channels):
                    continue
                meta[chn] = nfo
                schedule.append((chn, source))
        return meta, schedule

    @staticmethod
    def _meta_sequence(*sources):
        for source in sources:
            assert(hasattr(source,'meta'))
            for chn in source.meta.keys():
                yield chn,source

    @staticmethod
    def _generate_meta(channels, *sources):
        """
        return metadata dictionary and transfer schedule using each of the upstream sources
        """
        if channels is not None:
            if hasattr(channels, 'keys'):
                return TimeInterpolatedMerge._generate_meta_from_sequence(channels.items(), *sources)
            elif hasattr(channels, '__contains__'): 
                return TimeInterpolatedMerge._generate_meta_from_set(channels, *sources)
            elif iterable(channels):
                return TimeInterpolatedMerge._generate_meta_from_sequence(channels, *sources)                
            else:
                raise ValueError('channels must be a mappable or set')
        else:
            return TimeInterpolatedMerge._generate_meta_from_sequence(TimeInterpolatedMerge._meta_sequence(*sources))




    def __init__(self, primary, secondary_list, channels=None, order=1, pool_depth=16, allow_nans=False):
        """
        Initialize a time-interpolated merge filter with one primary and one or more secondary sources.
        Overlapping channels are selected with priority on earlier (primary, then secondaries in order).
        Metaframe for self is updated to merge primary and secondary source metadata.
        Update order is preserved such that primary is always updated first, followed by secondaries in order.

        :param primary: the framestream that we get time information from
        :param secondary_list : a collection of secondary framestreams which get merged in order
        :param channels: optional set() of channel names which get merged from the secondaries. 
                         If this is a dictionary, the key is the channel name and value is which source to use.
        :param order: polynomial order to use for interpolating, defaults to 1 (see dplkit.util.interp.TimeSeriesPolyInterp)
        :param pool_depth: how many recent values to keep in cache for individual channels

        """
        # examine .meta from primary and each of the secondaries to get list of channels and our .provides dict
        # compare against channels-of-interest set if provided to create a table
        # merge meta from primary with meta from secondaries, masked by channels-of-interest
        # set .meta for self
        self.provides, self._schedule = TimeInterpolatedMerge._generate_meta(channels, primary, *secondary_list)

        LOG.debug('transfer schedule: %s' % repr(self._schedule))
        LOG.debug('what we provide: %s' % repr(list(self.provides.keys())))

        self._primary = primary
        self._tsips = dict((channel, TimeSeriesPolyInterp(order=order, pool_low=pool_depth)) for (channel,_) in self.provides)
        self._allow_nans = allow_nans


    def _eat_next_frame(self, source, data = None):
        "append new data from a source to the interpolator objects, returning the new time-span we can interpolate within"
        if data is None:
            data = as_struct(source.next())
        start, t, width = center_time(data)
        LOG.debug('using frame center time of %s' % (t))
        # collect inner time-span of all the TSIPs to return as guidance
        s,e = None, None
        for channel,sched_source in self._schedule: 
            tsip = self._tsips[channel]  # FUTURE: merge this into schedule?
            if source is sched_source:
                tsip.append((t, getattr(data, channel)))
            ts,te = tsip.span
            if s is None or s < ts: 
                s = ts
            if e is None or e > te: 
                e = te
        return s,e 


    def _frame_at_time(self, when):
        "return a dictionary frame for a given datetime"
        zult = {}
        for (channel, source) in self._schedule:
            tsip = self._tsips[channel]  # FUTURE: merge into _schedule?
            s,e = tsip.span
            if when < s: 
                if self._allow_nans:
                    LOG.warning('time %s is outside the interpolation realm %s ~ %s, expect NaNs for %s' % (when, s,e,channel))
                else:
                    raise ValueError("out-of-order input sequence? cannot interpolate %s before %s (attempted %s)" % (channel, s, when))
            while when > e:
                LOG.debug('feeding frame of data to %s interpolator and its siblings, waiting for %s' % (channel, when))
                s,e = self._eat_next_frame(source)
                LOG.debug('can now interpolate %s %s ~ %s' % (channel, s, e))

            # perform time interpolation
            zult[channel] = tsip(when)
        return zult


    def resample(self, *args, **kwargs):
        """
        Yield a framestream of dictionaries, one for each timeframe in the primary source.        
        Interpolate the channels provided by secondary sources.
        Use dplkit.frame.struct.as_struct() adapter if struct-like frames are preferred.
        """
        # for each frame in the primary
        #  see if we have an applicable frame from each of the secondaries
        #  call secondary.next() if we need another frame in order to interpolate
        #  catch StopIteration exception? yes, just return when we encounter StopIteration
        #  time interpolate between neighboring secondary frames, for channels of interest
        #    use simple linear interpolation using frame center times? 
        #    maintain dimensionality in all the arrays in the desired channels
        #  if channels is None, use all channels from the secondary
        #  create a new output frame which merges the primary data with the interpolated secondary data
        #  yield that frame

        # for each frame in primary, obtain time window
        # for each channel,source in schedule
        #   check source 
        for timeframe in self._primary:
            timeframe = as_struct(timeframe)
            self._eat_next_frame(self._primary, timeframe)
            start, when, width = center_time(timeframe)
            # FUTURE: should this be part of schedule?
            zult = self._frame_at_time(when)
            zult['start'] = start
            zult['width'] = width
            zult['_center_time'] = when    #FUTURE: is this advisable? useful? 
            yield zult




# def test1(lidar_info, radar_info):

#     lidar = dpl_rti(**lidar_info)
#     radar = dpl_rti(**radar_info)

#     # does this take start time, time interval, end time?
#     # take the time information from the first parameter
#     tim = TimeInterpolatedMerge(lidar, [radar])
#     merge = MergeStreams([lidar, radar])
#     for frame in merge:
#         printstuff(frame)






def main():
    import optparse
    usage = """
%prog [options] ...


"""
    parser = optparse.OptionParser(usage)
    parser.add_option('-t', '--test', dest="self_test",
                    action="store_true", default=False, help="run self-tests")
    parser.add_option('-v', '--verbose', dest='verbosity', action="count", default=0,
                    help='each occurrence increases verbosity 1 level through ERROR-WARNING-INFO-DEBUG')
    # parser.add_option('-o', '--output', dest='output',
    #                 help='location to store output')
    # parser.add_option('-I', '--include-path', dest="includes",
    #                 action="append", help="include path")                            
    (options, args) = parser.parse_args()


    # make options a globally accessible structure, e.g. OPTS.
    global OPTS
    OPTS = options


    if options.self_test:
        unittest.main()
        return 0


    levels = [logging.ERROR, logging.WARN, logging.INFO, logging.DEBUG]
    logging.basicConfig(level = levels[min(3,options.verbosity)])


    if not args:
        parser.error( 'incorrect arguments, try -h or --help.' )
        return 1


    # FIXME main logic
      
    return 0




if __name__=='__main__':
    sys.exit(main())
