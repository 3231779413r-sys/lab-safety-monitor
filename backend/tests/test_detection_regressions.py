import unittest
from unittest.mock import patch

from app.ml.person_filters import filter_persons_by_min_box_area_ratio
from app.ml.temporal_filter import TemporalFilter
from app.ml.yolov11_detector import YOLOv11Detector


class DetectionRegressionTests(unittest.TestCase):
    def test_binary_temporal_filter_triggers_for_missing_ppe_without_violation_confidence(self):
        temporal_filter = TemporalFilter(
            buffer_size=3,
            min_frames_for_violation=2,
            min_frames_for_clear=3,
            fusion_strategy="ema",
            ema_alpha=0.7,
            confidence_threshold=0.4,
        )

        first = temporal_filter.update("track:1", ["hard_hat"])
        second = temporal_filter.update("track:1", ["hard_hat"])

        self.assertFalse(first["is_violation"])
        self.assertTrue(second["is_violation"])
        self.assertEqual(second["stable_missing_ppe"], ["hard_hat"])

    def test_person_filter_falls_back_to_original_persons_when_threshold_filters_all(self):
        persons = [{"id": 1, "box": [0.0, 0.0, 60.0, 120.0]}]

        with patch("app.ml.person_filters.settings.PERSON_MIN_BOX_AREA_RATIO", 0.5):
            filtered = filter_persons_by_min_box_area_ratio(persons, (1080, 1920))

        self.assertEqual(filtered, persons)

    def test_positive_ppe_suppresses_conflicting_negative_violation(self):
        detector = YOLOv11Detector()
        persons = [{"id": 1, "box": [100.0, 50.0, 300.0, 400.0]}]
        ppe_detections = {
            "hardhat": [{"box": [140.0, 60.0, 260.0, 150.0], "score": 0.92}],
            "mask": [],
            "safety_vest": [],
        }
        violation_detections = {
            "hardhat": [{"box": [130.0, 55.0, 270.0, 170.0], "score": 0.88}],
        }

        associated = detector.associate_ppe_to_persons(
            persons,
            ppe_detections,
            violation_detections,
            [],
        )

        self.assertEqual(associated[0]["detected_ppe"], ["hardhat"])
        self.assertEqual(associated[0]["missing_ppe"], [])

    def test_hardhat_violation_requires_four_consecutive_frames(self):
        temporal_filter = TemporalFilter(
            buffer_size=4,
            min_frames_for_violation=2,
            min_frames_by_type={"hardhat": 4},
            min_frames_for_clear=3,
            fusion_strategy="ema",
            ema_alpha=0.7,
            confidence_threshold=0.4,
        )

        first = temporal_filter.update("track:hardhat", ["hardhat"])
        second = temporal_filter.update("track:hardhat", ["hardhat"])
        third = temporal_filter.update("track:hardhat", ["hardhat"])
        fourth = temporal_filter.update("track:hardhat", ["hardhat"])

        self.assertFalse(first["is_violation"])
        self.assertFalse(second["is_violation"])
        self.assertFalse(third["is_violation"])
        self.assertTrue(fourth["is_violation"])
        self.assertEqual(fourth["stable_missing_ppe"], ["hardhat"])

    def test_temporal_filter_canonicalizes_legacy_aliases(self):
        temporal_filter = TemporalFilter(
            buffer_size=3,
            min_frames_for_violation=2,
            min_frames_by_type={"hardhat": 2, "work_clothes": 2},
            min_frames_for_clear=3,
            fusion_strategy="ema",
            ema_alpha=0.7,
            confidence_threshold=0.4,
        )

        first = temporal_filter.update("track:alias", ["hard_hat", "no_work_clothes"])
        second = temporal_filter.update("track:alias", ["hardhat", "work_clothes"])

        self.assertFalse(first["is_violation"])
        self.assertTrue(second["is_violation"])
        self.assertCountEqual(second["stable_missing_ppe"], ["hardhat", "work_clothes"])


if __name__ == "__main__":
    unittest.main()
