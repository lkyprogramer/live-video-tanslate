"""Use the same completion-driven schedule for causal simulation and real PCM pacing."""
from concurrent.futures import ThreadPoolExecutor
from heapq import heappop, heappush
from itertools import count
from queue import Empty, Queue
from time import monotonic_ns


class Schedule:
    def __init__(self, *, realtime=False, elapsed_override=None, shared_lanes=None):
        self.realtime = realtime
        self.elapsed_override = elapsed_override
        self.shared_lanes = shared_lanes or {}
        self.anchor = monotonic_ns()
        self.now = 0
        self.events = []
        self.order = count()
        self.lanes = {}
        self.ready = Queue()

    def at(self, time_ns, callback, priority=2):
        heappush(self.events, (time_ns, priority, next(self.order), callback))

    def work(self, function, value, complete, lane):
        if not self.realtime:
            start = monotonic_ns()
            result = function(value)
            delta = monotonic_ns() - start
            if self.elapsed_override is not None:
                delta = self.elapsed_override(lane, value)
            self.at(self.now + max(1, delta), lambda: complete(result), priority=1)
            return
        executor = self.shared_lanes.get(lane)
        if executor is None:
            if lane not in self.lanes:
                self.lanes[lane] = ThreadPoolExecutor(max_workers=1, thread_name_prefix="a1-" + lane)
            executor = self.lanes[lane]
        future = executor.submit(function, value)
        future.add_done_callback(lambda f: self.ready.put((monotonic_ns()-self.anchor, lambda: complete(f.result()))))

    def run(self, until):
        try:
            while not until():
                if self.realtime:
                    while True:
                        try:
                            at, callback = self.ready.get_nowait()
                        except Empty:
                            break
                        self.at(at, callback, priority=1)
                    now = monotonic_ns() - self.anchor
                    if not self.events or self.events[0][0] > now:
                        timeout = (self.events[0][0] - now)/1e9 if self.events else 10
                        try:
                            at, callback = self.ready.get(timeout=timeout)
                        except Empty:
                            if not self.events:
                                raise RuntimeError("No worker completion or scheduled event") from None
                            continue
                        self.at(at, callback, priority=1)
                        continue
                elif not self.events:
                    raise RuntimeError("No scheduled event")
                at, _, _, callback = heappop(self.events)
                self.now = max(self.now, at, monotonic_ns()-self.anchor if self.realtime else 0)
                callback()
        finally:
            for executor in self.lanes.values():
                executor.shutdown(wait=True)
