import unittest

from sqlalchemy.dialects import postgresql

from app.api.routes.events import _build_violation_type_filter
from app.core.danger_events import (
    canonicalize_danger_event_key,
    canonicalize_danger_event_values,
    expand_danger_event_filter_values,
)


class DangerEventFilterTests(unittest.TestCase):
    def test_expand_filter_values_includes_legacy_aliases(self):
        self.assertEqual(
            expand_danger_event_filter_values("hardhat"),
            ["hardhat", "hard_hat", "no_hardhat", "no_hard_hat"],
        )
        self.assertEqual(
            expand_danger_event_filter_values("safety_shoes"),
            ["safety_shoes", "protective_shoes", "no_safety_shoes", "no_protective_shoes"],
        )
        self.assertEqual(
            expand_danger_event_filter_values("work_clothes"),
            ["work_clothes", "no_work_clothes"],
        )

    def test_canonicalization_normalizes_legacy_aliases(self):
        self.assertEqual(canonicalize_danger_event_key("hard_hat"), "hardhat")
        self.assertEqual(canonicalize_danger_event_key("vest"), "safety_vest")
        self.assertEqual(canonicalize_danger_event_key("no_vest"), "safety_vest")
        self.assertEqual(
            canonicalize_danger_event_values(["hard_hat", "vest", "work_clothes"]),
            ["hardhat", "safety_vest", "work_clothes"],
        )

    def test_violation_type_filter_matches_normalized_and_legacy_values(self):
        expression = _build_violation_type_filter("hardhat")
        compiled = expression.compile()

        self.assertIn("CAST(compliance_events.missing_ppe AS VARCHAR)", str(compiled))
        self.assertIn("CAST(compliance_events.action_violations AS VARCHAR)", str(compiled))
        self.assertIn('%"hardhat"%', compiled.params.values())
        self.assertIn('%"no_hardhat"%', compiled.params.values())

    def test_postgresql_filter_avoids_jsonb_danger_event_types_lookup(self):
        expression = _build_violation_type_filter("area_missed_inspection")
        compiled = str(expression.compile(dialect=postgresql.dialect()))

        self.assertNotIn("compliance_events.danger_event_types", compiled)
        self.assertIn("CAST(compliance_events.missing_ppe AS VARCHAR)", compiled)
        self.assertIn("CAST(compliance_events.action_violations AS VARCHAR)", compiled)


if __name__ == "__main__":
    unittest.main()
