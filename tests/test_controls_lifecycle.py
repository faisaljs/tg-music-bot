import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import tools
from plugins import _common, controls, playlist
from state import SessionStore


@pytest.fixture
def control_session(monkeypatch, tmp_path):
    store = SessionStore()
    bot = SimpleNamespace(me=SimpleNamespace(id=999, username="testbot"))
    call = SimpleNamespace(play=AsyncMock(), resume=AsyncMock(), pause=AsyncMock(), leave_call=AsyncMock())
    chat = SimpleNamespace(id=-1009010, type=tools.ChatType.SUPERGROUP, title="Test")
    message = SimpleNamespace(
        chat=chat, command=["stop"], delete=AsyncMock(),
        from_user=SimpleNamespace(id=123456789, mention=lambda: "User"),
    )
    for module in (tools, _common, controls, playlist):
        monkeypatch.setattr(module, "state", store)
    for module in (tools, _common):
        monkeypatch.setattr(module, "db_task", lambda coroutine: coroutine.close())
        monkeypatch.setattr(module, "ggg", str(tmp_path))
    monkeypatch.setattr(tools, "clients", {"bot": bot, "call_py": call})
    monkeypatch.setattr(tools, "calls", {1: call})
    monkeypatch.setattr(controls, "get_call_client", lambda cid: call)
    monkeypatch.setattr(controls, "_resolve_ctrl_chat_id", AsyncMock(return_value=chat.id))
    monkeypatch.setattr(controls, "rich_reply", AsyncMock())
    monkeypatch.setattr(tools, "rich_send_blocks", AsyncMock(return_value=None))
    monkeypatch.setattr(tools, "rich_send", AsyncMock(return_value=None))
    monkeypatch.setattr(tools, "make_media_stream", AsyncMock(return_value=SimpleNamespace()))
    return store, bot, call, chat, message


@pytest.mark.parametrize("start_new_track", [False, True])
async def test_stop_waits_for_inflight_play_then_leaves_without_reactivation(control_session, start_new_track):
    store, bot, call, chat, message = control_session
    await store.activate(chat.id)
    initial_generation = store.generation(chat.id)
    started, release = asyncio.Event(), asyncio.Event()
    operations = []

    async def play(*args):
        operations.append("play")
        started.set()
        await release.wait()

    async def leave(*args):
        operations.append("leave")

    call.play.side_effect = play
    call.leave_call.side_effect = leave
    joining = asyncio.create_task(tools.join_call(
        message, "Track", "https://media.test/song.mp3", chat, "User", "1:00", "audio", None,
        stream_url="https://media.test/song.mp3", assistant_num=1, generation=initial_generation,
    ))
    await asyncio.wait_for(started.wait(), 1)
    stopping = asyncio.create_task(controls.end_handler_func.__wrapped__(bot, message))
    await asyncio.sleep(0)
    assert not store.is_current(chat.id, initial_generation)
    assert chat.id not in store.active
    pending = [joining, stopping]
    if start_new_track:
        await store.activate(chat.id)
        pending.append(asyncio.create_task(tools.join_call(
            message, "New", "https://media.test/new.mp3", chat, "User", "1:00", "audio", None,
            stream_url="https://media.test/new.mp3", assistant_num=1, generation=store.generation(chat.id),
        )))
    release.set()
    await asyncio.wait_for(asyncio.gather(*pending), 2)
    if start_new_track:
        assert operations == ["play", "leave", "play"]
        assert store.playing[chat.id]["title"] == "New"
        assert chat.id in store.active
    else:
        assert operations == ["play", "leave"]
        call.resume.assert_not_awaited()
        assert chat.id not in store.active
        assert chat.id not in store.playing
    assert not store.queues.get(chat.id)


async def test_stop_panel_cleanup_does_not_erase_newer_session(control_session):
    store, bot, call, chat, _message = control_session
    await store.activate(chat.id)
    new_track = {"title": "New"}

    async def delete_panel():
        store.invalidate(chat.id)
        await store.activate(chat.id)
        store.playing[chat.id] = new_track
        store.queues[chat.id] = [new_track]

    panel = SimpleNamespace(delete=AsyncMock(side_effect=delete_panel))
    store.set_now_playing(chat.id, panel)
    await controls._stop_playback(bot, chat.id, call)
    panel.delete.assert_awaited_once()
    assert store.playing[chat.id] is new_track
    assert store.queues[chat.id] == [new_track]
    assert chat.id in store.active


@pytest.mark.parametrize("stop_while_sending", [False, True])
async def test_saved_video_playlist_propagates_mode_and_stop(control_session, monkeypatch, stop_while_sending):
    store, bot, _call, chat, message = control_session
    status = SimpleNamespace(chat=chat)

    async def send(*args, **kwargs):
        if stop_while_sending:
            await store.deactivate(chat.id)
        return status

    bot.send_message = send
    monkeypatch.setattr(playlist, "allow_play_request", AsyncMock(return_value=True))
    resolve = AsyncMock(return_value=("Track", "1:00", "https://youtu.be/abcdefghijk", None, None, None, "abcdefghijk", "https://media.test/video.mp4"))
    queue = AsyncMock()
    start = AsyncMock()
    monkeypatch.setattr(playlist, "handle_youtube", resolve)
    monkeypatch.setattr(playlist, "put_queue", queue)
    monkeypatch.setattr(playlist, "dend", start)
    await playlist._play_playlist_tracks(bot, message, {"name": "Saved", "tracks": [{"title": "Track", "yt_link": "https://youtu.be/abcdefghijk"}]}, video_mode=True)
    await asyncio.sleep(0)
    if stop_while_sending:
        resolve.assert_not_awaited()
        queue.assert_not_awaited()
        start.assert_not_awaited()
    else:
        resolve.assert_awaited_once_with("https://youtu.be/abcdefghijk", mode="video")
        assert queue.call_args.args[7] == "video"
        assert queue.call_args.kwargs["generation"] == store.generation(chat.id)
        start.assert_awaited_once_with(bot, status, None, generation=store.generation(chat.id))
