from http import HTTPStatus

import pytest
from tracktolib.pg_sync import insert_many

from tests.data.db import gen_chat, gen_chat_user, gen_user, load_chat_types
from tests.data.utils import Fake
from ..utils import generate_large_file


@pytest.fixture
def chat_access(engine):
    chat_types = load_chat_types(engine, as_dict=True)
    owner = gen_user(telegram_id=f"owner-{Fake.id()}")
    other = gen_user(telegram_id=f"other-{Fake.id()}")
    chat = gen_chat(created_by=owner["id"])
    chat_user = gen_chat_user(chat_id=chat["id"], chat_source_id=chat_types["telegram"]["id"])
    # get_telegram_chat_username matches users.telegram_id against chat_users.internal_code.
    owner["telegram_id"] = chat_user["internal_code"]
    user_chat = {
        "user_id": owner["id"],
        "chat_id": chat["id"],
        "chat_user_id": chat_user["id"],
    }

    with engine.cursor() as cur:
        insert_many(cur, "general.users", [owner, other])
        insert_many(cur, "general.chats", [chat])
        insert_many(cur, "general.chat_users", [chat_user])
        insert_many(cur, "general.user_chats", [user_chat])
    engine.commit()
    return owner, other, chat


class _FakeChatSession:
    async def ask_rag(self, **kwargs):
        return "Test answer", {"results": []}


@pytest.fixture
def fake_chat_session(monkeypatch):
    monkeypatch.setattr("polarsen.api.main.ChatSession.get_session", lambda **kwargs: _FakeChatSession())


def _ask(client, chat_id: int, user_id: int):
    return client.get(
        f"/chats/{chat_id}/ask",
        params={
            "api_key": "test-key",
            "question": "What happened?",
            "user_id": user_id,
            "model": "gpt-4.1",
        },
    )


def test_ask_rejects_user_without_chat_access(client, chat_access, fake_chat_session):
    _, other, chat = chat_access

    resp = _ask(client, chat["id"], other["id"])

    assert resp.status_code == HTTPStatus.FORBIDDEN
    assert resp.json()["error_code"] == "forbidden"


def test_ask_allows_chat_owner(client, chat_access, fake_chat_session):
    owner, _, chat = chat_access

    resp = _ask(client, chat["id"], owner["id"])

    assert resp.status_code == HTTPStatus.OK, resp.text
    assert resp.json()["response"] == "Test answer"


def test_upload_rejects_user_without_chat_access(client, chat_access):
    _, other, chat = chat_access

    resp = client.post(
        "/chats/upload",
        params={
            "user_id": other["id"],
            "filename": "chat.json",
            "mime_type": "application/json",
            "chat_type": "telegram",
            "chat_id": chat["id"],
        },
    )

    assert resp.status_code == HTTPStatus.FORBIDDEN
    assert resp.json()["error_code"] == "forbidden"


def test_upload_allows_chat_owner(client, chat_access):
    owner, _, chat = chat_access
    content = generate_large_file(1024)

    resp = client.post(
        "/chats/upload",
        params={
            "user_id": owner["id"],
            "filename": "chat.json",
            "mime_type": "application/json",
            "chat_type": "telegram",
            "chat_id": chat["id"],
        },
        headers={"X-Content-Length": str(len(content))},
        content=content,
    )

    assert resp.status_code == HTTPStatus.OK, resp.text
