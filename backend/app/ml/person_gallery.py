"""
人员重识别库

存储人员特征库（面部嵌入 + 外观特征），用于在跟踪删除和重新进入时重新识别个人。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
from datetime import datetime


@dataclass
class PersonRecord:
    """存储单个人员在多次检测中的特征。"""


    person_id: str
    face_embeddings: List[np.ndarray] = field(default_factory=list)
    appearance_features: List[np.ndarray] = field(default_factory=list)
    last_seen_frame: int = 0
    first_seen_frame: int = 0
    total_detections: int = 0
    last_seen_time: datetime = field(default_factory=datetime.now)
    name: Optional[str] = None
    identity_data: Dict[str, Any] = field(default_factory=dict)


class PersonGallery:
    """
    维护已知人员的库，用于重识别。

    匹配优先级：
    1. 面部嵌入匹配
    2. 外观特征匹配
    3. 无匹配：创建新的person_id
    """


    def __init__(
        self,
        face_threshold: float = 0.6,
        appearance_threshold: float = 0.5,
        max_features_per_person: int = 50,
    ):
        self.face_threshold = face_threshold
        self.appearance_threshold = appearance_threshold
        self.max_features_per_person = max_features_per_person

        self.persons: Dict[str, PersonRecord] = {}
        self.next_person_id = 1

    def _get_next_person_id(self) -> str:
        """生成下一个顺序人员ID。"""
        pid = f"person_{self.next_person_id}"
        self.next_person_id += 1
        return pid

    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """计算两个向量之间的余弦相似度。"""
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))

    def _mean_embedding(self, embeddings: List[np.ndarray]) -> np.ndarray:
        """计算嵌入列表的平均值。"""
        if not embeddings:
            return np.zeros(512)
        return np.mean(embeddings, axis=0)

    def add_person(
        self,
        person_id: Optional[str] = None,
        face_embedding: Optional[np.ndarray] = None,
        appearance_feature: Optional[np.ndarray] = None,
        frame_number: int = 0,
        name: Optional[str] = None,
        identity_data: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        添加新人员或更新现有人员。

        参数：
            person_id: 可选的特定ID（用于从数据库加载）
            face_embedding: 可用的面部嵌入
            appearance_feature: 可用的外观特征
            frame_number: 当前帧号

        返回：
            分配/使用的person_id
        """

        if person_id is None:
            person_id = self._get_next_person_id()

        if person_id not in self.persons:
            self.persons[person_id] = PersonRecord(
                person_id=person_id,
                first_seen_frame=frame_number,
            )

        record = self.persons[person_id]

        if face_embedding is not None:
            if len(record.face_embeddings) < self.max_features_per_person:
                record.face_embeddings.append(face_embedding)

        if appearance_feature is not None:
            if len(record.appearance_features) < self.max_features_per_person:
                record.appearance_features.append(appearance_feature)

        record.last_seen_frame = frame_number
        record.total_detections += 1
        record.last_seen_time = datetime.now()
        if name:
            record.name = name
        if identity_data:
            record.identity_data.update(identity_data)

        return person_id

    def update_person(
        self,
        person_id: str,
        face_embedding: Optional[np.ndarray] = None,
        appearance_feature: Optional[np.ndarray] = None,
        frame_number: int = 0,
        name: Optional[str] = None,
        identity_data: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        更新现有人员的特征。

        参数：
            person_id: 要更新的人员
            face_embedding: 可用的新面部嵌入
            appearance_feature: 可用的新外观特征
            frame_number: 当前帧号

        返回：
            如果找到并更新了人员则返回True，否则返回False
        """

        if person_id not in self.persons:
            return False

        record = self.persons[person_id]

        if face_embedding is not None:
            if len(record.face_embeddings) < self.max_features_per_person:
                record.face_embeddings.append(face_embedding)

        if appearance_feature is not None:
            if len(record.appearance_features) < self.max_features_per_person:
                record.appearance_features.append(appearance_feature)

        record.last_seen_frame = frame_number
        record.total_detections += 1
        record.last_seen_time = datetime.now()
        if name:
            record.name = name
        if identity_data:
            record.identity_data.update(identity_data)

        return True

    def find_match(
        self,
        face_embedding: Optional[np.ndarray] = None,
        appearance_feature: Optional[np.ndarray] = None,
    ) -> Tuple[Optional[str], str]:
        """
        在库中查找最佳匹配的人员。

        参数：
            face_embedding: 可用的面部嵌入
            appearance_feature: 可用的外观特征

        返回：
            (person_id或None, match_type: "face" | "appearance" | "none")的元组
        """

        best_match_id: Optional[str] = None
        best_score = 0.0
        match_type = "none"

        # Priority 1: Try face matching
        if face_embedding is not None and len(face_embedding) > 0:
            for person_id, record in self.persons.items():
                if record.face_embeddings:
                    mean_face = self._mean_embedding(record.face_embeddings)
                    score = self._cosine_similarity(face_embedding, mean_face)
                    if score > best_score and score >= self.face_threshold:
                        best_match_id = person_id
                        best_score = score
                        match_type = "face"

        # Priority 2: Try appearance matching (if no face match found)
        if best_match_id is None and appearance_feature is not None:
            for person_id, record in self.persons.items():
                if record.appearance_features:
                    mean_app = self._mean_embedding(record.appearance_features)
                    score = self._cosine_similarity(appearance_feature, mean_app)
                    if score > best_score and score >= self.appearance_threshold:
                        best_match_id = person_id
                        best_score = score
                        match_type = "appearance"

        return best_match_id, match_type

    def get_person_count(self) -> int:
        """获取库中唯一人员的总数。"""
        return len(self.persons)

    def get_person(self, person_id: str) -> Optional[PersonRecord]:
        """获取特定人员记录。"""
        return self.persons.get(person_id)

    def rename_person(
        self,
        source_person_id: str,
        target_person_id: str,
        target_name: Optional[str] = None,
        target_identity_data: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Merge one record into another and keep the target identity."""
        if source_person_id == target_person_id:
            if target_name or target_identity_data:
                return self.update_person(
                    target_person_id,
                    name=target_name,
                    identity_data=target_identity_data,
                )
            return target_person_id in self.persons

        source = self.persons.get(source_person_id)
        if source is None:
            if target_person_id not in self.persons:
                self.persons[target_person_id] = PersonRecord(person_id=target_person_id)
            target = self.persons[target_person_id]
        else:
            target = self.persons.get(target_person_id)
            if target is None:
                target = PersonRecord(person_id=target_person_id)
                self.persons[target_person_id] = target

            target.face_embeddings.extend(source.face_embeddings[: self.max_features_per_person])
            target.appearance_features.extend(source.appearance_features[: self.max_features_per_person])
            if len(target.face_embeddings) > self.max_features_per_person:
                target.face_embeddings = target.face_embeddings[-self.max_features_per_person :]
            if len(target.appearance_features) > self.max_features_per_person:
                target.appearance_features = target.appearance_features[-self.max_features_per_person :]
            target.first_seen_frame = min(target.first_seen_frame or source.first_seen_frame, source.first_seen_frame)
            target.last_seen_frame = max(target.last_seen_frame, source.last_seen_frame)
            target.total_detections += source.total_detections
            target.last_seen_time = max(target.last_seen_time, source.last_seen_time)
            if source.name and not target.name:
                target.name = source.name
            if source.identity_data:
                target.identity_data.update(source.identity_data)
            self.persons.pop(source_person_id, None)

        if target_name:
            target.name = target_name
        if target_identity_data:
            target.identity_data.update(target_identity_data)
        return True

    def get_all_persons(self) -> Dict[str, PersonRecord]:
        """获取所有人员记录。"""
        return self.persons

    def get_stats(self) -> Dict:
        """获取库统计信息。"""
        total_embeddings = sum(
            len(p.face_embeddings) + len(p.appearance_features)
            for p in self.persons.values()
        )
        return {
            "total_persons": len(self.persons),
            "total_embeddings": total_embeddings,
            "next_person_id": self.next_person_id,
        }

    def clear(self):
        """清除库中的所有人员。"""
        self.persons.clear()
        self.next_person_id = 1

    def export_for_db(self) -> Dict[str, Dict]:
        """导出库数据用于数据库存储。"""
        return {
            pid: {
                "face_embeddings": [
                    e.tolist() if isinstance(e, np.ndarray) else list(e)
                    for e in record.face_embeddings
                ],
                "appearance_features": [
                    e.tolist() if isinstance(e, np.ndarray) else list(e)
                    for e in record.appearance_features
                ],
                "first_seen_frame": record.first_seen_frame,
                "last_seen_frame": record.last_seen_frame,
                "total_detections": record.total_detections,
                "name": record.name,
            }
            for pid, record in self.persons.items()
        }
