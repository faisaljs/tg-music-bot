import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins import controls
from state import state
from tools import auto_skip_stalled_track, update_progress_button


@pytest.mark.asyncio
async def test_progress_updater_does_not_edit_while_paused():
    chat_id = -1001234567890
    state.played[chat_id] = time.time()
    state.playing[chat_id] = {"duration": "1:00"}
    state.mark_paused(chat_id)

    message = MagicMock()
    message.edit_reply_markup = AsyncMock()
    chat = MagicMock(id=chat_id)
    markup = MagicMock(inline_keyboard=[])

    async def stop_after_pause(_):
        await asyncio.sleep(0)
        state.playing.pop(chat_id, None)

    with patch("tools.asyncio.sleep", side_effect=stop_after_pause):
        await update_progress_button(message, "1:00", chat, markup)

    message.edit_reply_markup.assert_not_awaited()
    state.paused_at.pop(chat_id, None)
    state.played.pop(chat_id, None)


@pytest.mark.asyncio
async def test_pause_and_resume_freeze_elapsed_time():
    chat_id = -1001234567891
    state.played[chat_id] = time.time()
    state.playing[chat_id] = {"duration": "1:00"}

    mock_client = MagicMock()
    mock_message = MagicMock()
    mock_message.command = ["pause"]
    mock_message.from_user.id = 12345
    mock_message.from_user.mention.return_value = "User"
    mock_message.chat.id = chat_id
    mock_cp = MagicMock()
    mock_cp.pause = AsyncMock()
    mock_cp.resume = AsyncMock()

    with patch("plugins._common.is_authorized", AsyncMock(return_value=True)), \
         patch("plugins.controls._resolve_ctrl_chat_id", AsyncMock(return_value=chat_id)), \
         patch("plugins.controls.get_call_client", return_value=mock_cp), \
         patch("plugins.controls.is_active_chat", AsyncMock(return_value=True)), \
         patch("plugins.controls.rich_reply", AsyncMock()):
        await controls.pause_handler_func(mock_client, mock_message)

    assert state.is_paused(chat_id)
    assert state.elapsed_playback(chat_id) == 0

    resume_message = MagicMock()
    resume_message.command = ["resume"]
    resume_message.from_user.id = 12345
    resume_message.from_user.mention.return_value = "User"
    resume_message.chat.id = chat_id

    with patch("plugins.controls._resolve_ctrl_chat_id", AsyncMock(return_value=chat_id)), \
         patch("plugins.controls.get_call_client", return_value=mock_cp), \
         patch("plugins._common.is_authorized", AsyncMock(return_value=True)), \
         patch("plugins.controls.is_authorized", AsyncMock(return_value=True)), \
         patch("plugins.controls.is_active_chat", AsyncMock(return_value=True)), \
         patch("plugins.controls.rich_reply", AsyncMock()):
        await controls.resume_handler_func(mock_client, resume_message)

    mock_cp.pause.assert_awaited_once_with(chat_id)
    mock_cp.resume.assert_awaited_once_with(chat_id)
    assert not state.is_paused(chat_id)
    state.playing.pop(chat_id, None)
    state.played.pop(chat_id, None)


@pytest.mark.asyncio
async def test_progress_updater_auto_skips_stalled_track():
    chat_id = -1001234567892
    state.played[chat_id] = time.time() - 81
    state.playing[chat_id] = {"duration": "1:00"}

    message = MagicMock()
    message.edit_reply_markup = AsyncMock()
    chat = MagicMock(id=chat_id)
    markup = MagicMock(inline_keyboard=[])
    auto_skip = AsyncMock()

    with patch("tools.auto_skip_stalled_track", auto_skip):
        await update_progress_button(message, "1:00", chat, markup)

    auto_skip.assert_awaited_once_with(chat_id, 60)
    message.edit_reply_markup.assert_not_awaited()
    state.playing.pop(chat_id, None)
    state.played.pop(chat_id, None)


@pytest.mark.asyncio
async def test_stalled_track_ends_when_queue_is_empty():
    chat_id = -1001234567893
    state.played[chat_id] = time.time() - 81
    state.playing[chat_id] = {"duration": "1:00"}
    active_cp = MagicMock()
    active_cp.leave_call = AsyncMock()

    with patch("tools.get_call_client", return_value=active_cp), \
         patch("tools.remove_active_chat", AsyncMock()) as remove_active:
        assert await auto_skip_stalled_track(chat_id, 60)

    active_cp.leave_call.assert_awaited_once_with(chat_id)
    remove_active.assert_awaited_once_with(chat_id)
    assert chat_id not in state.playing
    state.played.pop(chat_id, None)
