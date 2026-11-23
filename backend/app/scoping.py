from typing import Generic, TypeVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

M = TypeVar("M")


class OrgRepo(Generic[M]):
    """The only way endpoints touch a tenant table. org_id comes from the session
    (never the client) and is applied to every query and every insert."""

    def __init__(self, db: Session, model: type[M], org_id: UUID):
        self.db, self.model, self.org_id = db, model, org_id

    def query(self):
        return select(self.model).where(self.model.org_id == self.org_id)

    def list(self, *where):
        return list(self.db.scalars(self.query().where(*where)))

    def get(self, id: UUID) -> M | None:
        return self.db.scalar(self.query().where(self.model.id == id))

    def first(self, *where) -> M | None:
        return self.db.scalar(self.query().where(*where))

    def add(self, **fields) -> M:
        obj = self.model(org_id=self.org_id, **fields)
        self.db.add(obj)
        self.db.flush()
        return obj

    def delete(self, obj: M) -> None:
        assert obj.org_id == self.org_id
        self.db.delete(obj)
