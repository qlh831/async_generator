import socket
import selectors
import functools
import time

from scheduler import mark_async, MimicFuture
from web import Request, BaseRequestHandler
from log import app_log


sel = selectors.DefaultSelector()

class NonBlockingIO(object):

    def __init__(self, scheduler):
        self.scheduler = scheduler
        self.router = None

    def start_server(self, host, port, router):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind((host, port))
        sock.listen()
        sock.setblocking(False)
        sel.register(sock, selectors.EVENT_READ, self._on_new_connect)
        self.router = router
        app_log.info("Start listen on {}:{}".format(host, port))

    def _on_new_connect(self, server_sock, _):
        conn, _ = server_sock.accept()
        conn.setblocking(False)
        sel.register(conn, selectors.EVENT_READ, self._on_message)

    def _on_message(self, conn, _):
        sel.unregister(conn)
        self.scheduler.schedule_gen(self._handle_request(conn))

    @mark_async
    def _handle_request(self, conn):
        try:
            data = conn.recv(1024)
            if data:
                request = Request(conn, data)
                handler_class = self.router(request.path)
                yield handler_class(request).do_handle()
        finally:
            conn.close()

    @mark_async
    def request(self, host, port, path, method):

        # 仅支持：明文ip，明文端口，路径，方法
        # 不支持：https，dns，自定义header，自定义body，timeout 等

        start_time = time.time()

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM, 0)
        sock.setblocking(False)
        try:
            sock.connect((host, port))
        except BlockingIOError: pass

        future = MimicFuture()

        method = method.upper()
        message = "\r\n".join([
            "{} {} HTTP/1.1".format(method, path),
            "Host: {}:{}".format(host, port),
            "",
            "",
            "",
        ]).encode("utf-8")
        request = {
            "host": host,
            "port": port,
            "path": path,
            "method": method,
            "message": message,
            "fut": future,
            "start_time": start_time,
        }

        sel.register(sock, selectors.EVENT_WRITE, functools.partial(self._on_connect_ok, request))

        result = yield future

        return result

    def _on_connect_ok(self, request, sock, mask):
        sel.unregister(sock)
        try:
            sock.send(request["message"])
        except Exception as e:
            sock.close()
            request["fut"].throw(type(e), e.args[1], e.__traceback__)
            return
        sel.register(sock, selectors.EVENT_READ, functools.partial(self._on_response, request))

    def _on_response(self, request, sock, mask):
        sel.unregister(sock)
        try:
            data = sock.recv(1024)
        except Exception as e:
            request["fut"].throw(type(e), e.args[1], e.__traceback__)
        else:
            request["fut"].send(data)
        finally:
            sock.close()
        cost = time.time() - request["start_time"]
        app_log.info("HTTP {} http://{}:{}{} {}ms".format(
            request["method"],
            request["host"],
            request["port"],
            request["path"],
            round(cost*1000, 1),
        ))

    @mark_async
    def select(self):
        while True:
            events = sel.select(timeout=-1)
            for key, mask in events:
                callback, sock, mask = key.data, key.fileobj, mask
                callback(sock, mask)
            yield


if __name__ == "__main__":

    from scheduler import sched

    nbio = NonBlockingIO(sched)
    for _ in range(100):
        request_gen = nbio.request("192.168.0.10", 8080, "/", "get")
        sched.schedule_gen(request_gen)
    sched.schedule_gen(nbio.select())
    sched.run_forever()
