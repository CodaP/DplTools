#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
dplkit.role.librarian
~~~~~~~~~~~~~~~~~~~~~

librarian(search-criteria) -> [asset-uri, asset-uri...]

:copyright: 2012 by University of Wisconsin Regents, see AUTHORS for more details
:license: GPLv3, see LICENSE for more details
"""

import os, sys
import logging
from exceptions import Exception
from abc import ABCMeta, abstractmethod
from dplkit.role.decorator import has_requires,has_provides

LOG = logging.getLogger(__name__)


class AmbiguousQueryError(Exception):
    """Exception saying that a given query doesn't resolve to an un-ambiguous sequence of assets
    """
    pass

@has_requires
@has_provides
class aLibrarian(object):
    """A Librarian returns sets of media asset URIs when given search expressions.
    """
    __metaclass__ = ABCMeta

    def __init__(self, *args, **kwargs):
        """
        """
        #super(self.__class__, self).__init__()

    @abstractmethod
    def search(self, *where_exprs, **key_values):
        """
        Yield time-ordered sequence of dictionaries satisfying search conditions
        Each dictionary should have a 'uri' key compatible with being passed to a zookeeper
        where_exprs is a series of string expressions (SQL-conformant) of which all must be satisfied
        key_values is a dictionary of asset attributes that must match
        key_values can also include lambda expressions returning true/false on the key in question
        Admissible keys should be a subset of librarian's meta.keys().
 
        example for a simple system with one librarian, one zookeeper, one narrator class::

            assets = mylibrarian(start=start_time, end=end_time)
            for asset_info in assets:
                media_info = myzookeeper(**asset_info)
                frames = mynarrator(**media_info)
                for frame in frames:
                    process(frame)

        """
        pass

    def __call__(self, *args, **kwargs):
        """
        default action for a librarian is to search()
        """
        return self.search(*args, **kwargs)


def test():
    """ """
    pass


if __name__=='__main__':
    test()
