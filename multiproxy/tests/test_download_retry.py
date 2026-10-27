"""A download that fails says the real reason and tries again by itself (download_retry.py, https_only.session), 2026-10-23.

A stand-in site on 127.0.0.1 answers the cases the real one did: a header longer than aiohttp's default 8,190-byte limit (the real cause of the director's failure),
403, 404, 429 with Retry-After, 503 then 200, a dropped connection, a timeout and a redirect that goes round in circles. No internet, no waiting (the waits are test-short)."""
import asyncio
import hashlib
import logging

import aiohttp
import pytest
import pytest_asyncio
from aiohttp import web

import download_retry as dr
import https_only
import local_models as lm
from voice_downloads import FileSpec, InternetSource

BODY = b"runner-bytes" * 1000
SHA = hashlib.sha256(BODY).hexdigest()
FAST = (0.01, 0.01, 0.01)


class Site:
    def __init__(self):
        self.hits = 0
        self.plan = []                 # one entry per hit: ("ok",) | ("status", n, headers) | ("drop",) | ("hang",) | ("loop",) | ("bigheader",)

    async def handler(self, request):
        i = self.hits
        self.hits += 1
        step = self.plan[min(i, len(self.plan) - 1)]
        kind = step[0]
        if kind == "status":
            return web.Response(status=step[1], headers=step[2] if len(step) > 2 else {})
        if kind == "drop":
            request.transport.close()
            return web.Response()
        if kind == "hang":
            await asyncio.sleep(5)
        if kind == "loop":
            return web.Response(status=302, headers={"Location": "/again"})
        headers = {"Content-Security-Policy": "default-src 'none'; " + "child-src github.githubassets.com " * 400} if kind == "bigheader" else {}
        return web.Response(body=BODY, headers=headers)


@pytest_asyncio.fixture
async def site():
    s = Site()
    app = web.Application()
    app.router.add_get("/{tail:.*}", s.handler)
    runner = web.AppRunner(app)
    await runner.setup()
    tcp = web.TCPSite(runner, "127.0.0.1", 0)
    await tcp.start()
    s.url = f"http://127.0.0.1:{tcp._server.sockets[0].getsockname()[1]}/file.zip?token=SECRET123"
    yield s
    await runner.cleanup()


async def _get(site, tmp_path, session_kw=None, redirects=False):
    """One whole download with the real code path, through the retry wrapper."""
    logs = []
    sleeps = []

    async def nap(sec):
        sleeps.append(sec)
    async with https_only.session(timeout=aiohttp.ClientTimeout(total=None, sock_connect=2, sock_read=1), **(session_kw or {})) as session:
        async def op():
            async with session.get(site.url, allow_redirects=redirects) as resp:
                if resp.status != 200:
                    raise dr.HttpStatus(resp.status, str(resp.url), dr.retry_after_seconds(resp.headers.get("Retry-After")))
                data = await resp.read()
            assert data == BODY
            return data
        try:
            return await dr.run(op, url=site.url, what="the runner software", device="MSI", file="file.zip", log=logs.append, sleep=nap, waits=FAST), logs, sleeps
        except dr.DownloadFailed as e:
            return e, logs, sleeps


def test_the_session_has_room_for_long_answer_headers():
    async def go():
        async with https_only.session() as s:
            return s._max_field_size if hasattr(s, "_max_field_size") else None
    assert asyncio.run(go()) in (None, https_only.MAX_HEADER_BYTES)


@pytest.mark.asyncio
async def test_a_header_longer_than_the_default_limit_works_with_the_download_session(site, tmp_path):
    site.plan = [("bigheader",)]
    got, logs, _ = await _get(site, tmp_path)
    assert got == BODY and not logs
    async with aiohttp.ClientSession() as plain:                     # and it is what failed before: the library's own default refuses it
        with pytest.raises(aiohttp.ClientError):
            async with plain.get(site.url) as r:
                await r.read()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,words", [(403, "answered 403: it refused the download"), (404, "answered 404: this file is not on the site"),
                                          (410, "answered 410.")])
async def test_a_refusal_says_what_the_site_said(site, tmp_path, status, words):
    site.plan = [("status", status)]
    err, logs, _ = await _get(site, tmp_path)
    assert isinstance(err, dr.DownloadFailed) and err.message.startswith("Couldn't download the runner software on MSI. ")
    assert words in err.message and "internet" not in err.message


@pytest.mark.asyncio
async def test_403_and_404_get_one_more_try_and_410_none(site, tmp_path):
    site.plan = [("status", 404)]
    await _get(site, tmp_path)
    assert site.hits == 2
    site.hits, site.plan = 0, [("status", 410)]
    await _get(site, tmp_path)
    assert site.hits == 1


