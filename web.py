import json
import datetime
from urllib.parse import unquote

from scheduler import mark_async
from log import app_log


class HttpParseError(Exception): pass


class Request(object):

    def __init__(self, conn, data):
        self.conn = conn
        self.data = data

        peername = self.conn.getpeername()
        self.client_ip = peername[0]
        self.client_port = peername[1]

        self.method = None
        self.path = None
        self.http_version = None
        self.headers = {}
        self.raw_body = None
        self.query_string = None
        self.params = {}

        self.parse()

    def parse(self):
        try:
            body_idx = self.data.find(b"\r\n\r\n")
            self.raw_body = self.data[body_idx+4:]
            lines = self.data[:body_idx].decode("utf-8").split("\r\n")

            parts = lines[0].split(" ")
            self.method = parts[0].lower().strip()
            full_path = parts[1].strip()
            self.http_version = parts[2].strip()

            for line in lines[1:]:
                line = line.strip()
                sep_idx = line.find(":")
                key = line[:sep_idx].strip()
                value = line[sep_idx+1:].strip()
                self.headers[key] = value

            query_idx = full_path.find("?")
            if query_idx >= 0:
                query_string = full_path[query_idx+1:]
                kevs = query_string.split("&")
                query_string_params = {}
                for kev in kevs:
                    k, v = kev.strip().split("=")
                    query_string_params[k] = unquote(v)
                self.params.update(query_string_params)
                self.path = full_path[:query_idx]
                self.query_string = query_string
            else:
                self.path = full_path
        except:
            raise HttpParseError("http parse error")
        if not all([self.method, self.path, self.http_version]):
            raise HttpParseError("http parse error")


class BaseRequestHandler(object):

    def __init__(self, request):
        self.request = request
        self.params = {}

        self.start_handle_time = None
        self.finished = False

        self.status_code = 200
        self.headers = {}
        self.body = b""

    @mark_async
    def before_handle(self):
        self.params = self.request.params
        if self.request.headers.get("Content-Type", "").startswith("application/json"):
            body_params = json.loads(self.request.raw_body.decode("utf-8"))
            self.params.update(body_params)
        self.set_default_headers()

    @mark_async
    def do_handle(self):
        self.start_handle_time = datetime.datetime.now().timestamp()
        yield self.before_handle()
        yield self.method()
        yield self.after_handle()

    @mark_async
    def method(self):
        try:
            func = getattr(self, self.request.method, self.raise_405)
            yield func()
        except Exception as e:
            app_log.exception(e)
            self.raise_500()

    @mark_async
    def after_handle(self):
        cost = datetime.datetime.now().timestamp() - self.start_handle_time
        app_log.info("{} {} {} ({}) {}ms".format(
            self.request.method.upper(),
            self.status_code,
            self.request.path,
            self.request.client_ip,
            round(cost*1000, 1))
        )

    def set_default_headers(self):
        self.headers = {
            "Server": "qlh/0.1",
            "Content-Type": "application/json",
            "Date": datetime.datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT"),
            "Timestamp": int(datetime.datetime.now().timestamp()),
        }

    def set_header(self, key, value):
        self.headers[key] = value

    def set_status_code(self, status_code):
        self.status_code = status_code

    def write(self, seq):
        if isinstance(seq, str):
            seq = seq.encode("utf-8")
        self.body = seq
        self.finish()

    def write_json(self, json_dict):
        self.body = json.dumps(json_dict).encode("utf-8")
        self.finish()

    def finish(self):
        if self.finished:
            return
        self.finished = True
        lines = []
        lines.append("HTTP/1.1 {} {}".format(
            self.status_code,
            code_to_text(self.status_code))
        )
        if self.body:
            self.set_header("Content-Length", len(self.body))
        for key in sorted(list(self.headers.keys())):
            lines.append("{}: {}".format(key, self.headers[key]))

        message = "\r\n".join(lines).encode("utf-8")
        message = message + b"\r\n\r\n" + self.body + b"\r\n"
        self.request.conn.send(message)

    def raise_404(self):
        self.set_status_code(404)
        self.write("404 Not Found")

    def raise_405(self):
        self.set_status_code(405)
        self.write("405 Not Allowed")

    def raise_500(self):
        self.set_status_code(500)
        self.write("500 Internal Server Error")


def code_to_text(status_code):
    _map = {
        200: "OK",
        201: "Created",
        202: "Accepted",
        204: "No Content",
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not Found",
        405: "Method Not Allowed",
        500: "Internal Server Error",
        501: "Not Implemented",
    }
    return _map[status_code]
