import time
import unittest
from unittest.mock import patch

import numpy as np

from app.ml.pipeline import DetectionPipeline


class _DummyDetector:
    def initialize(self):
        return None


class _DummyTemporalFilter:
    def clear_all(self):
        return None


class _DummyPoseAnalyzer:
    def analyze(self, person):
        return {"pose_status": "standing", "pose_actions": []}


class _DummyPoseFilter:
    def update(self, tracking_key, actions):
        return actions


class _DummyFaceRecognizer:
    min_detection_score = 0.5
    threshold = 0.45
    min_margin = 0.05

    def detect_faces(self, frame):
        return []

    def compare_embeddings(self, embedding1, embedding2):
        return float(np.dot(embedding1, embedding2))

    def is_strong_match(self, best_score, second_best_score):
        return best_score >= self.threshold


class DetectionPipelineFacePriorityTests(unittest.TestCase):
    def _build_pipeline(self) -> DetectionPipeline:
        with (
            patch("app.ml.pipeline.get_detector", return_value=_DummyDetector()),
            patch("app.ml.pipeline.get_temporal_filter", return_value=_DummyTemporalFilter()),
            patch("app.ml.pipeline.get_pose_action_analyzer", return_value=_DummyPoseAnalyzer()),
            patch("app.ml.pipeline.get_pose_action_filter", return_value=_DummyPoseFilter()),
            patch("app.ml.pipeline.get_face_recognizer", return_value=_DummyFaceRecognizer()),
            patch("app.ml.pipeline.get_reid_service", return_value=None),
        ):
            pipeline = DetectionPipeline()
        pipeline.reid_enabled = False
        pipeline.reid_service = None
        return pipeline

    def test_attach_face_identities_rechecks_when_current_frame_has_face(self):
        pipeline = self._build_pipeline()
        pipeline.frame_count = 10
        pipeline._refresh_known_faces_cache = lambda: None
        pipeline._detect_frame_faces = lambda frame, persons: [
            {
                "frame_box": [12.0, 12.0, 32.0, 32.0],
                "embedding": np.array([1.0, 0.0], dtype=np.float32),
                "thumbnail": b"face",
                "score": 0.99,
            }
        ]

        called = {"count": 0}

        def fake_match(tracking_key, matched_face, person):
            called["count"] += 1
            self.assertIsNotNone(matched_face)
            return {
                "person_id": "emp-b",
                "person_name": "B",
                "face_matched": True,
                "identity_source": "face",
                "face_observed_this_frame": True,
                "face_confirmed_this_frame": True,
                "subject_type": "employee",
                "subject_supervision_scope": [],
                "allowed_camera_ids": [],
                "appointment_start": None,
                "appointment_end": None,
                "external_person_id": None,
                "face_embedding": matched_face["embedding"],
                "thumbnail": matched_face["thumbnail"],
                "tracking_key": tracking_key,
                "last_attempt_frame": pipeline.frame_count,
                "last_seen_at": time.monotonic(),
            }

        pipeline._match_face_identity = fake_match
        pipeline._face_identity_cache["track:1"] = {
            "person_id": "emp-a",
            "person_name": "A",
            "face_matched": True,
            "identity_source": "face",
            "tracking_key": "track:1",
            "last_attempt_frame": pipeline.frame_count - 1,
            "last_seen_at": time.monotonic(),
        }

        persons = [{"track_id": 1, "id": 1, "box": [0.0, 0.0, 100.0, 200.0]}]
        pipeline._attach_face_identities(np.zeros((64, 64, 3), dtype=np.uint8), persons)

        self.assertEqual(called["count"], 1)
        self.assertEqual(persons[0]["person_id"], "emp-b")
        self.assertEqual(persons[0]["person_name"], "B")
        self.assertTrue(persons[0]["face_confirmed_this_frame"])

    def test_resolve_reid_identities_prefers_current_frame_face_match_over_cached_name(self):
        pipeline = self._build_pipeline()
        pipeline.frame_count = 20
        pipeline._face_identity_cache["track:1"] = {
            "person_id": "emp-a",
            "person_name": "A",
            "face_matched": True,
            "identity_source": "face",
            "tracking_key": "track:1",
            "last_attempt_frame": pipeline.frame_count - 1,
            "last_seen_at": time.monotonic(),
        }

        stale_person = {
            "track_id": 1,
            "id": 1,
            "tracking_key": "track:1",
            "person_id": "emp-a",
            "person_name": "A",
            "face_matched": True,
            "face_observed_this_frame": False,
            "face_confirmed_this_frame": False,
            "face_embedding": None,
            "appearance_feature": None,
            "identity_source": "face",
        }
        confirmed_person = {
            "track_id": 2,
            "id": 2,
            "tracking_key": "track:2",
            "person_id": "emp-a",
            "person_name": "A",
            "face_matched": True,
            "face_observed_this_frame": True,
            "face_confirmed_this_frame": True,
            "face_embedding": np.array([1.0, 0.0], dtype=np.float32),
            "appearance_feature": None,
            "identity_source": "face",
            "subject_type": "employee",
            "subject_supervision_scope": [],
            "allowed_camera_ids": [],
            "appointment_start": None,
            "appointment_end": None,
            "external_person_id": None,
        }

        pipeline._resolve_reid_identities([stale_person, confirmed_person])

        self.assertEqual(confirmed_person["person_id"], "emp-a")
        self.assertEqual(confirmed_person["identity_source"], "face")
        self.assertFalse(stale_person["face_matched"])
        self.assertEqual(stale_person["identity_source"], "unknown")
        self.assertTrue(str(stale_person["person_id"]).startswith("reid_unknown:"))
        self.assertNotIn("track:1", pipeline._face_identity_cache)
        self.assertEqual(pipeline._last_face_tracking_key_by_person.get("emp-a"), "track:2")
        self.assertEqual(confirmed_person["tracking_key"], "stable:1")

    def test_assign_faces_to_persons_uses_best_global_match_not_input_order(self):
        pipeline = self._build_pipeline()
        persons = [
            {"track_id": 1, "id": 1, "box": [0.0, 0.0, 90.0, 200.0]},
            {"track_id": 2, "id": 2, "box": [80.0, 0.0, 180.0, 200.0]},
        ]
        detected_faces = [
            {
                "frame_box": [120.0, 20.0, 150.0, 60.0],
                "embedding": np.array([1.0, 0.0], dtype=np.float32),
                "thumbnail": b"face",
                "score": 0.99,
            }
        ]

        assignments = pipeline._assign_faces_to_persons(persons, detected_faces)

        self.assertEqual(assignments, {1: 0})


if __name__ == "__main__":
    unittest.main()
