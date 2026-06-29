from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.SQL_ECHO,
)

async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


def _column_type_sql(dialect_name: str, column_type: str) -> str:
    if column_type == "json":
        return "JSONB" if dialect_name == "postgresql" else "JSON"
    if column_type == "datetime":
        return "TIMESTAMP" if dialect_name == "postgresql" else "DATETIME"
    if column_type == "blob":
        return "BYTEA" if dialect_name == "postgresql" else "BLOB"
    if column_type == "bool":
        return "BOOLEAN"
    if column_type == "int":
        return "INTEGER"
    if column_type == "float":
        return "DOUBLE PRECISION" if dialect_name == "postgresql" else "REAL"
    return "VARCHAR"


def _ensure_schema(sync_conn) -> None:
    inspector = inspect(sync_conn)
    dialect_name = sync_conn.dialect.name

    table_definitions = {
        "visitor_registrations": {
            "id": "str",
            "start_time": "datetime",
            "end_time": "datetime",
            "visiting_company": "str",
            "total_people": "int",
            "created_at": "datetime",
            "updated_at": "datetime",
        },
        "external_personnel_registrations": {
            "id": "str",
            "external_person_id": "str",
            "name": "str",
            "organization": "str",
            "start_time": "datetime",
            "end_time": "datetime",
            "visit_reason": "str",
            "face_image": "blob",
            "face_embedding": "blob",
            "face_image_storage": "str",
            "face_image_bucket": "str",
            "face_image_object_key": "str",
            "face_image_content_type": "str",
            "face_image_size_bytes": "int",
            "supervision_events": "str",
            "allowed_camera_ids": "str",
            "created_at": "datetime",
            "updated_at": "datetime",
        },
        "external_persons": {
            "id": "str",
            "name": "str",
            "organization": "str",
            "supervision_scope": "str",
            "allowed_camera_ids": "str",
            "face_embedding": "blob",
            "thumbnail": "blob",
            "face_image_storage": "str",
            "face_image_bucket": "str",
            "face_image_object_key": "str",
            "face_image_content_type": "str",
            "face_image_size_bytes": "int",
            "created_at": "datetime",
            "updated_at": "datetime",
        },
        "job_title_options": {
            "id": "str",
            "code": "str",
            "name": "str",
            "sort_order": "int",
            "is_active": "bool",
            "created_at": "datetime",
            "updated_at": "datetime",
        },
    }

    table_columns = {
        "persons": {
            "is_employee": "bool",
            "workshop": "str",
            "job_title": "str",
            "supervision_scope": "str",
            "face_image_storage": "str",
            "face_image_bucket": "str",
            "face_image_object_key": "str",
            "face_image_content_type": "str",
            "face_image_size_bytes": "int",
        },
        "shift_schedules": {
            "day_person_ids": "str",
            "night_person_ids": "str",
        },
        "video_sources": {
            "floor": "str",
            "name_suffix": "str",
            "camera_detection_scope": "str",
            "backend_detection_scope": "str",
            "area_overcapacity_polygon": "str",
            "area_overcapacity_limit": "int",
            "is_patrol_area": "bool",
            "last_patrol_at": "datetime",
            "last_patrol_person_id": "str",
            "last_patrol_person_name": "str",
            "last_patrol_evaluated_window_end": "datetime",
        },
        "compliance_events": {
            "person_name": "str",
            "action_violations": "json",
            "danger_event_types": "json",
            "camera_ids": "json",
            "camera_name": "str",
            "snapshot_storage": "str",
            "snapshot_bucket": "str",
            "snapshot_object_key": "str",
            "snapshot_content_type": "str",
            "snapshot_size_bytes": "int",
            "video_path": "str",
            "video_storage": "str",
            "video_bucket": "str",
            "video_object_key": "str",
            "video_content_type": "str",
            "video_size_bytes": "int",
            "start_frame": "int",
            "end_frame": "int",
            "end_timestamp": "datetime",
            "duration_frames": "int",
            "is_ongoing": "bool",
        },
        "external_personnel_registrations": {
            "external_person_id": "str",
            "face_embedding": "blob",
            "face_image_storage": "str",
            "face_image_bucket": "str",
            "face_image_object_key": "str",
            "face_image_content_type": "str",
            "face_image_size_bytes": "int",
            "supervision_events": "str",
            "allowed_camera_ids": "str",
        },
        "external_persons": {
            "supervision_scope": "str",
            "allowed_camera_ids": "str",
            "face_embedding": "blob",
            "thumbnail": "blob",
            "face_image_storage": "str",
            "face_image_bucket": "str",
            "face_image_object_key": "str",
            "face_image_content_type": "str",
            "face_image_size_bytes": "int",
            "created_at": "datetime",
            "updated_at": "datetime",
        },
        "supervision_settings": {
            "other_person_scope": "str",
            "area_missed_inspection_enabled": "int",
            "area_missed_inspection_interval_hours": "float",
            "area_missed_inspection_start_time": "str",
            "area_missed_inspection_camera_ids": "str",
            "blind_spot_stay_enabled": "int",
            "blind_spot_stay_threshold_seconds": "int",
            "workshop_overcapacity_enabled": "int",
            "workshop_overcapacity_limit": "int",
            "alert_cooldown_seconds": "int",
        },
    }

    existing_tables = set(inspector.get_table_names())
    for table_name, columns in table_definitions.items():
        if table_name in existing_tables:
            continue
        column_sql: list[str] = []
        for column_name, column_type in columns.items():
            sql_type = _column_type_sql(dialect_name, column_type)
            not_null_fields = {
                "id",
                "start_time",
                "end_time",
                "visiting_company",
                "total_people",
                "name",
                "organization",
                "visit_reason",
            }
            nullable_sql = "NOT NULL" if column_name in not_null_fields else ""
            column_sql.append(f"{column_name} {sql_type} {nullable_sql}".strip())
        sync_conn.execute(text(f"CREATE TABLE {table_name} ({', '.join(column_sql)}, PRIMARY KEY (id))"))

    for table_name, columns in table_columns.items():
        if table_name not in existing_tables:
            continue
        existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
        for column_name, column_type in columns.items():
            if column_name in existing_columns:
                continue
            sql_type = _column_type_sql(dialect_name, column_type)
            sync_conn.execute(
                text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {sql_type}")
            )

    if "supervision_settings" in existing_tables and dialect_name == "postgresql":
        supervision_columns = {column["name"]: column for column in inspector.get_columns("supervision_settings")}
        interval_column = supervision_columns.get("area_missed_inspection_interval_hours")
        if interval_column is not None:
            type_name = str(interval_column["type"]).lower()
            if "double precision" not in type_name and "real" not in type_name and "float" not in type_name:
                sync_conn.execute(
                    text(
                        "ALTER TABLE supervision_settings "
                        "ALTER COLUMN area_missed_inspection_interval_hours "
                        "TYPE DOUBLE PRECISION "
                        "USING area_missed_inspection_interval_hours::double precision"
                    )
                )

    if "job_title_options" in existing_tables:
        existing_rows = sync_conn.execute(
            text("SELECT code FROM job_title_options")
        ).fetchall()
        existing_codes = {row[0] for row in existing_rows}
        defaults = [
            ("management", "管理人员", 10),
            ("inspector", "巡检人员", 20),
            ("hazardous_operator", "危险作业人员", 30),
            ("regular_operator", "常规作业人员", 40),
        ]
        for code, name, sort_order in defaults:
            if code in existing_codes:
                continue
            sync_conn.execute(
                text(
                    "INSERT INTO job_title_options (id, code, name, sort_order, is_active) "
                    "VALUES (:id, :code, :name, :sort_order, :is_active)"
                ),
                {
                    "id": f"job-title-{code}",
                    "code": code,
                    "name": name,
                    "sort_order": sort_order,
                    "is_active": True,
                },
            )


async def get_db():
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    """Initialize database tables using Alembic migrations or create_all."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_schema)
