from web import BaseRequestHandler
from scheduler import mark_async, sched


class HomeHandler(BaseRequestHandler):

    @mark_async
    def get(self):
        yield sched.sleep(5)
        self.write("hello world")

    @mark_async
    def post(self):
        response = {
            "client_ip": self.request.client_ip,
            "client_port": self.request.client_port,
            "path": self.request.path,
            "method": self.request.method,
            "headers": self.request.headers,
            "params": self.params,
        }
        self.write_json(response)

    @mark_async
    def put(self):
        1 / 0
        self.write_json({})


class DefaultHandler(BaseRequestHandler):

    @mark_async
    def method(self):
        self.raise_404()


router_config = [
    ("/", HomeHandler),
]

def router(path):
    for item in router_config:
        if path == item[0]:
            return item[1]
    return DefaultHandler
