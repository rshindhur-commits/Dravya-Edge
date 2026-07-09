import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv(override=True)


def get_secret_env(name, default=None):

    value = os.getenv(name)

    if value not in [None, ""]:

        return value

    return default


def get_bool_env(name, default=False):

    value = get_secret_env(name)

    if value is None:

        return default

    return value.strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on"
    }


def get_float_env(name, default):

    value = get_secret_env(name)

    if value is None:

        return default

    try:

        return float(value)

    except ValueError:

        return default


def get_int_env(name, default):

    value = get_secret_env(name)

    if value is None:

        return default

    try:

        return int(value)

    except ValueError:

        return default


@dataclass(frozen=True)
class RuntimeSettings:

    app_env: str
    use_mock_market_data: bool
    use_mock_options: bool
    enable_ai_summary: bool
    scanner_ai_summary_enabled: bool
    scanner_debug: bool
    ai_request_timeout_seconds: float
    polygon_api_key: str
    openai_api_key: str
    openai_dashboard_model: str
    openai_summary_max_tokens: int
    ai_summary_cache_file: str
    scanner_output_file: str
    polygon_base_url: str
    account_size: float
    risk_percent: float
    max_contracts_per_trade: int
    option_stop_loss_pct: float
    event_blocker_enabled: bool
    event_blocker_dates: str
    event_blocker_days_before: int
    option_min_volume: int
    option_min_open_interest: int
    option_max_spread_pct: float
    option_min_quality_score: int
    option_delayed_quote_minutes: int
    option_max_quote_age_minutes: int
    option_allow_0dte: bool
    option_allow_1dte: bool
    realtime_market_data_required: bool
    realtime_options_required: bool
    max_stock_data_delay_minutes: float
    option_require_bid_ask: bool
    option_require_fresh_quote: bool
    option_min_dte: int
    option_preferred_min_dte: int
    option_preferred_max_dte: int
    option_max_dte: int

    @property
    def is_production(self):

        return self.app_env.lower() == "production"


def get_settings():

    return RuntimeSettings(
        app_env=get_secret_env(
            "APP_ENV",
            get_secret_env(
                "ENV",
                "development"
            )
        ),
        use_mock_market_data=get_bool_env(
            "USE_MOCK_MARKET_DATA",
            False
        ),
        use_mock_options=get_bool_env(
            "USE_MOCK_OPTIONS",
            False
        ),
        enable_ai_summary=get_bool_env(
            "ENABLE_AI_SUMMARY",
            False
        ),
        scanner_ai_summary_enabled=get_bool_env(
            "SCANNER_AI_SUMMARY_ENABLED",
            False
        ),
        scanner_debug=get_bool_env(
            "SCANNER_DEBUG",
            False
        ),
        ai_request_timeout_seconds=get_float_env(
            "AI_REQUEST_TIMEOUT_SECONDS",
            10
        ),
        polygon_api_key=get_secret_env(
            "POLYGON_API_KEY",
            ""
        ).strip(),
        openai_api_key=(
            get_secret_env(
                "OPENAI_API_KEY_APP",
                ""
            )
            or get_secret_env(
                "OPENAI_API_KEY",
                ""
            )
        ).strip(),
        openai_dashboard_model=get_secret_env(
            "OPENAI_DASHBOARD_MODEL",
            "gpt-4.1-mini"
        ),
        openai_summary_max_tokens=get_int_env(
            "OPENAI_SUMMARY_MAX_TOKENS",
            220
        ),
        ai_summary_cache_file=get_secret_env(
            "AI_SUMMARY_CACHE_FILE",
            "app/state/ai_summary_cache.json"
        ),
        scanner_output_file=get_secret_env(
            "SCANNER_OUTPUT_FILE",
            "scanner_output.xlsx"
        ),
        polygon_base_url=get_secret_env(
            "POLYGON_BASE_URL",
            "https://api.polygon.io"
        ).rstrip("/"),
        account_size=get_float_env(
            "ACCOUNT_SIZE",
            get_float_env(
                "DAILY_START_CAPITAL",
                2000
            )
        ),
        risk_percent=get_float_env(
            "RISK_PERCENT",
            get_float_env(
                "OPTION_MAX_RISK_PER_TRADE_PCT",
                0.10
            ) * 100
        ),
        max_contracts_per_trade=get_int_env(
            "MAX_CONTRACTS_PER_TRADE",
            1
        ),
        option_stop_loss_pct=get_float_env(
            "OPTION_STOP_LOSS_PCT",
            0.20
        ),
        event_blocker_enabled=get_bool_env(
            "EVENT_BLOCKER_ENABLED",
            True
        ),
        event_blocker_dates=get_secret_env(
            "EVENT_BLOCKER_DATES",
            ""
        ),
        event_blocker_days_before=get_int_env(
            "EVENT_BLOCKER_DAYS_BEFORE",
            1
        ),
        option_min_volume=get_int_env(
            "OPTION_MIN_VOLUME",
            100
        ),
        option_min_open_interest=get_int_env(
            "OPTION_MIN_OPEN_INTEREST",
            500
        ),
        option_max_spread_pct=get_float_env(
            "OPTION_MAX_SPREAD_PCT",
            10
        ),
        option_min_quality_score=get_int_env(
            "OPTION_MIN_QUALITY_SCORE",
            65
        ),
        option_delayed_quote_minutes=get_int_env(
            "OPTION_DELAYED_QUOTE_MINUTES",
            10
        ),
        option_max_quote_age_minutes=get_int_env(
            "OPTION_MAX_QUOTE_AGE_MINUTES",
            30
        ),
        option_allow_0dte=get_bool_env(
            "OPTION_ALLOW_0DTE",
            False
        ),
        option_allow_1dte=get_bool_env(
            "OPTION_ALLOW_1DTE",
            False
        ),
        realtime_market_data_required=get_bool_env(
            "REALTIME_MARKET_DATA_REQUIRED",
            False
        ),
        realtime_options_required=get_bool_env(
            "REALTIME_OPTIONS_REQUIRED",
            False
        ),
        max_stock_data_delay_minutes=get_float_env(
            "MAX_STOCK_DATA_DELAY_MINUTES",
            2
        ),
        option_require_bid_ask=get_bool_env(
            "OPTION_REQUIRE_BID_ASK",
            False
        ),
        option_require_fresh_quote=get_bool_env(
            "OPTION_REQUIRE_FRESH_QUOTE",
            False
        ),
        option_min_dte=get_int_env(
            "OPTION_MIN_DTE",
            10
        ),
        option_preferred_min_dte=get_int_env(
            "OPTION_PREFERRED_MIN_DTE",
            14
        ),
        option_preferred_max_dte=get_int_env(
            "OPTION_PREFERRED_MAX_DTE",
            30
        ),
        option_max_dte=get_int_env(
            "OPTION_MAX_DTE",
            45
        )
    )


