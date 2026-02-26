from typing import Any


def extract_comment_tasks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    entries = payload.get("entry")
    if not isinstance(entries, list):
        return tasks

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        changes = entry.get("changes")
        if not isinstance(changes, list):
            continue

        for change in changes:
            if not isinstance(change, dict):
                continue
            field = _as_str(change.get("field"))
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            if _is_delete_event(value):
                continue
            if not _looks_like_comment_event(field, value):
                continue

            comment_id = _extract_comment_id(value)
            if not comment_id or comment_id in seen_ids:
                continue

            seen_ids.add(comment_id)
            from_data = value.get("from")
            tasks.append(
                {
                    "comment_id": comment_id,
                    "media_id": _first_nonempty(value.get("media_id"), value.get("post_id")),
                    "parent_id": _first_nonempty(value.get("parent_id")),
                    "commenter_username": _first_nonempty(
                        value.get("username"),
                        from_data.get("username") if isinstance(from_data, dict) else None,
                    ),
                    "comment_text": _first_nonempty(value.get("text")),
                    "payload": {
                        "field": field,
                        "entry_id": _as_str(entry.get("id")),
                        "time": entry.get("time"),
                        "value": value,
                    },
                }
            )

    return tasks


def _looks_like_comment_event(field: str | None, value: dict[str, Any]) -> bool:
    field_value = (field or "").strip().lower()
    item_value = _as_str(value.get("item"))
    if field_value == "comments":
        return True
    if item_value and item_value.lower() == "comment":
        return True
    if value.get("comment_id"):
        return True
    if value.get("text") and value.get("id") and value.get("media_id"):
        return True
    return False


def _is_delete_event(value: dict[str, Any]) -> bool:
    verb = _as_str(value.get("verb"))
    return bool(verb and verb.lower() in {"remove", "delete", "deleted"})


def _extract_comment_id(value: dict[str, Any]) -> str | None:
    for candidate in (value.get("comment_id"), value.get("id")):
        comment_id = _as_str(candidate)
        if comment_id and comment_id.isdigit():
            return comment_id
    return None


def _first_nonempty(*values: Any) -> str | None:
    for value in values:
        text_value = _as_str(value)
        if text_value:
            return text_value
    return None


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text_value = str(value).strip()
    return text_value or None
