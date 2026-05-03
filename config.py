import os
from pathlib import Path


def read_token(path: str | Path) -> str:
    """
    Читает токен из текстового файла.

    Args:
        path (str | Path): Путь к файлу с токеном.

    Returns:
        str: Непустое значение токена.

    Raises:
        FileNotFoundError: Если файл с токеном не существует.
        ValueError: Если файл найден, но токен внутри пустой.
    """
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Token file not found: {path}")

    token = path.read_text(encoding="utf-8").strip()

    if not token:
        raise ValueError("Token file is empty")

    return token


def read_env_var(name: str, env_path: str | Path = ".env") -> str:
    """
    Читает обязательную переменную окружения из `os.environ` или `.env`.

    Args:
        name (str): Имя искомой переменной.
        env_path (str | Path, optional): Путь к файлу `.env`, если переменная не найдена в окружении.

    Returns:
        str: Непустое строковое значение переменной.

    Raises:
        FileNotFoundError: Если переменная не найдена в окружении и файл `.env` отсутствует.
        KeyError: Если переменная отсутствует и в окружении, и в файле `.env`.
        ValueError: Если переменная найдена, но содержит пустое значение.
    """
    env_value = os.getenv(name)
    if env_value is not None:
        env_value = env_value.strip().strip("'\"")
        if not env_value:
            raise ValueError(f"{name} is empty in environment")
        return env_value

    path = Path(env_path)

    if not path.exists():
        raise FileNotFoundError(f".env file not found: {path}")

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        if key.strip() != name:
            continue

        value = value.strip().strip("'\"")
        if not value:
            raise ValueError(f"{name} is empty in {path}")
        return value

    raise KeyError(f"{name} not found in {path}")


def read_env_var_optional(
    name: str, default: str | None = None, env_path: str | Path = ".env"
) -> str | None:
    """
    Читает необязательную переменную окружения с безопасным fallback.

    Args:
        name (str): Имя переменной.
        default (str | None, optional): Значение, которое будет возвращено при отсутствии переменной.
        env_path (str | Path, optional): Путь к файлу `.env`.

    Returns:
        str | None: Найденное значение переменной или `default`.
    """
    try:
        return read_env_var(name, env_path=env_path)
    except (KeyError, FileNotFoundError):
        return default
