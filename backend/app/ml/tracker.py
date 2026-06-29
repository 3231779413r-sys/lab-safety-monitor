"""
DeepSORT Multi-Object Tracker

Provides consistent person tracking across frames even when faces are not visible.
Uses a combination of:
1. Kalman filter for motion prediction
2. Deep appearance features for re-identification
3. Hungarian algorithm for optimal assignment
"""

import numpy as np
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, field
from scipy.optimize import linear_sum_assignment
from filterpy.kalman import KalmanFilter


@dataclass
class Track:
    """Represents a tracked person."""

    track_id: int
    kalman_filter: KalmanFilter
    hits: int = 1  # Number of consecutive detections
    age: int = 0  # Frames since creation
    time_since_update: int = 0  # Frames since last detection
    state: str = "tentative"  # tentative, confirmed, deleted
    appearance_features: List[np.ndarray] = field(default_factory=list)
    person_id: Optional[str] = None  # Linked face recognition ID

    # Track history for visualization
    positions: List[Tuple[float, float]] = field(default_factory=list)

    def get_state(self) -> np.ndarray:
        """Get current [x, y, w, h] from Kalman filter."""
        state = self.kalman_filter.x
        return np.array([state[0], state[1], state[2], state[3]]).flatten()

    def get_box(self) -> List[float]:
        """Get current bounding box [x1, y1, x2, y2] as native Python floats."""
        x, y, w, h = self.get_state()
        return [float(x - w / 2), float(y - h / 2), float(x + w / 2), float(y + h / 2)]


