import logging
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import youtube
from youtube import _run_yt_dlp as run_ytdlp


WATCH_URL = "https://www.youtube.com/watch?v=abcdefghijk"
AUDIO_URL = "https://media.invalid/audio.m4a"
VIDEO_URL = "https://media.invalid/video.mp4"
PROVIDER_VIDEO_URL = "https://provider.invalid/stream/resolver/job-123"


@pytest.fixture(autouse=True)
def isolate_resolver(monkeypatch):
    monkeypatch.setattr(youtube, "API_TOKEN", "")
    monkeypatch.setattr(youtube, "YOUTUBE_API_KEYS", [])
    monkeypatch.setattr(youtube, "_api_fail_count", 0)
    monkeypatch.setattr(youtube, "_api_cooldown_until", 0)
    monkeypatch.setattr(youtube, "_SEARCH_CACHE", {})
    monkeypatch.setattr(youtube, "_STREAM_CACHE", {})
    monkeypatch.setattr(youtube, "_MEM_CACHE", {})
    monkeypatch.setattr(youtube, "_read_cache", AsyncMock(return_value=None))
    monkeypatch.setattr(youtube, "_write_cache", AsyncMock())
    monkeypatch.setattr(youtube, "check_stream_url", AsyncMock(return_value=None))
    monkeypatch.setattr(youtube, "get_media_proxy", AsyncMock(return_value="http://127.0.0.1:9876"))
    monkeypatch.setattr(youtube, "_get_remote_file_size", AsyncMock(return_value=None))
    monkeypatch.setattr(youtube, "_extract_direct_info_sync", lambda *args: None)
    monkeypatch.setattr(youtube, "_ytdlp_search_first_sync", lambda *args: None)
    monkeypatch.setattr(youtube, "_run_yt_dlp", AsyncMock(return_value=None))


def provider_response(**changes):
    # /info returns a muxed-video resolver job without codec/format fields.
    return {
        "query_type": "search",
        "query": "test song",
        "title": "Test song",
        "video_id": "abcdefghijk",
        "youtube_link": WATCH_URL,
        "stream_url": PROVIDER_VIDEO_URL,
        "duration": "03:00",
        "channel_name": "Test artist",
        "views": "1000",
        "thumbnail": "https://images.invalid/thumbnail.jpg",
        "suggested": [],
        "time_taken": "0.5 sec",
        **changes,
    }


def innertube_response(**changes):
    return {
        "title": "Test song",
        "video_id": "abcdefghijk",
        "youtube_link": WATCH_URL,
        "stream_url": VIDEO_URL,
        "duration": "03:00",
        **changes,
    }


@pytest.mark.asyncio
async def test_video_handler_preserves_provider_proxy_contract_without_local_extraction(monkeypatch):
    monkeypatch.setattr(youtube, "API_TOKEN", "resolver-test-token")
    client = MagicMock()
    client.get = AsyncMock(return_value=httpx.Response(200, json=provider_response()))
    monkeypatch.setattr(youtube, "get_http_client", lambda: client)
    fallback = AsyncMock(side_effect=AssertionError("Local extraction is unavailable"))
    monkeypatch.setattr(youtube, "resolve_innertube", fallback)

    result = await youtube.handle_youtube("test song", mode="video")

    assert result[7] == PROVIDER_VIDEO_URL
    fallback.assert_not_awaited()
    assert client.get.call_args.kwargs["params"] == {"q": "test song"}
    assert youtube._api_fail_count == 0


@pytest.mark.asyncio
async def test_video_stream_preserves_opaque_provider_proxy_contract(monkeypatch):
    monkeypatch.setattr(youtube, "API_TOKEN", "resolver-test-token")
    client = MagicMock()
    client.get = AsyncMock(return_value=httpx.Response(200, json=provider_response(query_type="url")))
    monkeypatch.setattr(youtube, "get_http_client", lambda: client)
    fallback = AsyncMock(side_effect=AssertionError("Local extraction is unavailable"))
    monkeypatch.setattr(youtube, "resolve_innertube", fallback)

    assert await youtube.get_video_stream(WATCH_URL) == PROVIDER_VIDEO_URL
    fallback.assert_not_awaited()
    assert client.get.call_args.kwargs["params"] == {"q": WATCH_URL}


