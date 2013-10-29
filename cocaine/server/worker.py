#
#    Copyright (c) 2011-2013 Anton Tyurin <noxiouz@yandex.ru>
#    Copyright (c) 2011-2013 Other contributors as noted in the AUTHORS file.
#
#    This file is part of Cocaine.
#
#    Cocaine is free software; you can redistribute it and/or modify
#    it under the terms of the GNU Lesser General Public License as published
#    by the Free Software Foundation; either version 3 of the License, or
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

import socket
import sys
import traceback
import types

from ..asio import ev
from ..asio.pipe import Pipe
from ..asio.stream import ReadableStream, WritableStream, Decoder
from ..concurrent import Deferred
from ..logging import core as log
from ..protocol.message import Message, RPC

from .request import Request
from .response import Response
from .sandbox import Sandbox


class Worker(object):

    def __init__(self, init_args=None, disown_timeout=2, heartbeat_timeout=20):
        self._init_endpoint(init_args or sys.argv)

        self.sessions = dict()
        self.sandbox = Sandbox()

        self.loop = ev.Loop()

        self.disown_timer = ev.Timer(self.on_disown, disown_timeout, self.loop)
        self.heartbeat_timer = ev.Timer(self.on_heartbeat, heartbeat_timeout, self.loop)
        self.heartbeat_timer.start()

        if isinstance(self.endpoint, types.TupleType) or isinstance(self.endpoint, types.ListType):
            if len(self.endpoint) == 2:
                socket_type = socket.AF_INET
            elif len(self.endpoint) == 4:
                socket_type = socket.AF_INET6
            else:
                raise ValueError('invalid endpoint')
        elif isinstance(self.endpoint, types.StringType):
            socket_type = socket.AF_UNIX
        else:
            raise ValueError('invalid endpoint')
        sock = socket.socket(socket_type)
        self.pipe = Pipe(sock)
        self.pipe.connect(self.endpoint, blocking=True)
        self.loop.bind_on_fd(self.pipe.fileno())

        self.decoder = Decoder()
        self.decoder.bind(self.on_message)

        self.w_stream = WritableStream(self.loop, self.pipe)
        self.r_stream = ReadableStream(self.loop, self.pipe)
        self.r_stream.bind(self.decoder.decode)

        self.loop.register_read_event(self.r_stream._on_event,
                                      self.pipe.fileno())
        log.debug("Worker with %s send handshake" % self.id)
        # Send both messages - to run timers properly. This messages will be sent
        # only after all initialization, so they have same purpose.
        self._send_handshake()
        self._send_heartbeat()

    def _init_endpoint(self, init_args):
        try:
            self.id = init_args[init_args.index("--uuid") + 1]
            # app_name = init_args[init_args.index("--app") + 1]
            self.endpoint = init_args[init_args.index("--endpoint") + 1]
        except Exception as err:
            log.error("Wrong cmdline arguments: %s " % err)
            raise RuntimeError("Wrong cmdline arguments")

    def run(self, binds=None):
        if not binds:
            binds = {}
        for event, name in binds.iteritems():
            self.on(event, name)
        self.loop.run()

    def terminate(self, errno, reason):
        self.w_stream.write(Message(RPC.TERMINATE, 0, errno, reason).pack())
        self.loop.stop()
        exit(1)

    # Event machine
    def on(self, event, callback):
        self.sandbox.on(event, callback)

    # Events
    def on_heartbeat(self):
        self._send_heartbeat()

    def on_message(self, args):
        msg = Message.initialize(args)
        if msg is None:
            return
        elif msg.id == RPC.INVOKE:
            deferred = Deferred()
            request = Request(deferred)
            response = Response(msg.session, self)
            try:
                self.sandbox.invoke(msg.event, request, response)
                self.sessions[msg.session] = deferred
            except (ImportError, SyntaxError) as err:
                response.error(2, "unrecoverable error: %s " % str(err))
                self.terminate(1, "Bad code")
            except Exception as err:
                log.error("On invoke error: %s" % err)
                traceback.print_stack()
                response.error(1, "Invocation error")
        elif msg.id == RPC.CHUNK:
            log.debug("Receive chunk: %d" % msg.session)
            try:
                _session = self.sessions[msg.session]
                _session.trigger(msg.data)
            except Exception as err:
                log.error("On push error: %s" % str(err))
                self.terminate(1, "Push error: %s" % str(err))
                return
        elif msg.id == RPC.CHOKE:
            log.debug("Receive choke: %d" % msg.session)
            _session = self.sessions.get(msg.session, None)
            if _session is not None:
                _session.close()
                self.sessions.pop(msg.session)
        elif msg.id == RPC.HEARTBEAT:
            log.debug("Receive heartbeat. Stop disown timer")
            self.disown_timer.stop()
        elif msg.id == RPC.TERMINATE:
            log.debug("Receive terminate. %s, %s" % (msg.errno, msg.reason))
            self.terminate(msg.errno, msg.reason)
        elif msg.id == RPC.ERROR:
            _session = self.sessions.get(msg.session, None)
            if _session is not None:
                _session.error(Exception(msg.reason))

    def on_disown(self):
        log.error("Disowned")
        self.loop.stop()

    # Private:
    def _send_handshake(self):
        self.w_stream.write(Message(RPC.HANDSHAKE, 0, self.id).pack())

    def _send_heartbeat(self):
        self.disown_timer.start()
        log.debug("Send heartbeat. Start disown timer")
        self.w_stream.write(Message(RPC.HEARTBEAT, 0).pack())

    def _send_choke(self, session):
        self.w_stream.write(Message(RPC.CHOKE, session).pack())

    def _send_chunk(self, session, data):
        self.w_stream.write(Message(RPC.CHUNK, session, data).pack())

    def _send_error(self, session, code, msg):
        self.w_stream.write(Message(RPC.ERROR, session, code, msg).pack())
