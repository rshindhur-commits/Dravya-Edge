from app.db.repository_base import BestEffortRepository


class QuoteAttributionRepository(BestEffortRepository):

    def batch_upsert(self, rows):

        return self._batch_execute("""
            INSERT INTO quote_attribution (
                attribution_id, trading_day, scan_id, scanner_timestamp, symbol,
                option_ticker, quote_timestamp, quote_age_seconds,
                source_timestamp_field, quote_source, allowed_age_seconds,
                final_classification, reason
            ) VALUES (
                :attribution_id, CAST(:trading_day AS DATE), :scan_id,
                CAST(:scanner_timestamp AS TIMESTAMPTZ), :symbol, :option_ticker,
                CAST(:quote_timestamp AS TIMESTAMPTZ), CAST(:quote_age_seconds AS DOUBLE PRECISION),
                :source_timestamp_field, :quote_source, CAST(:allowed_age_seconds AS DOUBLE PRECISION),
                :final_classification, :reason
            ) ON CONFLICT (attribution_id) DO UPDATE SET
                quote_timestamp = EXCLUDED.quote_timestamp,
                quote_age_seconds = EXCLUDED.quote_age_seconds,
                source_timestamp_field = EXCLUDED.source_timestamp_field,
                quote_source = EXCLUDED.quote_source,
                allowed_age_seconds = EXCLUDED.allowed_age_seconds,
                final_classification = EXCLUDED.final_classification,
                reason = EXCLUDED.reason
        """, rows)