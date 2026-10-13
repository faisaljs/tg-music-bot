import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from state import state
import tools
from plugins import controls


@pytest.mark.asyncio
async def test_button_skip_autoplay_pauses_and_cleans_up():
    chat_id = -1001234567890
    state.queues[chat_id] = []
    state.playing[chat_id] = {"title": "Test Song", "yt_link": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}
    state.set_autoplay(chat_id, True)

    mock_client = MagicMock()
    mock_cb = MagicMock()
    mock_cb.data = "skip"
    mock_cb.from_user.id = 12345
    mock_cb.message.chat.id = chat_id

    mock_cp = MagicMock()
    mock_cp.pause = AsyncMock()

    mock_np_msg = MagicMock()
    mock_np_msg.delete = AsyncMock()
    state.set_now_playing(chat_id, mock_np_msg)

    with patch("plugins._common.is_authorized", AsyncMock(return_value=True)), \
         patch("plugins.controls._resolve_ctrl_chat_id", AsyncMock(return_value=chat_id)), \
         patch("plugins.controls.get_call_client", return_value=mock_cp), \
         patch("plugins.controls.trigger_suggestions", AsyncMock()) as mock_suggest:
        mock_cb.answer = AsyncMock()
        await controls.button_skip_handler(mock_client, mock_cb)

        # 1. active_cp.pause should be awaited to immediately silence VC
        mock_cp.pause.assert_awaited_once_with(chat_id)
        # 2. Callback query answered with autoplay notice
        mock_cb.answer.assert_awaited()
        # 3. Old now-playing message deleted
        mock_np_msg.delete.assert_awaited_once()
        # 4. Suggestions triggered
        mock_suggest.assert_called_once()


@pytest.mark.asyncio
async def test_skip_command_autoplay_pauses_and_cleans_up():
    chat_id = -1001234567890
    state.queues[chat_id] = []
    state.playing[chat_id] = {"title": "Test Song", "yt_link": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}
    state.set_autoplay(chat_id, True)

    mock_client = MagicMock()
    mock_msg = MagicMock()
    mock_msg.command = ["skip"]
    mock_msg.from_user.id = 12345
    mock_msg.from_user.mention = MagicMock(return_value="User")
    mock_msg.chat.id = chat_id
    mock_msg.delete = AsyncMock()

    mock_cp = MagicMock()
    mock_cp.pause = AsyncMock()

    mock_np_msg = MagicMock()
    mock_np_msg.delete = AsyncMock()
    state.set_now_playing(chat_id, mock_np_msg)

    with patch("plugins._common.is_authorized", AsyncMock(return_value=True)), \
         patch("plugins.controls._resolve_ctrl_chat_id", AsyncMock(return_value=chat_id)), \
         patch("plugins.controls.get_call_client", return_value=mock_cp), \
         patch("plugins.controls.rich_reply", AsyncMock()), \
         patch("plugins.controls.trigger_suggestions", AsyncMock()) as mock_suggest:
        await controls.skip_handler_func(mock_client, mock_msg)

        # 1. active_cp.pause should be awaited to silence VC
        mock_cp.pause.assert_awaited_once_with(chat_id)
        # 2. Old now-playing message deleted
        mock_np_msg.delete.assert_awaited_once()
        # 3. Suggestions triggered
        mock_suggest.assert_called_once()


@pytest.mark.asyncio
async def test_trigger_suggestions_pauses_and_handles_pyrogram_client():
    chat_id = -1001234567890
    last_song = {"title": "Test Song", "yt_link": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}

    # Mock client as a pyrogram Client (which lacks leave_call)
    pyrogram_client = MagicMock(spec=["send_message"])
    mock_cp = MagicMock()
    mock_cp.pause = AsyncMock()
    mock_cp.leave_call = AsyncMock()

    with patch("tools.get_call_client", return_value=mock_cp), \
         patch("tools.get_related_suggestions", AsyncMock(return_value=[])), \
         patch("tools.remove_active_chat", AsyncMock()):
        await tools._trigger_suggestions(pyrogram_client, chat_id, last_song)

        # 1. pause should be called on entry
        mock_cp.pause.assert_awaited_once_with(chat_id)
        # 2. when suggestions are empty, leave_call should be called on mock_cp, NOT on pyrogram_client
        mock_cp.leave_call.assert_awaited_once_with(chat_id)
