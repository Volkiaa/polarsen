import io
import json
import os

from minio import Minio
from tracktolib.pg_sync import fetch_one, insert_many

from polarsen import env
from tests.data.db import (
    gen_chat,
    gen_chat_upload,
    gen_chat_user,
    gen_telegram_group,
    gen_user,
    load_chat_types,
)
from tests.data.utils import Fake


def test_process_uploads_rejects_inaccessible_existing_chat(engine, minio_client: Minio, loop, aengine):
    bucket = os.getenv("CHAT_UPLOADS_S3_BUCKET", "test-bucket")
    os.environ["CHAT_UPLOADS_S3_BUCKET"] = bucket
    env.CHAT_UPLOADS_S3_BUCKET = bucket
    if not minio_client.bucket_exists(bucket):
        minio_client.make_bucket(bucket)

    chat_types = load_chat_types(engine, as_dict=True)
    owner = gen_user(telegram_id=f"owner-{Fake.id()}")
    other = gen_user(telegram_id=f"other-{Fake.id()}")
    group = gen_telegram_group()
    chat = gen_chat(created_by=owner["id"])
    chat["internal_code"] = str(group["id"])
    chat_user = gen_chat_user(chat_id=chat["id"], chat_source_id=chat_types["telegram"]["id"])
    upload = gen_chat_upload(user_id=other["id"], chat_type_id=chat_types["telegram"]["id"])

    with engine.cursor() as cur:
        insert_many(cur, "general.users", [owner, other])
        insert_many(cur, "general.chats", [chat])
        insert_many(cur, "general.chat_users", [chat_user])
        insert_many(
            cur,
            "general.user_chats",
            [{"user_id": owner["id"], "chat_id": chat["id"], "chat_user_id": chat_user["id"]}],
        )
        insert_many(cur, "general.chat_uploads", [upload])
    engine.commit()

    content = json.dumps(group).encode()
    minio_client.put_object(bucket, upload["file_path"], io.BytesIO(content), len(content))

    async def run():
        import niquests

        from polarsen.cli.ingest import process_uploads
        from polarsen.s3_utils import get_s3_client

        async with niquests.AsyncSession() as session:
            with get_s3_client() as s3:
                return await process_uploads(session, aengine, s3)

    assert loop.run_until_complete(run()) == []
    messages = fetch_one(engine, "SELECT count(*) AS count FROM general.chat_messages")
    pending = fetch_one(engine, "SELECT count(*) AS count FROM general.chat_uploads WHERE processed_at IS NULL")
    assert messages is not None and messages["count"] == 0
    assert pending is not None and pending["count"] == 0
