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

    if not os.getenv("DATABASE_URL", "").strip():
        connection_url = _nested_secret(
            secrets,
            "connections",
            "trading_db",
            "url"
        )

        if connection_url:
            os.environ["DATABASE_URL"] = str(connection_url)