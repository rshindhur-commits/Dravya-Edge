import json

from app.config.settings import settings
from app.utils.runtime_logging import debug_print

def generate_trade_summary(symbol, price, analysis):

    if not settings.enable_ai_summary:

        return {
            "bias": "SKIPPED",
            "confidence": 0,
            "strategy": "WAIT",
            "entry_type": "NONE",
            "expiration_style": "NONE",
            "risk_level": "N/A",
            "summary": "AI summary disabled"
        }

    if not settings.openai_api_key:

        return {
            "bias": "UNKNOWN",
            "confidence": 0,
            "strategy": "WAIT",
            "entry_type": "NONE",
            "expiration_style": "NONE",
            "risk_level": "HIGH",
            "summary": "AI summary unavailable: OPENAI_API_KEY is not set"
        }

    try:

        from openai import OpenAI

    except ImportError as e:

        print(
            f"[AI ERROR] OpenAI package unavailable: {e}"
        )

        return {
            "bias": "UNKNOWN",
            "confidence": 0,
            "strategy": "WAIT",
            "entry_type": "NONE",
            "expiration_style": "NONE",
            "risk_level": "HIGH",
            "summary": "AI summary unavailable"
        }

    client = OpenAI(
        api_key=settings.openai_api_key,
        timeout=settings.ai_request_timeout_seconds
    )

    prompt = f"""
    Analyze this momentum trading setup.

    Symbol: {symbol}
    Current Price: {price}

    Signal: {analysis['signal']}
    Score: {analysis['score']}

    Reasons:
    {', '.join(analysis['reasons'])}

    Return ONLY valid JSON with this structure:

    {{
      "bias": "",
      "confidence": 0,
      "strategy": "",
      "entry_type": "",
      "expiration_style": "",
      "risk_level": "",
      "summary": ""
    }}

    Rules:
    - confidence should be 1-10
    - strategy should be CALL, PUT, or WAIT
    - Keep summary concise and trader-focused
    """

    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.3
        )
        content = (
            response
            .choices[0]
            .message
            .content
        )

        debug_print(
            "[RAW AI RESPONSE]",
            repr(content)
        )        

        content = content.strip()

        if content.startswith("```json"):
            content = (
                content
                .replace("```json", "")
                .replace("```", "")
                .strip()
            )

        elif content.startswith("```"):
            content = (
                content
                .replace("```", "")
                .strip()
            )

        return json.loads(content)

    except Exception as e:

        status_code = getattr(
            e,
            "status_code",
            None
        )

        print(
            f"[AI ERROR] "
            f"{type(e).__name__} "
            f"status={status_code}"
        )

        return {
            "bias": "UNKNOWN",
            "confidence": 0,
            "strategy": "WAIT",
            "entry_type": "NONE",
            "expiration_style": "NONE",
            "risk_level": "HIGH",
            "summary": "AI summary unavailable"
        }