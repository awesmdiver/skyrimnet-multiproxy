"""Security review 4.3.0, S1: every download stops while it runs, and keeps nothing, when a server sends more than the size it promised.

Stand-ins only: a real server on 127.0.0.1 that sends far too much (never the real internet), and a fake session for `download_file`."""
import asyncio
import hashlib
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace

import aiohttp
import pytest
from aiohttp import web

import benchmarks_remote_client
import claude_courier
import copilot_courier
import local_models

MB = 1024 * 1024


class Flood:
    """A server that answers every request with `total` bytes, counting how many it managed to send before the client hung up."""

    def __init__(self, total):
        self.total, self.sent = total, 0
        self.loop = asyncio.new_event_loop()
        ready = threading.Event()

        async def handle(request):
            resp = web.StreamResponse()
            await resp.prepare(request)
            try:
                for _ in range(self.total // MB):
                    await resp.write(b"x" * MB)
                    self.sent += MB
            except (ConnectionError, asyncio.CancelledError):
                pass
            return resp

        async def start():
            app = web.Application()
            app.router.add_get("/{tail:.*}", handle)
            self.runner = web.AppRunner(app)
            await self.runner.setup()
            site = web.TCPSite(self.runner, "127.0.0.1", 0)
            await site.start()
            self.url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/file"
            ready.set()

        threading.Thread(target=lambda: (asyncio.set_event_loop(self.loop), self.loop.run_until_complete(start()), self.loop.run_forever()), daemon=True).start()
        ready.wait(5)

    def close(self):
        asyncio.run_coroutine_threadsafe(self.runner.cleanup(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)


@pytest.fixture
def flood():
    f = Flood(100 * MB)
    yield f
    f.close()


def _fake_get(chunks):
    class Resp:
        status = 200
        headers = {}
        url = "https://example.invalid/x"

        class content:
            @staticmethod
            async def iter_chunked(n):
                for c in chunks:
                    yield c

    @asynccontextmanager
    async def get(session, url, headers=None, **kw):
        yield Resp()
    return get


@pytest.mark.asyncio
async def test_download_file_stops_at_the_promised_size_and_keeps_nothing(tmp_path, monkeypatch):
    sent = []

    def many():
        for _ in range(200):                                  # 200 MB for a file promised at 1,000 bytes
            sent.append(1)
            yield b"x" * MB
    monkeypatch.setattr(local_models.https_only, "get", _fake_get(many()))
    dest = tmp_path / "m.gguf"
    with pytest.raises(local_models.LocalModelError) as e:
        await local_models.download_file(None, "https://example.invalid/x", dest, 1000, "0" * 64, lambda n: None)
    assert "bigger than it should be" in e.value.message
    assert len(sent) <= 2                                     # stopped at the first chunk past the size, not read to the end
    assert not dest.exists() and not dest.with_name(dest.name + ".part").exists()


@pytest.mark.asyncio
async def test_download_file_still_takes_the_right_file_and_a_resumed_one(tmp_path, monkeypatch):
    data = b"abc" * 5000
    sha = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(local_models.https_only, "get", _fake_get([data[:7000], data[7000:]]))
    dest = tmp_path / "ok.bin"
    seen = []
    await local_models.download_file(None, "https://example.invalid/x", dest, len(data), sha, seen.append)
    assert dest.read_bytes() == data and sum(seen) == len(data)
    # a file that is exactly the promised size plus a little slack is still refused at the end (wrong checksum / size), never kept
    monkeypatch.setattr(local_models.https_only, "get", _fake_get([data + b"y" * 100]))
    dest2 = tmp_path / "slack.bin"
    with pytest.raises(local_models.LocalModelError):
        await local_models.download_file(None, "https://example.invalid/x", dest2, len(data), sha, lambda n: None)
    assert not dest2.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("module,host_cls,err", [(claude_courier, "ClaudeHost", claude_courier.ClaudeError), (copilot_courier, "CopilotHost", copilot_courier.CopilotError)])
async def test_the_claude_and_copilot_installers_stop_too(tmp_path, flood, module, host_cls, err):
    job = module.Install()
    job.total = 1000
    fake_self = SimpleNamespace(changed=lambda: None)
    dest = tmp_path / "prog.part"
    with pytest.raises(err):
        await getattr(module, host_cls)._download(fake_self, flood.url, dest, job)
    await asyncio.sleep(0.2)
    assert flood.sent < 10 * MB, f"the server managed to send {flood.sent // MB} MB before the client stopped"
    assert not dest.exists()


@pytest.mark.asyncio
async def test_a_result_file_from_a_courier_stops_too(tmp_path, flood):
    client = benchmarks_remote_client.BenchmarkClient.__new__(benchmarks_remote_client.BenchmarkClient)
    client.base, client.clock, client.last_contact = flood.url.rsplit("/file", 1)[0], __import__("time").monotonic, 0
    client._headers = lambda: {}
    dest = tmp_path / "result.part"
    import courier_client
    orig = courier_client._no_redirect
    courier_client._no_redirect = lambda r: None
    try:
        with pytest.raises(benchmarks_remote_client.RemoteError) as e:
            await client.download("run", "file", dest, 1000)
    finally:
        courier_client._no_redirect = orig
    assert e.value.code == "too_big" and not dest.exists()
    assert flood.sent < 10 * MB
