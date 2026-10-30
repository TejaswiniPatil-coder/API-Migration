"""
Migration-related data models.

Represent migration mappings (loaded from JSON), argument
transformations, and per-call migration plans.
"""

from dataclasses import dataclass, field
from typing import Optional, Any


@dataclass
class ArgumentTransformation:
    """Describes how one argument must change during migration."""

    old_name: str
    new_name: Optional[str] = None       # None means argument is removed
    old_position: Optional[int] = None
    new_position: Optional[int] = None
    type_conversion: Optional[str] = None  # e.g. "str_to_int"
    default_value: Optional[str] = None


@dataclass
class MigrationMapping:
    """A single migration rule loaded from api_mappings.json."""

    migration_id: str
    library: str
    old_version: str
    new_version: str
    old_api: str                          # e.g. "HTTPClient.old_connect"
    new_api: str                          # e.g. "HTTPClient.connect"
    old_signature: list[str] = field(default_factory=list)
    new_signature: list[str] = field(default_factory=list)
    argument_transformations: list[dict[str, Any]] = field(default_factory=list)
    description: str = ""
    transformation_type: str = "rename"   # rename | arg_modify | arg_reorder | method_to_property


@dataclass
class MigrationPlan:
    """A concrete plan to migrate one detected API call."""

    migration_id: str
    line: int
    old_api: str
    new_api: str
    transformation_type: str
    argument_changes: list[ArgumentTransformation] = field(default_factory=list)
    validation_notes: list[str] = field(default_factory=list)
    confidence: float = 1.0               # 1.0 = deterministic, <1.0 = AI-assisted


@dataclass
class MigrationResult:
    """Outcome of applying a migration plan."""

    plan: MigrationPlan
    original_code: str
    migrated_code: str
    diff: str = ""
    success: bool = True
    errors: list[str] = field(default_factory=list)
    ai_explanation: Optional[str] = None
