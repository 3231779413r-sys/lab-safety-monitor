import unittest

import numpy as np

from app.ml.osnet_reid import GlobalReIDService
from app.ml.tracker import DeepSORTTracker


class ReIDComponentTests(unittest.TestCase):
    def test_global_reid_service_can_match_identity(self):
        service = GlobalReIDService(extractor=None, match_threshold=0.6, max_features_per_person=8, feature_dim=4)
        feature = np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32)
        feature /= np.linalg.norm(feature)

        service.upsert_identity(
            person_id="person_123",
            feature=feature,
            person_name="张三",
            identity_data={"subject_type": "employee", "subject_supervision_scope": ["hardhat"]},
            camera_id="cam-1",
            face_verified=True,
            index_identity=True,
        )

        query = np.array([0.09, 0.21, 0.31, 0.39], dtype=np.float32)
        query /= np.linalg.norm(query)
        person_id, score = service.search(query)

        self.assertEqual(person_id, "person_123")
        self.assertGreater(score, 0.95)
        record = service.get_identity("person_123")
        self.assertIsNotNone(record)
        self.assertEqual(record.person_name, "张三")
        self.assertEqual(record.identity_data["subject_type"], "employee")
        self.assertEqual(record.identity_data["subject_supervision_scope"], ["hardhat"])
        self.assertEqual(len(record.features), 1)

    def test_global_reid_service_can_rename_identity(self):
        service = GlobalReIDService(extractor=None, match_threshold=0.6, max_features_per_person=8, feature_dim=4)
        feature = np.array([0.3, 0.1, 0.4, 0.2], dtype=np.float32)
        feature /= np.linalg.norm(feature)
        service.upsert_identity(
            person_id="reid_unknown:1",
            feature=feature,
            person_name="未知人员",
            identity_data={"subject_type": "unknown"},
            camera_id="cam-1",
            face_verified=False,
            index_identity=False,
        )
        service.rename_identity(
            "reid_unknown:1",
            "employee-1",
            target_name="李四",
            target_identity_data={"subject_type": "employee"},
        )
        record = service.get_identity("employee-1")
        self.assertIsNotNone(record)
        self.assertEqual(record.person_name, "李四")
        self.assertEqual(record.identity_data["subject_type"], "employee")
        self.assertIsNone(service.get_identity("reid_unknown:1"))

    def test_tracker_reuses_track_for_same_person(self):
        tracker = DeepSORTTracker()
        appearance = np.array([0.2, 0.4, 0.6, 0.8], dtype=np.float32)
        appearance /= np.linalg.norm(appearance)

        detections_a = [{"box": [10.0, 20.0, 80.0, 180.0], "appearance_feature": appearance}]
        tracker.update(detections_a)
        first_track_id = detections_a[0].get("reid_track_id")

        detections_b = [{"box": [14.0, 22.0, 84.0, 182.0], "appearance_feature": appearance}]
        tracker.update(detections_b)
        second_track_id = detections_b[0].get("reid_track_id")

        self.assertIsNotNone(first_track_id)
        self.assertEqual(first_track_id, second_track_id)


if __name__ == "__main__":
    unittest.main()
