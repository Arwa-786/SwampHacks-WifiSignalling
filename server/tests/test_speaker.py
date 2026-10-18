import time

from speaker import Speaker


class FakeProc:
    def __init__(self, log, text):
        self.text, self.log, self.done = text, log, False
        log.append(("start", text))

    def poll(self):
        return 0 if self.done else None

    def wait(self):
        while not self.done:
            time.sleep(0.01)

    def terminate(self):
        self.done = True
        self.log.append(("cut", self.text))


def make():
    log, procs = [], []
    def popen(cmd):
        p = FakeProc(log, cmd[0]); procs.append(p); return p
    return Speaker(lambda t: [t], popen=popen), log, procs


def settle():
    time.sleep(0.1)


def test_one_at_a_time_in_order():
    sp, log, procs = make()
    sp.say("scan"); sp.say("hide"); settle()
    assert log == [("start", "scan")]          # "hide" waits its turn
    procs[0].done = True; settle()
    assert log[-1] == ("start", "hide")


def test_urgent_cuts_in_and_drops_queue():
    sp, log, procs = make()
    sp.say("scan"); sp.say("hide"); settle()
    sp.say("Red light!", urgent=True); settle()
    assert ("cut", "scan") in log and log[-1] == ("start", "Red light!")
    assert ("start", "hide") not in log


def test_duplicate_ignored():
    sp, log, _ = make()
    assert sp.say("Green light!", urgent=True)
    assert not sp.say("Green light!", urgent=True)
