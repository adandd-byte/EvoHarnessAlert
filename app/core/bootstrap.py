from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import Base, engine
from app.core.security import hash_password
from app.models.entities import AlertSource, UserAccount
from app.services.knowledge import KnowledgeService


def create_schema() -> None:
    Base.metadata.create_all(bind=engine)


def seed_data(db: Session) -> None:
    if db.query(UserAccount).count() == 0:
        admin = UserAccount(username="admin", display_name="告警管理员", password_hash=hash_password("admin123"))
        admin.roles = {"ROLE_ADMIN"}
        viewer = UserAccount(username="viewer", display_name="值班观察员", password_hash=hash_password("viewer123"))
        viewer.roles = {"ROLE_ADMIN"}
        db.add_all([admin, viewer])
        db.commit()
    for name, kind, desc in [("webhook", "webhook", "通用 Webhook 告警"), ("prometheus", "prometheus", "Prometheus Alertmanager 告警")]:
        if db.query(AlertSource).filter(AlertSource.name == name).first() is None:
            db.add(AlertSource(name=name, kind=kind, description=desc))
    db.commit()
    service = KnowledgeService(db, get_settings())
    root = Path(__file__).resolve().parents[1]
    for file in sorted((root / "knowledge").glob("*.md")):
        service.ensure_source(file.name, file.read_text(encoding="utf-8"))