settings = get_settings()


def print_runtime_banner():

    print(
        "[RUNTIME] "
        f"env={settings.app_env} "
        f"mock_market_data={settings.use_mock_market_data} "
        f"mock_options={settings.use_mock_options} "
        f"ai_summary={settings.enable_ai_summary} "
        f"scanner_ai={settings.scanner_ai_summary_enabled} "
        f"debug={settings.scanner_debug} "
        f"ai_timeout={settings.ai_request_timeout_seconds}s "
        f"scanner_output={settings.scanner_output_file} "
        f"event_blocker={settings.event_blocker_enabled} "
        f"event_days={settings.event_blocker_days_before} "
        f"option_spread_max={settings.option_max_spread_pct}% "
        f"option_min_vol={settings.option_min_volume} "
        f"option_min_oi={settings.option_min_open_interest} "
        f"option_max_quote_age={settings.option_max_quote_age_minutes}m "
        f"option_dte_pref={settings.option_preferred_min_dte}-"
        f"{settings.option_preferred_max_dte} "
        f"option_dte_allowed={settings.option_min_dte}-"
        f"{settings.option_max_dte} "
        f"rt_stock_required={settings.realtime_market_data_required} "
        f"rt_options_required={settings.realtime_options_required}"
    )


def validate_runtime_settings():

    readiness_errors = []

    if not settings.use_mock_market_data and not settings.polygon_api_key:

        readiness_errors.append(
            "POLYGON_API_KEY is required when USE_MOCK_MARKET_DATA=false"
        )

    if not settings.use_mock_options and not settings.polygon_api_key:

        readiness_errors.append(
            "POLYGON_API_KEY is required when USE_MOCK_OPTIONS=false"
        )

    if settings.use_mock_market_data:

        readiness_errors.append(
            "USE_MOCK_MARKET_DATA must be false for Monday live/delayed scans"
        )

    if settings.use_mock_options:

        readiness_errors.append(
            "USE_MOCK_OPTIONS must be false for Monday live/delayed option checks"
        )

    if settings.option_allow_0dte:

        readiness_errors.append(
            "OPTION_ALLOW_0DTE must be false for the current safety workflow"
        )

    if settings.option_allow_1dte:

        readiness_errors.append(
            "OPTION_ALLOW_1DTE must be false for the current safety workflow"
        )

    if settings.realtime_options_required and not settings.option_require_bid_ask:

        readiness_errors.append(
            "OPTION_REQUIRE_BID_ASK must be true when REALTIME_OPTIONS_REQUIRED=true"
        )

    if settings.realtime_options_required and not settings.option_require_fresh_quote:

        readiness_errors.append(
            "OPTION_REQUIRE_FRESH_QUOTE must be true when REALTIME_OPTIONS_REQUIRED=true"
        )

    if (
        settings.option_max_spread_pct <= 0
        or settings.option_max_spread_pct > 15
    ):

        readiness_errors.append(
            "OPTION_MAX_SPREAD_PCT must be > 0 and <= 15"
        )

    if not (
        2 <= settings.option_min_dte
        <= settings.option_preferred_min_dte
        <= settings.option_preferred_max_dte
        <= settings.option_max_dte
    ):

        readiness_errors.append(
            "DTE settings must satisfy 2 <= OPTION_MIN_DTE <= "
            "OPTION_PREFERRED_MIN_DTE <= OPTION_PREFERRED_MAX_DTE <= "
            "OPTION_MAX_DTE"
        )

    try:

        output_path = Path(settings.scanner_output_file)
        output_dir = output_path.parent

        if str(output_dir) in ["", "."]:

            output_dir = Path(".")

        output_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        probe_path = output_dir / ".scanner_output_write_probe.tmp"

        with open(probe_path, "w", encoding="utf-8") as probe_file:

            probe_file.write("ok")

        probe_path.unlink(missing_ok=True)

    except Exception as e:

        readiness_errors.append(
            f"SCANNER_OUTPUT_FILE directory is not writable: {e}"
        )

    if readiness_errors:

        raise RuntimeError(
            "Startup readiness check failed:\n- "
            + "\n- ".join(readiness_errors)
        )

    if settings.enable_ai_summary and not settings.openai_api_key:

        print(
            "[RUNTIME WARNING] ENABLE_AI_SUMMARY=true but "
            "OPENAI_API_KEY is not set; AI summaries will fall back."
        )