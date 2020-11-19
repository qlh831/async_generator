import time
from collections import OrderedDict

from log import app_log


class _GeneratorControlBlock(object):
    def __init__(self, gen, parent=None):
        self.id = id(gen)
        self.gen = gen
        self.val = None
        self.parent = parent
        self.wait_until = 0


class _GeneratorWait(object):
    def __init__(self, seconds):
        self.seconds = seconds


class _GeneratorScheduler(object):

    def __init__(self):
        self._gcbs = OrderedDict()

    def timestamp(self):
        return time.time()

    def sleep(self, seconds):
        return _GeneratorWait(seconds)

    def _schedule_gen(self, gen, parent=None):
        gcb = _GeneratorControlBlock(gen, parent=parent)
        self._gcbs[gcb.id] = gcb

    def schedule_gen(self, gen):
        self._schedule_gen(gen)

    def resume_gcb(self, gcb, val):
        gcb.val = val
        self._gcbs[gcb.id] = gcb

    def run_forever(self):
        while True:
            if not self._gcbs:
                break
            gcbs = tuple(self._gcbs.values())
            for gcb in gcbs:
                if gcb.wait_until > self.timestamp():
                    continue
                try:
                    if isinstance(gcb.val, Exception):
                        _val = gcb.gen.throw(gcb.val)
                    else:
                        _val = gcb.gen.send(gcb.val)
                except StopIteration as e:
                    if gcb.parent is not None:
                        gcb.parent.val = e.value
                        self._gcbs[gcb.parent.id] = gcb.parent
                    del self._gcbs[gcb.id]
                    continue
                except Exception as e:
                    if gcb.parent is not None:
                        gcb.parent.val = e
                        self._gcbs[gcb.parent.id] = gcb.parent
                    else:
                        app_log.exception(e)
                    del self._gcbs[gcb.id]
                    continue
                if isinstance(_val, _GeneratorWait):
                    gcb.wait_until = self.timestamp() + _val.seconds
                    gcb.val = None
                elif hasattr(_val, "_is_future"):
                    del self._gcbs[gcb.id]
                    _val.send(gcb)
                elif hasattr(_val, "__next__"):
                    del self._gcbs[gcb.id]
                    self._schedule_gen(_val, parent=gcb)
                else:
                    gcb.val = _val


sched = _GeneratorScheduler()


class MimicFuture(object):
    def __init__(self):
        g = self.mimic()
        g.send(None)
        self.gen = g
        self.result = None

    def mimic(self):
        yield
        parent = yield
        try:
            self.result = yield
        except Exception as e:
            self.result = e
        sched.resume_gcb(parent, self.result)

    def send(self, val):
        self.gen.send(val)

    def throw(self, *args, **kwargs):
        self.gen.throw(*args, **kwargs)


def mark_async(func):

    def generator_wrapper(val):
        yield
        return val

    def wrapper(*args, **kwargs):
        gen = func(*args, **kwargs)
        if not hasattr(gen, "__next__"):
            gen = generator_wrapper(gen)
        return gen

    return wrapper
