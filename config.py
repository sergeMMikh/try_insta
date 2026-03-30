from pathlib import Path


def read_token(path: str | Path) -> str:
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Token file not found: {path}")

    token = path.read_text(encoding="utf-8").strip()

    if not token:
        raise ValueError("Token file is empty")

    return token


def read_env_var(name: str, env_path: str | Path = ".env") -> str:
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
    try:
        return read_env_var(name, env_path=env_path)
    except (KeyError, FileNotFoundError):
        return default
