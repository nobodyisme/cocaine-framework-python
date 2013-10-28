# encoding: utf-8
#
#    Copyright (c) 2011-2013 Anton Tyurin <noxiouz@yandex.ru>
#    Copyright (c) 2011-2013 Other contributors as noted in the AUTHORS file.
#
#    This file is part of Cocaine.
#
#    Cocaine is free software; you can redistribute it and/or modify
#    it under the terms of the GNU Lesser General Public License as published by
#    the Free Software Foundation; either version 3 of the License, or
#    (at your option) any later version.
#
#    Cocaine is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
#    GNU Lesser General Public License for more details.
#
#    You should have received a copy of the GNU Lesser General Public License
#    along with this program. If not, see <http://www.gnu.org/licenses/>.
#

from cocaine.decorators import default
from cocaine.logging.log import core_log


class Sandbox(object):

    def __init__(self):
        self._events = dict()
        self._logger = core_log

    def invoke(self, event_name, request, stream):
        """ Connect worker and decorator """
        event_closure = self._events.get(event_name, None)
        if event_closure is not None:
            event_handler = event_closure()
            event_handler.invoke(request, stream)
        else:
            self._logger.warn("there is no handler for event %s" % event_name)
            #todo: define magic constants
            stream.error(-100, "there is no handler for event %s" % event_name)

    def on(self, event_name, event_handler):
        try:
            # Try to construct handler.
            closure = event_handler()
        except Exception:
            # If this callable object is not our wrapper - may raise Exception
            closure = default(event_handler)()
            if hasattr(closure, "_wrapped"):
                event_handler = default(event_handler)
        else:
            if not hasattr(closure, "_wrapped"):
                event_handler = default(event_handler)
        self._events[event_name] = event_handler