@pytest.mark.asyncio
async def test_video_retry_resolver_rejects_audio_provider_response(monkeypatch):
    monkeypatch.setattr(youtube, "API_TOKEN", "resolver-test-token")
    client = MagicMock()
    client.get = AsyncMock(return_value=httpx.Response(200, json=provider_response(vcodec="none")))
    monkeypatch.setattr(youtube, "get_http_client", lambda: client)
    fallback = AsyncMock(return_value=innertube_response())
    monkeypatch.setattr(youtube, "resolve_innertube", fallback)

    assert await youtube.get_video_stream(WATCH_URL) == VIDEO_URL
    fallback.assert_awaited_once_with(WATCH_URL, mode="video")
    assert youtube._api_fail_count == 0


@pytest.mark.asyncio
async def test_audio_handler_preserves_positional_callback_api(monkeypatch):
    monkeypatch.setattr(youtube, "API_TOKEN", "resolver-test-token")
    client = MagicMock()
    client.get = AsyncMock(return_value=httpx.Response(200, json=provider_response(stream_url=AUDIO_URL)))
    monkeypatch.setattr(youtube, "get_http_client", lambda: client)
    callback = MagicMock()

    result = await youtube.handle_youtube("test song", "track-1", -100123, callback)

    assert result[7] == AUDIO_URL
    callback.assert_called_once()
    assert callback.call_args.args[:2] == ("track-1", -100123)


@pytest.mark.asyncio
async def test_video_handler_rejects_explicit_audio_only_provider_formats(monkeypatch):
    monkeypatch.setattr(youtube, "API_TOKEN", "resolver-test-token")
    client = MagicMock()
    client.get = AsyncMock(return_value=httpx.Response(200, json=provider_response(
        formats=[{"url": AUDIO_URL, "vcodec": "none", "acodec": "aac", "protocol": "https"}],
    )))
    monkeypatch.setattr(youtube, "get_http_client", lambda: client)
    fallback = AsyncMock(return_value=innertube_response())
    monkeypatch.setattr(youtube, "resolve_innertube", fallback)

    result = await youtube.handle_youtube("test song", mode="video")

    assert result[7] == VIDEO_URL
    fallback.assert_awaited_once_with("test song", mode="video")


@pytest.mark.asyncio
async def test_provider_with_explicit_video_codec_is_usable(monkeypatch):
    monkeypatch.setattr(youtube, "API_TOKEN", "resolver-test-token")
    client = MagicMock()
    client.get = AsyncMock(return_value=httpx.Response(200, json=provider_response(
        stream_url=VIDEO_URL, vcodec="h264", acodec="aac",
    )))
    monkeypatch.setattr(youtube, "get_http_client", lambda: client)
    fallback = AsyncMock()
    monkeypatch.setattr(youtube, "resolve_innertube", fallback)

    result = await youtube.get_video_info("test song", mode="video")

    assert result[6] == VIDEO_URL
    fallback.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_video_does_not_fall_back_to_known_audio_only_url(monkeypatch):
    monkeypatch.setattr(youtube, "_extract_direct_info_sync", lambda *args: {
        "url": AUDIO_URL, "duration": 180,
        "formats": [{"url": AUDIO_URL, "vcodec": "none", "acodec": "aac", "protocol": "https"}],
    })

    result = await youtube.get_video_details(AUDIO_URL, mode="video")

    assert "error" in result


