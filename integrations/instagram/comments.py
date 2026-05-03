from typing import Any

from .graph import InstagramGraphClient


def get_comment(client: InstagramGraphClient, comment_id: str) -> dict[str, Any]:
    """
    Получает подробную информацию о комментарии из Graph API.

    Args:
        client (InstagramGraphClient): Инициализированный клиент Instagram Graph API.
        comment_id (str): Идентификатор комментария.

    Returns:
        dict[str, Any]: Словарь с данными комментария и связанным медиа.
    """
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
    """
    Публикует ответ на комментарий через Graph API.

    Args:
        client (InstagramGraphClient): Инициализированный клиент Instagram Graph API.
        comment_id (str): Идентификатор исходного комментария.
        message (str): Текст ответа.

    Returns:
        dict[str, Any]: Ответ Graph API с данными созданного reply-комментария.
    """
    return client.post(
        f"{comment_id}/replies",
        data={"message": message},
    )
