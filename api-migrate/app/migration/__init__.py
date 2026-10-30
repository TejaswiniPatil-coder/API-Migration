"""
Migration package.

Provides API mapping, migration planning, AST transformation,
and code generation.
"""

from app.migration.api_mapper import MappedAPI, match_detections_to_mappings
from app.migration.migration_planner import (
    MigrationPlanner,
    create_migration_plans,
)
from app.migration.ast_transformer import (
    MigrationTransformer,
    apply_migrations,
    TransformResult,
)
from app.migration.code_generator import (
    generate_code,
    generate_diff,
    unparse_tree,
    GeneratedCode,
)

__all__ = [
    "MappedAPI",
    "match_detections_to_mappings",
    "MigrationPlanner",
    "create_migration_plans",
    "MigrationTransformer",
    "apply_migrations",
    "TransformResult",
    "generate_code",
    "generate_diff",
    "unparse_tree",
    "GeneratedCode",
]