@pytest.mark.asyncio
async def test_direct_video_prefers_muxed_format_over_audio_first(monkeypatch):
    monkeypatch.setattr(youtube, "_extract_direct_info_sync", lambda *args: {
        "url": AUDIO_URL, "duration": 180,
        "formats": [
            {"url": AUDIO_URL, "vcodec": "none", "acodec": "aac", "protocol": "https"},
            {"url": VIDEO_URL, "vcodec": "h264", "acodec": "aac", "protocol": "https", "ext": "mp4"},
        ],
    })

    result = await youtube.get_video_details(VIDEO_URL, mode="video")

    assert result["stream_url"] == VIDEO_URL


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol, extension", [("m3u8", "m3u8"), ("m3u8_native", "m3u8"), ("http_dash_segments", "mpd")])
async def test_direct_video_accepts_recorded_muxed_network_manifest(monkeypatch, protocol, extension):
    manifest_url = f"https://media.invalid/archive.{extension}"
    monkeypatch.setattr(youtube, "_extract_direct_info_sync", lambda *args: {
        "duration": 1800,
        "is_live": False,
        "live_status": "was_live",
        "formats": [{
            "format_id": "hls-360p", "url": manifest_url, "protocol": protocol,
            "ext": "mp4", "vcodec": "avc1.4d401e", "acodec": "mp4a.40.2", "height": 360,
        }],
    })

    result = await youtube.get_video_details(manifest_url, mode="video")

    assert result["stream_url"] == manifest_url
    assert result["duration"] == "30:00"


@pytest.mark.asyncio
async def test_ytdlp_video_fallback_rejects_audio_only_formats(monkeypatch):
    monkeypatch.setattr(youtube, "get_video_info", AsyncMock(return_value=(None,) * 9))
    monkeypatch.setattr(youtube, "_ytdlp_search_first_sync", lambda *args: {
        "title": "Audio only", "id": "abcdefghijk", "duration": 180,
        "formats": [{"url": AUDIO_URL, "vcodec": "none", "acodec": "aac", "protocol": "https"}],
    })

    result = await youtube.get_video_details("test song", mode="video")

    assert "error" in result


@pytest.mark.asyncio
@pytest.mark.parametrize("is_live, expected", [(False, "30:00"), (True, "Live Stream")])
async def test_archived_livestream_keeps_finite_duration(monkeypatch, is_live, expected):
    monkeypatch.setattr(youtube, "_post_innertube_async", AsyncMock(return_value={
        "playabilityStatus": {"status": "OK"},
        "videoDetails": {
            "title": "Broadcast", "lengthSeconds": "1800", "isLive": is_live, "isLiveContent": True,
        },
        "streamingData": {"formats": [{"url": VIDEO_URL, "height": 360, "mimeType": "video/mp4"}]},
    }))

    result = await youtube.resolve_innertube(WATCH_URL)

    assert result["duration"] == expected


@pytest.mark.asyncio
async def test_archived_direct_manifest_keeps_finite_duration(monkeypatch):
    url = "https://media.invalid/archive.m3u8"
    monkeypatch.setattr(youtube, "_extract_direct_info_sync", lambda *args: {
        "url": url, "duration": 1800, "is_live": False, "live_status": "was_live",
        "protocol": "m3u8_native", "ext": "m3u8",
    })

    result = await youtube.get_video_details(url)

    assert result["duration"] == "30:00"


