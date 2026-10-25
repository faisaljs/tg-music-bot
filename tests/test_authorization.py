"""Blocked users must not reach protected handlers through existing privileges."""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pyrogram.enums import ChatMemberStatus

import tools
from plugins import _common as common
from plugins import admin_auth, controls, queue_cmds
from state import SessionStore


CHAT_ID = -1009876543210
USER_ID = 98765
TARGET_ID = 98766


@pytest.fixture
def authorization(monkeypatch):
    auth = {}
    blocked = []
    store = SessionStore()
    reply = AsyncMock()
    for module in (common, admin_auth, controls, queue_cmds):
        monkeypatch.setattr(module, "AUTH", auth)
        monkeypatch.setattr(module, "BLOCK", blocked)
        monkeypatch.setattr(module, "state", store)
        monkeypatch.setattr(module, "rich_reply", reply)
    monkeypatch.setattr(tools, "ADMIN", [])
    monkeypatch.setattr(tools, "SUDO", [])
    monkeypatch.setattr(common, "_admin_member_cache", {})
    client = SimpleNamespace(
        me=SimpleNamespace(id=999),
        get_chat=AsyncMock(return_value=SimpleNamespace(linked_chat=None)),
        get_chat_member=AsyncMock(
            return_value=SimpleNamespace(status=ChatMemberStatus.ADMINISTRATOR)
        ),
    )
    return SimpleNamespace(
        auth=auth, blocked=blocked, state=store, reply=reply, client=client
    )


def command_message(command, user_id=USER_ID):
    return SimpleNamespace(
        chat=SimpleNamespace(id=CHAT_ID),
        from_user=SimpleNamespace(id=user_id),
        id=1,
        command=command.split(),
        text=f"/{command}",
        reply_to_message=None,
        delete=AsyncMock(),
    )


def callback_query(data):
    callback = common.CallbackQuery(
        id="authorization-test",
        from_user=SimpleNamespace(id=USER_ID),
        message=SimpleNamespace(
            id=1, chat=SimpleNamespace(id=CHAT_ID), reply_markup=None
        ),
        data=data,
    )
    callback.answer = AsyncMock()
    return callback


@pytest.mark.parametrize(
    "privilege", ("owner", "admin", "sudo", "auth", "cached_admin", "telegram_admin")
)
@pytest.mark.parametrize("blocked", (True, False))
async def test_block_takes_precedence_over_every_privilege(
    authorization, privilege, blocked
):
    user_id = common.OWNER_ID if privilege == "owner" else USER_ID
    if privilege == "admin":
        tools.ADMIN.append(user_id)
    elif privilege == "sudo":
        tools.SUDO.append(user_id)
    elif privilege == "auth":
        authorization.auth[str(CHAT_ID)] = [user_id]
    elif privilege == "cached_admin":
        common._admin_member_cache[(CHAT_ID, user_id)] = (
            ChatMemberStatus.ADMINISTRATOR.value,
            time.time() + 60,
        )
    if blocked:
        authorization.blocked.append(user_id)

    assert await common.is_authorized(
        authorization.client, CHAT_ID, user_id
    ) is (not blocked)
    if blocked or privilege != "telegram_admin":
        authorization.client.get_chat_member.assert_not_awaited()
    else:
        authorization.client.get_chat_member.assert_awaited_once_with(CHAT_ID, user_id)


@pytest.mark.parametrize("blocked", (True, False))
async def test_auth_handler_cannot_delegate_after_blocking(
    authorization, monkeypatch, blocked
):
    authorization.auth[str(CHAT_ID)] = [USER_ID]
    if blocked:
        authorization.blocked.append(USER_ID)
    update_one = Mock()
    db_task = Mock()
    monkeypatch.setattr(admin_auth, "user_sessions", SimpleNamespace(update_one=update_one))
    monkeypatch.setattr(admin_auth, "db_task", db_task)

    await admin_auth.auth_user(
        authorization.client, command_message(f"auth {TARGET_ID}")
    )

    assert (TARGET_ID in authorization.auth[str(CHAT_ID)]) is (not blocked)
    if blocked:
        update_one.assert_not_called()
        db_task.assert_not_called()
        assert common.Messages.ADMIN_RESTRICTED_CMD in authorization.reply.await_args.args[1]
    else:
        update_one.assert_called_once()
        db_task.assert_called_once()
    authorization.client.get_chat_member.assert_not_awaited()


@pytest.mark.parametrize("blocked", (True, False))
async def test_shuffle_handler_cannot_change_queue_after_blocking(
    authorization, monkeypatch, blocked
):
    authorization.auth[str(CHAT_ID)] = [USER_ID]
    authorization.state.queues[CHAT_ID] = ["first", "second"]
    if blocked:
        authorization.blocked.append(USER_ID)
    shuffle = Mock(side_effect=lambda queue: queue.reverse())
    monkeypatch.setattr(queue_cmds.random, "shuffle", shuffle)

    await queue_cmds.shuffle_queue(authorization.client, command_message("shuffle"))

    assert authorization.state.queues[CHAT_ID] == (
        ["first", "second"] if blocked else ["second", "first"]
    )
    if blocked:
        shuffle.assert_not_called()
        assert common.Messages.ADMIN_RESTRICTED_CMD in authorization.reply.await_args.args[1]
    else:
        shuffle.assert_called_once()


@pytest.mark.parametrize("blocked", (True, False))
async def test_autoplay_callback_cannot_change_setting_after_blocking(
    authorization, blocked
):
    authorization.auth[str(CHAT_ID)] = [USER_ID]
    authorization.state.active.add(CHAT_ID)
    authorization.state.set_autoplay(CHAT_ID, False)
    if blocked:
        authorization.blocked.append(USER_ID)
    callback = callback_query("sgtoggle")

    await controls.suggestion_toggle_handler(authorization.client, callback)

    assert authorization.state.is_autoplay_enabled(CHAT_ID) is (not blocked)
    if blocked:
        callback.answer.assert_awaited_once_with(
            common.Messages.ADMIN_RESTRICTED_ACTION, show_alert=True
        )
    else:
        callback.answer.assert_awaited_once_with("Autoplay: ON", show_alert=False)
    authorization.client.get_chat.assert_not_awaited()
    authorization.client.get_chat_member.assert_not_awaited()


@pytest.mark.parametrize("command", ("skip", "cskip"))
async def test_blocked_song_owner_does_not_enter_skip_handler(authorization, command):
    authorization.blocked.append(USER_ID)
    authorization.state.playing[CHAT_ID] = {"by": SimpleNamespace(id=USER_ID)}
    message = command_message(command)

    await controls.skip_handler_func(authorization.client, message)

    message.delete.assert_not_awaited()
    authorization.client.get_chat.assert_not_awaited()
    authorization.client.get_chat_member.assert_not_awaited()
    assert common.Messages.ADMIN_RESTRICTED_CMD in authorization.reply.await_args.args[1]


@pytest.mark.parametrize("callback", (True, False))
async def test_unblocked_song_owner_keeps_skip_exemption(authorization, callback):
    authorization.state.playing[CHAT_ID] = {"by": SimpleNamespace(id=USER_ID)}
    handler = AsyncMock()
    protected_handler = common.admin_only()(handler)
    message = callback_query("skip") if callback else command_message("skip")

    await protected_handler(authorization.client, message)

    handler.assert_awaited_once_with(authorization.client, message)
    authorization.client.get_chat_member.assert_not_awaited()
