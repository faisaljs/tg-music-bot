from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import media_guard
import tools
from plugins import controls
from state import SessionStore


@pytest.fixture
def seek_context(monkeypatch):
    chat_id = -1009001
    state = SessionStore()
    state.active.add(chat_id)
    state.played[chat_id] = 1000
    state.paused_at[chat_id] = 1030
    state.playing[chat_id] = {
        "title": "Track", "duration": "10:00", "mode": "audio",
        "stream_url": "https://media.test/song.mp3",
    }
    call = SimpleNamespace(play=AsyncMock(), pause=AsyncMock(), leave_call=AsyncMock())
    monkeypatch.setattr(controls, "state", state)
    monkeypatch.setattr(tools, "state", state)
    monkeypatch.setattr(controls.time, "time", lambda: 1330)
    monkeypatch.setattr(controls, "_resolve_ctrl_chat_id", AsyncMock(return_value=chat_id))
    monkeypatch.setattr(controls, "get_call_client", lambda cid: call)
    monkeypatch.setattr(controls, "rich_reply", AsyncMock())
    monkeypatch.setattr(media_guard, "get_media_proxy", AsyncMock(return_value="http://127.0.0.1:9999"))

    def stream(source, *args, **kwargs):
        return SimpleNamespace(source=source, **kwargs)

    stream.Flags = tools.MediaStream.Flags
    monkeypatch.setattr(controls, "MediaStream", stream)
    monkeypatch.setattr(tools, "MediaStream", stream)
    return chat_id, state, call


@pytest.mark.parametrize("command,position", [("seek", 40), ("seekback", 20)])
async def test_seek_uses_frozen_position_and_preserves_pause(seek_context, command, position):
    chat_id, state, call = seek_context
    message = SimpleNamespace(
        chat=SimpleNamespace(id=chat_id), command=[command], text=f"/{command} 10",
        from_user=SimpleNamespace(id=123456789, mention=lambda: "User"), delete=AsyncMock(),
    )
    await controls.seek_handler_func.__wrapped__(SimpleNamespace(), message)

    call.play.assert_awaited_once()
    options = call.play.call_args.args[1].ffmpeg_parameters
    assert f"-ss 00:{position:02d}" in options
    assert "-http_proxy http://127.0.0.1:9999" in options
    assert "-protocol_whitelist http,https,httpproxy,tcp,tls,crypto" in options
    call.pause.assert_awaited_once_with(chat_id)
    assert state.is_paused(chat_id)
    assert state.elapsed_playback(chat_id) == position


async def test_stop_during_seek_resolution_prevents_old_track_restart(seek_context, monkeypatch):
    chat_id, state, call = seek_context
    state.playing[chat_id].pop("stream_url")
    state.playing[chat_id]["yt_link"] = "https://youtu.be/abcdefghijk"

    async def resolve(*args, **kwargs):
        await state.deactivate(chat_id)
        state.playing.pop(chat_id, None)
        return "https://media.test/song.mp3"

    monkeypatch.setattr(controls, "get_stream_url", resolve)
    message = SimpleNamespace(
        chat=SimpleNamespace(id=chat_id), command=["seek"], text="/seek 10",
        from_user=SimpleNamespace(id=123456789, mention=lambda: "User"), delete=AsyncMock(),
    )
    await controls.seek_handler_func.__wrapped__(SimpleNamespace(), message)
    call.play.assert_not_awaited()