@pytest.mark.asyncio
@pytest.mark.parametrize("resolver", ["get_stream", "get_video_stream", "get_video_info"])
async def test_provider_credentials_absent_but_signed_urls_logged(monkeypatch, caplog, capsys, resolver):
    token = "sentinel-provider-credential"
    signature = "sentinel-stream-signature"
    signed_url = f"https://user:sentinel-url-password@media.invalid/play?signature={signature}&expire=9999999999"
    monkeypatch.setattr(youtube, "API_TOKEN", token)
    client = MagicMock()
    client.get = AsyncMock(return_value=httpx.Response(200, json=provider_response(
        stream_url=signed_url, vcodec="h264", acodec="aac",
    )))
    monkeypatch.setattr(youtube, "get_http_client", lambda: client)
    caplog.set_level(logging.INFO)

    result = await getattr(youtube, resolver)(WATCH_URL)

    assert result
    output = caplog.text + capsys.readouterr().out
    for secret in (token, "sentinel-url-password"):
        assert secret not in output
    assert f"https://[redacted]@media.invalid/play?signature={signature}&expire=9999999999" in output
    assert client.get.call_args.kwargs["headers"]["Authorization"] == f"Bearer {token}"
    assert "token" not in client.get.call_args.kwargs["params"]


@pytest.mark.asyncio
async def test_youtube_data_api_keys_are_absent_from_application_and_httpx_logs(monkeypatch, caplog, capsys):
    key = "sentinel-youtube-api-key"
    monkeypatch.setattr(youtube, "YOUTUBE_API_KEYS", [key])

    def respond(request):
        if request.url.path.endswith("search"):
            return httpx.Response(200, json={"items": [{"id": {"videoId": "abcdefghijk"}, "snippet": {}}]})
        return httpx.Response(200, json={"items": [{"id": "abcdefghijk"}]})

    caplog.set_level(logging.INFO)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(youtube, "get_http_client", lambda: client)
        assert await youtube.youtube_search("test song")

    assert key not in caplog.text + capsys.readouterr().out


@pytest.mark.asyncio
async def test_resolver_exception_redacts_credentials_but_keeps_urls(monkeypatch, caplog, capsys):
    token = "sentinel-failed-provider-token"
    signature = "sentinel-error-signature"
    monkeypatch.setattr(youtube, "API_TOKEN", token)
    client = MagicMock()
    client.get = AsyncMock(side_effect=RuntimeError(
        f"Failed https://media.invalid/private?signature={signature}, Authorization: Bearer {token}",
    ))
    monkeypatch.setattr(youtube, "get_http_client", lambda: client)
    monkeypatch.setattr(youtube, "resolve_innertube", AsyncMock(return_value=None))
    monkeypatch.setattr(youtube, "_run_yt_dlp", AsyncMock(return_value=None))
    caplog.set_level(logging.INFO)

    assert await youtube.get_stream(WATCH_URL) is None

    output = caplog.text + capsys.readouterr().out
    assert token not in output
    assert f"https://media.invalid/private?signature={signature}" in output


def test_playback_logger_keeps_urls_and_redacts_tokens_in_tracebacks(monkeypatch, caplog):
    token = "sentinel-playback-token"
    signature = "sentinel-playback-signature"
    monkeypatch.setattr(youtube, "API_TOKEN", token)
    caplog.set_level(logging.INFO)
    playback_logger = logging.getLogger("tools")
    signed_url = f"HTTPS://media.invalid/{signature}/audio.m4a?token={token}"

    playback_logger.info("Playing %s", signed_url)
    try:
        raise RuntimeError(f"Cannot play {signed_url}")
    except RuntimeError:
        playback_logger.exception("Playback failed: %s", token)

    assert token not in caplog.text
    assert f"HTTPS://media.invalid/{signature}/audio.m4a?token=[redacted]" in caplog.text
    assert "RuntimeError" in caplog.text


@pytest.mark.asyncio
async def test_ytdlp_stderr_logs_complete_url_before_truncation(monkeypatch, caplog):
    signature = "sentinel-long-stderr-signature"
    signed_url = "https://media.invalid/" + "x" * 600 + f"?signature={signature}"
    process = MagicMock(returncode=1)
    process.communicate = AsyncMock(return_value=(b"", f"Failed request to {signed_url}".encode()))
    monkeypatch.setattr(youtube.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    caplog.set_level(logging.INFO)

    assert await run_ytdlp(WATCH_URL, "best", None) is None

    assert signature in caplog.text
