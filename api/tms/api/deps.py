from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from tms.db import get_sessionmaker


def get_db():
    with get_sessionmaker()() as session:
        yield session


DB = Annotated[Session, Depends(get_db)]
