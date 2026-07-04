import os


STREAMLIT_ENV_KEYS = [
    "APP_ENV",
    "ENV",
    "POLYGON_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_API_KEY_APP",
    "DATABASE_URL",
    "DATABASE_DIRECT_URL",
    "DB_WRITE_ENABLED",
    "DB_POOL_SIZE",
    "DB_MAX_OVERFLOW",
    "DB_CONNECT_TIMEOUT_SECONDS",
    "USE_MOCK_MARKET_DATA",
    "USE_MOCK_OPTIONS",
    "ENABLE_AI_SUMMARY",
    "SCANNER_AI_SUMMARY_ENABLED",
]


def _get_secret(secrets, key, default=None):
    try:
        if key in secrets:
            return secrets[key]
    except Exception:
        pass

    try:
        return secrets.get(key, default)
    except Exception:
        return default


def _nested_secret(secrets, *path):
    value = secrets

    for key in path:
        value = _get_secret(value, key)

        if value is None:
            return None

    return value


def _items(value):
    try:
        return list(value.items())
    except Exception:
        return []


def _first_connection_url(secrets):
    connections = _get_secret(secrets, "connections")

    if connections is None:
        return None

    for name in [
        "trading_db",
        "database",
        "neon",
        "postgres",
        "postgresql",
        "db"
    ]:
        connection_url = _nested_secret(
            secrets,
            "connections",
            name,
            "url"
        )

        if connection_url:
            return connection_url

    for _, connection in _items(connections):
        connection_url = _get_secret(connection, "url")

        if connection_url:
            return connection_url

    return None


def _sync_database_section(secrets):
    database = _get_secret(secrets, "database")

    if database is None:
        return False

    return _sync_database_mapping(database)


def _sync_database_mapping(source):
    if source is None:
        return False

    mapping = {
        "DATABASE_URL": ["DATABASE_URL", "database_url", "url"],
        "DATABASE_DIRECT_URL": [
            "DATABASE_DIRECT_URL",
            "database_direct_url",
            "direct_url"
        ],
        "DB_WRITE_ENABLED": [
            "DB_WRITE_ENABLED",
            "db_write_enabled",
            "write_enabled"
        ],
        "DB_POOL_SIZE": ["DB_POOL_SIZE", "db_pool_size", "pool_size"],
        "DB_MAX_OVERFLOW": [
            "DB_MAX_OVERFLOW",
            "db_max_overflow",
            "max_overflow"
        ],
        "DB_CONNECT_TIMEOUT_SECONDS": [
            "DB_CONNECT_TIMEOUT_SECONDS",
            "db_connect_timeout_seconds",
            "connect_timeout_seconds"
        ]
    }
    found = False

    for env_key, secret_keys in mapping.items():
        for secret_key in secret_keys:
            value = _get_secret(source, secret_key)

            if value is not None:
                os.environ[env_key] = str(value)
                found = True
                break

    return found


def _sync_nested_database_keys(secrets):
    found = False

    for section_name, section_value in _items(secrets):
        if section_name in {"connections", "database"}:
            continue

        if not _items(section_value):
            continue

        if _sync_database_mapping(section_value):
            found = True

    return found


def sync_streamlit_secrets_to_env(secrets=None):
    if secrets is None:
        try:
            import streamlit as st

            secrets = st.secrets

        except Exception:
            return

    for key in STREAMLIT_ENV_KEYS:
        value = _get_secret(secrets, key)

        if value is not None:
            os.environ[key] = str(value)

    try:
        for key, value in secrets.items():
            if isinstance(value, (str, int, float, bool)):
                os.environ[str(key)] = str(value)
    except Exception:
        pass

    database_section_found = _sync_database_section(secrets)
    nested_database_keys_found = _sync_nested_database_keys(secrets)

    connection_url_found = False

    if not os.getenv("DATABASE_URL", "").strip():
        connection_url = _first_connection_url(secrets)

        if connection_url:
            os.environ["DATABASE_URL"] = str(connection_url)
            connection_url_found = True

    if (
        os.getenv("DATABASE_URL", "").strip()
        and os.getenv("DB_WRITE_ENABLED") is None
    ):
        os.environ["DB_WRITE_ENABLED"] = "true"

    print(
        "[STREAMLIT SECRETS STATUS]",
        "ROOT_DATABASE_URL_PRESENT=",
        bool(_get_secret(secrets, "DATABASE_URL")),
        "ROOT_DB_WRITE_ENABLED_PRESENT=",
        _get_secret(secrets, "DB_WRITE_ENABLED") is not None,
        "DATABASE_SECTION_FOUND=",
        database_section_found,
        "NESTED_DATABASE_KEYS_FOUND=",
        nested_database_keys_found,
        "CONNECTION_URL_FOUND=",
        connection_url_found,
        "ENV_DATABASE_URL_PRESENT=",
        bool(os.getenv("DATABASE_URL", "").strip()),
        "ENV_DB_WRITE_ENABLED=",
        os.getenv("DB_WRITE_ENABLED")
    )