class DeepSORTTracker:
    """
    DeepSORT tracker implementation.

    Maintains tracks across frames using motion prediction and appearance features.
    """

    # Thresholds
    MAX_AGE = 90  # Max frames to keep unmatched track (increased from 30)
    MIN_HITS = 3  # Min hits before track is confirmed
    IOU_THRESHOLD = 0.3  # Min IOU for matching
    APPEARANCE_THRESHOLD = 0.7  # Max cosine distance for appearance matching
    MAX_APPEARANCE_FEATURES = 100  # Max features to store per track

    def __init__(self, person_gallery=None):
        self.tracks: List[Track] = []
        self.next_id = 1
        self.frame_count = 0
        self.person_gallery = person_gallery
        self._on_track_deleted_callback = None

    def _create_kalman_filter(self, bbox: List[float]) -> KalmanFilter:
        """
        Create Kalman filter for tracking.

        State: [x, y, w, h, vx, vy, vw, vh]
        Measurement: [x, y, w, h]
        """
        kf = KalmanFilter(dim_x=8, dim_z=4)

        # State transition matrix (constant velocity model)
        kf.F = np.array(
            [
                [1, 0, 0, 0, 1, 0, 0, 0],
                [0, 1, 0, 0, 0, 1, 0, 0],
                [0, 0, 1, 0, 0, 0, 1, 0],
                [0, 0, 0, 1, 0, 0, 0, 1],
                [0, 0, 0, 0, 1, 0, 0, 0],
                [0, 0, 0, 0, 0, 1, 0, 0],
                [0, 0, 0, 0, 0, 0, 1, 0],
                [0, 0, 0, 0, 0, 0, 0, 1],
            ]
        )

        # Measurement matrix
        kf.H = np.array(
            [
                [1, 0, 0, 0, 0, 0, 0, 0],
                [0, 1, 0, 0, 0, 0, 0, 0],
                [0, 0, 1, 0, 0, 0, 0, 0],
                [0, 0, 0, 1, 0, 0, 0, 0],
            ]
        )

        # Measurement noise
        kf.R *= 10.0

        # Process noise
        kf.Q[-1, -1] *= 0.01
        kf.Q[4:, 4:] *= 0.01

        # Initial covariance
        kf.P[4:, 4:] *= 1000.0
        kf.P *= 10.0

        # Initialize state from bbox [x1, y1, x2, y2]
        x1, y1, x2, y2 = bbox
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2
        w = x2 - x1
        h = y2 - y1

        kf.x = np.array([[cx], [cy], [w], [h], [0], [0], [0], [0]])

        return kf

    def _iou(self, box1: List[float], box2: List[float]) -> float:
        """Calculate Intersection over Union."""
        x1 = max(box1[0], box2[0])
        y1 = max(box1[1], box2[1])
        x2 = min(box1[2], box2[2])
        y2 = min(box1[3], box2[3])

        if x2 < x1 or y2 < y1:
            return 0.0

        intersection = (x2 - x1) * (y2 - y1)
        area1 = (box1[2] - box1[0]) * (box1[3] - box1[1])
        area2 = (box2[2] - box2[0]) * (box2[3] - box2[1])
        union = area1 + area2 - intersection

        return intersection / union if union > 0 else 0.0

    def _cosine_distance(
        self, features1: List[np.ndarray], feature2: np.ndarray
    ) -> float:
        """Calculate minimum cosine distance between feature and feature list."""
        if not features1 or feature2 is None:
            return 1.0

        distances = []
        for f1 in features1[-self.MAX_APPEARANCE_FEATURES :]:
            f1_norm = f1 / (np.linalg.norm(f1) + 1e-6)
            f2_norm = feature2 / (np.linalg.norm(feature2) + 1e-6)
            similarity = np.dot(f1_norm.flatten(), f2_norm.flatten())
            distances.append(1 - similarity)

        return min(distances)

    def _compute_cost_matrix(
        self,
        tracks: List[Track],
        detections: List[Dict],
        use_appearance: bool = True,
    ) -> np.ndarray:
        """
        Compute cost matrix for track-detection assignment.

        Combines IOU distance and appearance distance.
        """
        if not tracks or not detections:
            return np.empty((0, 0))

        cost_matrix = np.zeros((len(tracks), len(detections)))

        for i, track in enumerate(tracks):
            track_box = track.get_box()

            for j, det in enumerate(detections):
                det_box = det.get("box", [0, 0, 0, 0])

                # IOU distance (1 - IOU)
                iou_dist = 1 - self._iou(track_box, det_box)

                # Appearance distance
                if use_appearance and det.get("appearance_feature") is not None:
                    app_dist = self._cosine_distance(
                        track.appearance_features, det["appearance_feature"]
                    )
                    # Weighted combination (IOU weight 0.4, appearance weight 0.6)
                    cost = 0.4 * iou_dist + 0.6 * app_dist
                else:
                    cost = iou_dist

                cost_matrix[i, j] = cost

        return cost_matrix

    def update(self, detections: List[Dict]) -> List[Dict]:
        """
        Update tracker with new detections.

        Args:
            detections: List of dicts with 'box' [x1,y1,x2,y2] and optional 'appearance_feature'

        Returns:
            List of tracked objects with track_id assigned
        """
        self.frame_count += 1

        # Predict new locations for all tracks
        for track in self.tracks:
            track.kalman_filter.predict()
            track.age += 1
            track.time_since_update += 1

        # Split tracks into confirmed and tentative
        confirmed_tracks = [t for t in self.tracks if t.state == "confirmed"]
        tentative_tracks = [t for t in self.tracks if t.state == "tentative"]

        # Match confirmed tracks first (with appearance)
        matched_tracks, matched_dets, unmatched_tracks_conf, unmatched_dets = (
            self._match(confirmed_tracks, detections, use_appearance=True)
        )

        unmatched_detection_pool = [detections[index] for index in unmatched_dets]
        matched_tent, matched_dets_tent_local, unmatched_tracks_tent, unmatched_dets_tent_local = (
            self._match(tentative_tracks, unmatched_detection_pool, use_appearance=False)
        )
        matched_dets_tent = [unmatched_dets[index] for index in matched_dets_tent_local]
        unmatched_dets_final = [unmatched_dets[index] for index in unmatched_dets_tent_local]

        detection_track_map: Dict[int, Track] = {}

        # Update matched tracks
        for track_idx, det_idx in zip(matched_tracks, matched_dets):
            track = confirmed_tracks[track_idx]
            self._update_track(track, detections[det_idx])
            detection_track_map[det_idx] = track

        for track_idx, det_idx in zip(matched_tent, matched_dets_tent):
            track = tentative_tracks[track_idx]
            self._update_track(track, detections[det_idx])
            detection_track_map[det_idx] = track

        # Create new tracks for unmatched detections
        for det_idx in unmatched_dets_final:
            track = self._create_track(detections[det_idx])
            detection_track_map[det_idx] = track

        # Mark unmatched tracks
        unmatched_track_objs = [confirmed_tracks[i] for i in unmatched_tracks_conf] + [
            tentative_tracks[i] for i in unmatched_tracks_tent
        ]
        for track in unmatched_track_objs:
            # No update needed, time_since_update already incremented
            pass

        # Update track states
        self._update_track_states()

        for det_idx, track in detection_track_map.items():
            detection = detections[det_idx]
            detection["reid_track_id"] = int(track.track_id)
            detection["reid_track_state"] = track.state
            detection["reid_track_hits"] = int(track.hits)
            detection["reid_track_age"] = int(track.age)
            detection["reid_person_id"] = track.person_id

        # Return results
        results = []
        for track in self.tracks:
            if track.state != "deleted":
                box = track.get_box()
                results.append(
                    {
                        "track_id": int(track.track_id),
                        "box": box,
                        "state": track.state,
                        "person_id": track.person_id,
                        "age": int(track.age),
                        "hits": int(track.hits),
                        "time_since_update": int(track.time_since_update),
                    }
                )

        return results

    def _match(
        self,
        tracks: List[Track],
        detections: List[Dict],
        use_appearance: bool,
    ) -> Tuple[List[int], List[int], List[int], List[int]]:
        """
        Match tracks to detections using Hungarian algorithm.

        Returns:
            matched_tracks: indices of matched tracks
            matched_dets: indices of matched detections
            unmatched_tracks: indices of unmatched tracks
            unmatched_dets: indices of unmatched detections
        """
        if not tracks or not detections:
            return [], [], list(range(len(tracks))), list(range(len(detections)))

        cost_matrix = self._compute_cost_matrix(tracks, detections, use_appearance)

        # Apply threshold gate
        threshold = (
            self.APPEARANCE_THRESHOLD if use_appearance else (1 - self.IOU_THRESHOLD)
        )
        cost_matrix[cost_matrix > threshold] = threshold + 1

        # Hungarian algorithm
        row_indices, col_indices = linear_sum_assignment(cost_matrix)

        matched_tracks = []
        matched_dets = []
        for row, col in zip(row_indices, col_indices):
            if cost_matrix[row, col] <= threshold:
                matched_tracks.append(int(row))
                matched_dets.append(int(col))

        unmatched_tracks = [i for i in range(len(tracks)) if i not in matched_tracks]
        unmatched_dets = [i for i in range(len(detections)) if i not in matched_dets]

        return matched_tracks, matched_dets, unmatched_tracks, unmatched_dets

    def _update_track(self, track: Track, detection: Dict):
        """Update track with matched detection."""
        box = detection.get("box", [0, 0, 0, 0])
        x1, y1, x2, y2 = box
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2
        w = x2 - x1
        h = y2 - y1

        # Kalman update
        track.kalman_filter.update(np.array([[cx], [cy], [w], [h]]))

        # Update appearance features
        if detection.get("appearance_feature") is not None:
            track.appearance_features.append(detection["appearance_feature"])
            if len(track.appearance_features) > self.MAX_APPEARANCE_FEATURES:
                track.appearance_features = track.appearance_features[
                    -self.MAX_APPEARANCE_FEATURES :
                ]

        track.hits += 1
        track.time_since_update = 0

        # Store position for trajectory
        track.positions.append((cx, cy))
        if len(track.positions) > 100:
            track.positions = track.positions[-100:]

    def _create_track(self, detection: Dict) -> Track:
        """Create new track from detection."""
        box = detection.get("box", [0, 0, 0, 0])
        kf = self._create_kalman_filter(box)

        track = Track(
            track_id=self.next_id,
            kalman_filter=kf,
            hits=1,
            age=1,
            time_since_update=0,
            state="tentative",
        )

        if detection.get("appearance_feature") is not None:
            track.appearance_features.append(detection["appearance_feature"])

        # Store initial position
        x1, y1, x2, y2 = box
        track.positions.append(((x1 + x2) / 2, (y1 + y2) / 2))

        self.tracks.append(track)
        self.next_id += 1

        return track

    def _update_track_states(self):
        """Update track states and remove deleted tracks."""
        tracks_to_keep = []

        for track in self.tracks:
            was_deleted = track.state == "deleted"

            if track.state == "tentative":
                if track.hits >= self.MIN_HITS:
                    track.state = "confirmed"
                elif track.time_since_update > self.MAX_AGE:
                    track.state = "deleted"
            elif track.state == "confirmed":
                if track.time_since_update > self.MAX_AGE:
                    track.state = "deleted"

            # Callback for deleted tracks (save features to gallery)
            if track.state == "deleted" and not was_deleted:
                if self._on_track_deleted_callback is not None:
                    self._on_track_deleted_callback(track, self.frame_count)

            if track.state != "deleted":
                tracks_to_keep.append(track)

        self.tracks = tracks_to_keep

    def link_person_id(self, track_id: int, person_id: str):
        """Link a track to a face-recognized person ID."""
        for track in self.tracks:
            if track.track_id == track_id:
                track.person_id = person_id
                break

    def get_track_by_id(self, track_id: int) -> Optional[Track]:
        """Get track by ID."""
        for track in self.tracks:
            if track.track_id == track_id:
                return track
        return None

    def get_confirmed_tracks(self) -> List[Track]:
        """Get all confirmed tracks."""
        return [t for t in self.tracks if t.state == "confirmed"]

    def set_on_track_deleted(self, callback):
        """
        Set callback for when a track is deleted.

        Callback receives: (track: Track, frame_count: int)
        This allows persisting appearance features before the track is lost.
        """
        self._on_track_deleted_callback = callback

    def reset(self):
        """Reset tracker state."""
        self.tracks = []
        self.next_id = 1
        self.frame_count = 0


# Singleton instance
_tracker: Optional[DeepSORTTracker] = None


def get_tracker() -> DeepSORTTracker:
    """Get singleton tracker instance."""
    global _tracker
    if _tracker is None:
        _tracker = DeepSORTTracker()
    return _tracker
