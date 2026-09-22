from zvagent.remote import RemoteDesktopServer


class _ThreadStub:
    def __init__(self, *, target, name, daemon):
        self.target = target
        self.name = name
        self.daemon = daemon
        self.started = False

    def start(self):
        self.started = True

    def is_alive(self):
        return self.started


def test_background_start_leaves_running_transition_to_server_loop(monkeypatch):
    created = []

    def make_thread(**kwargs):
        thread = _ThreadStub(**kwargs)
        created.append(thread)
        return thread

    monkeypatch.setattr("zvagent.remote.threading.Thread", make_thread)
    server = RemoteDesktopServer(host="127.0.0.1", port=9000)
    server.start()

    assert server.running is False
    assert len(created) == 1
    assert created[0].target == server.serve_blocking
    assert created[0].name == "rdp-server"
    assert created[0].daemon is True

    server.start()
    assert len(created) == 1
