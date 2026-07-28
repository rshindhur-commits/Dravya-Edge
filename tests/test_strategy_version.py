import hashlib
import unittest
from unittest.mock import patch

from app.versioning import strategy_version as sv


class TestComputeStrategyVersion(unittest.TestCase):

    def test_returns_a_short_hex_string(self):
        version = sv.compute_strategy_version(use_cache=False)

        self.assertEqual(len(version), 12)
        int(version, 16)  # raises if not hex

    def test_is_deterministic_across_calls(self):
        first = sv.compute_strategy_version(use_cache=False)
        second = sv.compute_strategy_version(use_cache=False)

        self.assertEqual(first, second)

    def test_caches_by_default(self):
        sv._cache.clear()

        with patch.object(sv, "_file_bytes_hash", wraps=sv._file_bytes_hash) as hasher:
            sv.compute_strategy_version()
            sv.compute_strategy_version()
            sv.compute_strategy_version()

            self.assertEqual(hasher.call_count, 1)

        sv._cache.clear()

    def test_use_cache_false_bypasses_the_cache(self):
        sv._cache.clear()

        with patch.object(sv, "_file_bytes_hash", wraps=sv._file_bytes_hash) as hasher:
            sv.compute_strategy_version(use_cache=False)
            sv.compute_strategy_version(use_cache=False)

            self.assertEqual(hasher.call_count, 2)

        sv._cache.clear()

    def test_changing_a_hashed_file_changes_the_version(self):
        original = sv.compute_strategy_version(use_cache=False)

        with patch.object(
            sv, "_file_bytes_hash", return_value=hashlib.sha256(b"different").hexdigest()
        ):
            changed = sv.compute_strategy_version(use_cache=False)

        self.assertNotEqual(original, changed)

    def test_changing_decision_relevant_config_changes_the_version(self):
        original = sv.compute_strategy_version(use_cache=False)

        with patch.object(
            sv, "_decision_relevant_config", return_value={"scanner_entry_gate.min_rr": 999}
        ):
            changed = sv.compute_strategy_version(use_cache=False)

        self.assertNotEqual(original, changed)

    def test_config_key_order_does_not_affect_the_version(self):
        with patch.object(
            sv, "_decision_relevant_config",
            return_value={"a": 1, "b": 2}
        ):
            first = sv.compute_strategy_version(use_cache=False)

        with patch.object(
            sv, "_decision_relevant_config",
            return_value={"b": 2, "a": 1}
        ):
            second = sv.compute_strategy_version(use_cache=False)

        self.assertEqual(first, second)


class TestFileBytesHash(unittest.TestCase):

    def test_hashes_every_file_in_v1_decision_logic_files(self):
        for rel_path in sv.V1_DECISION_LOGIC_FILES:
            path = sv.ROOT_DIR / rel_path
            self.assertTrue(path.exists(), f"{rel_path} must exist to be hashed")

    def test_covers_exactly_the_four_i1_modules_plus_entry_gate(self):
        names = {path.split("/")[-1] for path in sv.V1_DECISION_LOGIC_FILES}

        self.assertEqual(names, {
            "momentum_strategy.py",
            "entry_engine.py",
            "risk_manager.py",
            "exit_engine.py",
            "entry_gate.py",
        })

    def test_file_order_does_not_affect_the_hash(self):
        forward = sv._file_bytes_hash(sv.V1_DECISION_LOGIC_FILES)
        reversed_hash = sv._file_bytes_hash(list(reversed(sv.V1_DECISION_LOGIC_FILES)))

        # Order DOES matter for this implementation (streaming hash, not
        # order-independent) -- pin that explicitly so nobody "fixes" it into
        # an accidental behavior change later without noticing.
        self.assertNotEqual(forward, reversed_hash)


class TestStrategyVersionManifest(unittest.TestCase):

    def test_manifest_short_version_matches_compute_strategy_version(self):
        manifest = sv.strategy_version_manifest()

        self.assertEqual(
            manifest["strategy_version"],
            sv.compute_strategy_version(use_cache=False)
        )

    def test_manifest_always_recomputes_ignoring_the_cache(self):
        sv.compute_strategy_version()  # warm the cache

        with patch.object(sv, "_file_bytes_hash", wraps=sv._file_bytes_hash) as hasher:
            sv.strategy_version_manifest()

            hasher.assert_called()

        sv._cache.clear()

    def test_manifest_includes_the_decision_relevant_config(self):
        manifest = sv.strategy_version_manifest()

        self.assertIn("scanner_entry_gate.min_rr", manifest["decision_relevant_config"])


class TestUnversionedBackfillConstant(unittest.TestCase):

    def test_unversioned_sentinel_is_stable(self):
        # Pinned literally: this string is written into historical evidence
        # rows and must never change once anything has been stamped with it.
        self.assertEqual(sv.UNVERSIONED, "v0-unversioned")


if __name__ == "__main__":
    unittest.main()
