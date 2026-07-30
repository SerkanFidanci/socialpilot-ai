"""Complete SQLAlchemy metadata for every process that reaches the database.

Declarative metadata only contains the tables whose model modules were imported. A
process that imports a subset cannot resolve a cross-module foreign key: a Celery
worker entry point pulls in the media and operations models, but `jobs.business_id`
references `businesses.id`, so the mapping stays broken until the businesses models
are imported too. Importing this module registers every table regardless of entry
point, and `verify_mapping_is_complete` turns a missing import into a startup error
instead of a failure on the first task that loads a row.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import configure_mappers

from app.modules.brands import models as brand_models
from app.modules.businesses import models as business_models
from app.modules.identity import models as identity_models
from app.modules.media import models as media_models
from app.modules.operations import models as operations_models

MODEL_MODULES = (
    brand_models,
    business_models,
    identity_models,
    media_models,
    operations_models,
)

metadata: MetaData = identity_models.Base.metadata


def verify_mapping_is_complete() -> int:
    """Configure every mapper and resolve every foreign key; return the count resolved.

    Raises `sqlalchemy.exc.NoReferencedTableError` when a referenced table is absent
    from the shared metadata, which means a model module was never imported.
    """

    configure_mappers()
    resolved = [key.column for table in metadata.tables.values() for key in table.foreign_keys]
    return len(resolved)
