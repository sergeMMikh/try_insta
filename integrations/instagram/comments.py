from typing import Any

from .graph import InstagramGraphClient


def get_comment(client: InstagramGraphClient, comment_id: str) -> dict[str, Any]:
    return client.get(
        f"{comment_id}",
        params={
            "fields": "id,text,username,timestamp,parent_id,media{id,media_type,permalink}",
        },
    )


def reply_to_comment(
    client: InstagramGraphClient,
    comment_id: str,
    message: str,
) -> dict[str, Any]:
    return client.post(
        f"{comment_id}/replies",
        data={"message": message},
    )
