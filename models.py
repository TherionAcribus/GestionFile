from extensions import db
from datetime import datetime, timezone
from sqlalchemy.schema import Sequence

class ConfigVersion(db.Model):
    id = db.Column(db.Integer, Sequence('config_version_id_seq'), primary_key=True)
    key = db.Column(db.String(50), unique=True, nullable=False)
    version = db.Column(db.String(50), nullable=False)
    comments = db.Column(db.Text)
    date = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f'<ConfigVersion {self.version}>'