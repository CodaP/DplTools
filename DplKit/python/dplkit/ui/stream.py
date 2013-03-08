#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
dplkit.ui.stream
~~~~~~~~~~~~~~~~~~

DPL UI module for stream objects

"""
__docformat__ = "restructuredtext en"

from PyQt4 import QtCore,QtGui

import os
import sys
import logging
from time import sleep

LOG = logging.getLogger(__name__)

class _StreamThread(QtCore.QThread):
    """Patched QThread object.
    Some older versions of PyQt4 don't have QThread default to calling
    exec_() in the thread's run method.

    The call to exec_() is needed to start the event loop of this thread,
    allowing signals/slots to work properly.
    """
    def run(self):
        self.exec_()

class _StreamObject(QtCore.QObject):
    """Object that holds the code performed by the `Stream` object in the
    new thread.
    """
    finished = QtCore.pyqtSignal()
    frame_ready = QtCore.pyqtSignal(object)

    def __init__(self, iterable=None, callable=None, parent=None,
            delay=None):
        super(_StreamObject, self).__init__(parent)

        if iterable is not None and callable is not None:
            raise ValueError("Either an iterable or a callable should be provided not both")
        elif iterable is not None:
            self.iterable = iterable
            self.callable = None
        else:
            self.iterable = None
            self.callable = callable

        self.delay = delay if delay is None else float(delay)

    def run(self):
        """Iterate through the iterable and signal when new frames are ready.
        """
        # If we were given a callable get the iterable
        if self.iterable is None:
            try:
                self.iterable = self.callable()
            except StandardError:
                self.finished.emit()
                raise

        # Iterate over the stream and signal each new frame
        try:
            for frame in self.iterable:
                self.frame_ready.emit(frame)

                # If the user added artificial delay
                if self.delay is not None: sleep(self.delay)

        except StandardError:
            raise
        finally:
            self.finished.emit()

class Stream(QtCore.QObject):
    """An object encapsulating a DPL stream for use in a GUI.
    A `Stream` takes the provided iterable and provides each element
    via a Qt Signal. This iteration happens in a separate Qt Thread to
    not block the GUI thread. A `Stream` expects the iterable to provide
    a DPL frame (as a python dictionary), although this object may work
    with any object type.

    A Qt Application must be initialized before using a `Stream` so that
    signals and thread communication operates properly.

    FUTURE: Add a Stream subclass (or other) that can be configured by the
            GUI (text boxes, buttons, checkboxes, etc.). Will probably need
            a new type of controller or something.
    """
    # Classes used
    _stream_object_class = _StreamObject
    _stream_thread_class = _StreamThread

    def __init__(self, iterable=None, callable=None, parent=None,
            delay=None,
            exit_app_on_complete=False):
        """Initialize a stream object.
        Allocate a QThread handle, but don't start it. An iterable or
        callable can be provided and will be iterated over in the separate
        thread. If an iterable is provided it will be passed to the new
        thread and iterated over once the `start` method is called. If a
        callable is provided it will be call with no arguments in the new
        thread's affinity once the `start` method is called.

        :keyword iterable: iterator or generator or other iterable object
        :keyword callable: function that returns an interable object
        :keyword parent: Qt parent, read Qt documentation for more info
                         http://pyqt.sourceforge.net/Docs/PyQt4/qobject.html#QObject
        :keyword exit_app_on_complete: Set to true if you want the entire
                         QApplication (including GUI) to exit when this Stream
                         is done processing. Should only be used for testing
                         or in the rare case that no GUI is involved.
        :keyword delay: Add an artificial delay between each iteration (in seconds)
        """
        super(Stream, self).__init__(parent)
        self.thread_handle = self._stream_thread_class()
        self.worker        = self._stream_object_class(
                iterable=iterable,
                callable=callable,
                delay=delay
                )
        self.exit_app_on_complete = exit_app_on_complete

        self._connect_worker_to_thread()

    def _connect_worker_to_thread(self):
        """Connect signals between the worker object and the thread.
        """
        # First move the object to the thread's affinity
        self.worker.moveToThread(self.thread_handle)

        # Make it so the thread dies when the worker is done
        self.worker.finished.connect(self.thread_handle.quit)

        # Make it so the worker starts when the thread starts
        self.thread_handle.started.connect(self.worker.run)

        # If the user is using the stream by itself (no GUI) then
        # they probably want the app to stop exec when the Stream finishes
        if self.exit_app_on_complete:
            self.thread_handle.finished.connect(QtGui.qApp.exit)

        # Make workers frame ready signal public
        self.frame_ready = self.worker.frame_ready

    def start(self):
        self.thread_handle.start()

    def wait(self, msecs=None):
        if msecs is not None:
            return self.thread_handle.wait(msecs=msecs)
        else:
            return self.thread_handle.wait()

