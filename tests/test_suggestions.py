import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import tools
import youtube
from state import SessionStore


SEED = "enidMo5izlE"
RECENT = "abcdefghijk"
NEXT = "lmnopqrstuv"
ANOTHER = "12345678901"


def track(video_id, *, renderer="playlistPanelVideoRenderer", title="Test song", artist="Test artist"):
    return {renderer: {
        "videoId": video_id,
        "title": {"runs": [{"text": title}]},
        "shortBylineText": {"runs": [{"text": artist}]},
        "lengthText": {"simpleText": "3:20"},
        "thumbnail": {"thumbnails": [{"url": "https://images.invalid/song.jpg"}]},
    }}


@pytest.fixture
def recommendation_api(monkeypatch):
    monkeypatch.setattr(youtube, "YOUTUBE_API_KEYS", [])
    radio = {"contents": {"playlistPanelRenderer": {
        "contents": [track(SEED)],
        "continuations": [{"nextRadioContinuationData": {"continuation": "empty-radio-page"}}],
    }}}
    client = MagicMock()
    client.post = AsyncMock(return_value=httpx.Response(200, json=radio))
    monkeypatch.setattr(youtube, "get_http_client", lambda: client)
    search = AsyncMock(return_value={"contents": [
        track(vid, renderer="compactVideoRenderer") for vid in (SEED, RECENT, NEXT, NEXT, ANOTHER)
    ]})
    monkeypatch.setattr(youtube, "_post_innertube_async", search)
    return client, search


@pytest.mark.asyncio
async def test_seed_only_radio_uses_keyless_search_and_excludes_recent_tracks(recommendation_api):
    _client, search = recommendation_api

    results = await youtube.get_related_suggestions(SEED, limit=2, exclude_ids={RECENT})

    assert [item["video_id"] for item in results] == [NEXT, ANOTHER]
    assert results[0]["duration"] == "3:20"
    assert results[0]["url"] == f"https://www.youtube.com/watch?v={NEXT}"
    query = search.await_args.args[1]["query"]
    assert "Test artist" in query
    assert SEED not in query


@pytest.mark.asyncio
async def test_complete_radio_does_not_search(recommendation_api):
    client, search = recommendation_api
    client.post.return_value = httpx.Response(200, json={"contents": [track(SEED), track(NEXT)]})

    results = await youtube.get_related_suggestions(SEED, limit=1)

    assert [item["video_id"] for item in results] == [NEXT]
    search.assert_not_awaited()


@pytest.mark.asyncio
async def test_radio_failure_searches_last_song_title(recommendation_api):
    client, search = recommendation_api
    client.post.return_value = httpx.Response(503)

    results = await youtube.get_related_suggestions(SEED, limit=1, seed_title="Known song title")

    assert results
    assert "Known song title" in search.await_args.args[1]["query"]
    assert SEED not in search.await_args.args[1]["query"]


@pytest.mark.asyncio
async def test_failed_search_preserves_partial_radio_results(recommendation_api):
    client, search = recommendation_api
    client.post.return_value = httpx.Response(200, json={"contents": [track(SEED), track(NEXT)]})
    search.side_effect = httpx.ConnectError("Search unavailable")

    results = await youtube.get_related_suggestions(SEED, limit=5)

    assert [item["video_id"] for item in results] == [NEXT]


@pytest.fixture
def playback_session(monkeypatch):
    store = SessionStore()
    monkeypatch.setattr(tools, "state", store)
    call = SimpleNamespace(pause=AsyncMock(), leave_call=AsyncMock())
    bot = SimpleNamespace()
    monkeypatch.setattr(tools, "get_call_client", lambda chat_id: call)
    monkeypatch.setattr(tools, "clients", {"bot": bot, "call_py": call})
    monkeypatch.setattr(tools, "db_task", lambda coroutine: coroutine.close())
    return store, call


@pytest.mark.asyncio
@pytest.mark.parametrize("autoplay", [True, False])
async def test_stalled_last_song_respects_autoplay(playback_session, monkeypatch, autoplay):
    store, call = playback_session
    chat_id = -10087654321
    await store.activate(chat_id)
    store.set_autoplay(chat_id, autoplay)
    last_song = {"title": "Known song title", "yt_link": f"https://youtu.be/{SEED}", "duration": "1:00"}
    store.playing[chat_id] = last_song
    store.played[chat_id] = time.time() - 81
    suggest = AsyncMock()
    monkeypatch.setattr(tools, "_trigger_suggestions", suggest)
    generation = store.generation(chat_id)

    assert await tools.auto_skip_stalled_track(chat_id, 60)
    await asyncio.sleep(0)

    if autoplay:
        suggest.assert_awaited_once_with(call, chat_id, last_song, generation=generation)
        call.pause.assert_awaited_once_with(chat_id)
        call.leave_call.assert_not_awaited()
        assert store.last_played[chat_id] is last_song
        assert chat_id in store.active
    else:
        suggest.assert_not_awaited()
        call.leave_call.assert_awaited_once_with(chat_id)
        assert chat_id not in store.active
    assert chat_id not in store.playing


@pytest.mark.asyncio
async def test_stop_during_stalled_pause_cannot_start_suggestions(playback_session, monkeypatch):
    store, call = playback_session
    chat_id = -10087654322
    await store.activate(chat_id)
    store.playing[chat_id] = {"title": "Known song", "yt_link": f"https://youtu.be/{SEED}"}
    store.played[chat_id] = time.time() - 81
    call.pause.side_effect = lambda cid: store.invalidate(cid)
    suggest = AsyncMock()
    monkeypatch.setattr(tools, "_trigger_suggestions", suggest)

    assert not await tools.auto_skip_stalled_track(chat_id, 60)
    await asyncio.sleep(0)

    suggest.assert_not_awaited()


@pytest.mark.asyncio
async def test_suggestion_card_displays_keyless_results(playback_session, recommendation_api, monkeypatch):
    store, call = playback_session
    chat_id = -10087654323
    store.set_autoplay(chat_id, False)
    send = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(tools, "rich_send_blocks", send)
    last_song = {"title": "Known song title", "yt_link": f"https://youtu.be/{SEED}"}

    await tools._trigger_suggestions(call, chat_id, last_song)

    send.assert_awaited_once()
    assert send.await_args.args[1] == chat_id
    assert f"sgplay_{NEXT}" in str(send.await_args.args[2])
    call.leave_call.assert_not_awaited()