@pytest.mark.asyncio
async def test_429_waits_the_sites_own_time_then_gives_up_after_the_tries(site, tmp_path):
    site.plan = [("status", 429, {"Retry-After": "7"})]
    err, logs, sleeps = await _get(site, tmp_path)
    assert site.hits == 4 and sleeps == [7.0, 7.0, 7.0]
    assert "answered 429: too many requests. Try again in a few minutes." in err.message


@pytest.mark.asyncio
async def test_503_then_200_just_works(site, tmp_path):
    site.plan = [("status", 503), ("ok",)]
    got, logs, sleeps = await _get(site, tmp_path)
    assert got == BODY and site.hits == 2 and sleeps == [0.01] and len(logs) == 1 and "trying again" in logs[0]


@pytest.mark.asyncio
async def test_5xx_that_never_ends_names_the_problem_on_their_end(site, tmp_path):
    site.plan = [("status", 502)]
    err, _, _ = await _get(site, tmp_path)
    assert "answered 502: the site has a problem on its end. Try again in a few minutes." in err.message and site.hits == 4


@pytest.mark.asyncio
async def test_a_dropped_connection_is_tried_again_and_a_dead_address_says_check_the_internet(site, tmp_path):
    site.plan = [("drop",), ("ok",)]
    got, _, _ = await _get(site, tmp_path)
    assert got == BODY and site.hits == 2
    e = aiohttp.ClientConnectionError("refused")                      # a connection that could not be made (a live dead port takes seconds to refuse on Windows)
    assert dr.reason_text(e, "https://github.com/x/y.zip", "MSI") == "Couldn't connect to github.com. Check the internet connection on MSI."
    assert dr.wait_for(e, 1) == dr.WAITS[0] and dr.wait_for(e, 4) is None


@pytest.mark.asyncio
async def test_a_site_that_never_answers_times_out_in_plain_words(site, tmp_path):
    site.plan = [("hang",)]
    err, _, _ = await _get(site, tmp_path)
    assert err.message.endswith("127.0.0.1 didn't answer in time.") and site.hits == 4


@pytest.mark.asyncio
async def test_the_log_line_has_the_facts_and_never_the_signed_query(site, tmp_path):
    site.plan = [("status", 503)]
    _, logs, _ = await _get(site, tmp_path)
    assert logs and all("SECRET123" not in x and "token" not in x for x in logs)
    assert "MSI" in logs[0] and "503" in logs[0] and "127.0.0.1" in logs[0] and "file.zip" in logs[0] and "try 1" in logs[0]
    assert dr.strip_query("https://x.example/a/b.zip?sig=abc#f") == "https://x.example/a/b.zip"


def test_a_redirect_that_cannot_be_followed_has_its_own_words():
    e = https_only.RedirectRefused("x")
    assert dr.reason_text(e, "https://github.com/a/b.zip", "MSI") == "GitHub sent the download somewhere it could not follow."
    assert dr.wait_for(e, 1) is None


def test_the_site_is_named_and_unknown_statuses_and_other_errors_pass_through():
    assert dr.site_name("release-assets.githubusercontent.com") == "GitHub" and dr.site_name("cdn-lfs.huggingface.co") == "Hugging Face" and dr.site_name("example.org") == "example.org"
    assert dr.reason_text(dr.HttpStatus(418, "https://github.com/x"), "https://github.com/x", "MSI") == "GitHub answered 418."
    assert dr.kind_of(lm.LocalModelError(502, "damaged")) == "other" and dr.kind_of(OSError("disk full")) == "other"


@pytest.mark.asyncio
async def test_a_bad_checksum_is_not_tried_again():
    calls = []

    async def op():
        calls.append(1)
        raise lm.LocalModelError(502, "didn't pass its safety check")
    with pytest.raises(lm.LocalModelError):
        await dr.run(op, url="https://x/y", what="w", device="d", waits=FAST)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_the_main_devices_own_download_source_raises_the_whole_plain_line(site, tmp_path):
    site.plan = [("status", 429)]
    spec = FileSpec("file.zip", site.url, len(BODY), SHA)
    logs = []
    src = InternetSource(device="this device", log=logs.append, what="the runner software", sleep=lambda s: asyncio.sleep(0))
    with pytest.raises(lm.LocalModelError) as ei:
        await src.fetch(spec, tmp_path / "file.zip", lambda n: None)
    assert ei.value.message == "Couldn't download the runner software on this device. GitHub answered 429: too many requests. Try again in a few minutes." or \
        ei.value.message.startswith("Couldn't download the runner software on this device. 127.0.0.1 answered 429")
    assert site.hits == 4 and logs


def test_the_install_error_keeps_the_real_reason_and_no_longer_guesses_the_internet():
    import decision_engine as de
    line = "Couldn't download the runner software on MSI. GitHub answered 429: too many requests. Try again in a few minutes."
    assert de.plain_install_error(line) == line
    assert "Anything already downloaded is saved" not in dr.full_text(dr.HttpStatus(503, "https://github.com/x"), "https://github.com/x", "the runner software", "MSI")
