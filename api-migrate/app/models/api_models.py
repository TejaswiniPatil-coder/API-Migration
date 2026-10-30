"""
API-related data models.

These dataclasses represent API signatures, parameters,
and detected API usages within source code.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ParameterInfo:
    """Describes a single parameter in an API signature."""

    name: str
    type_hint: Optional[str] = None
    default: Optional[str] = None       # string repr of default value
    required: bool = True


@dataclass
class APISignature:
    """Describes the full signature of an API (function / method)."""

    full_name: str                        # e.g. "HTTPClient.connect"
    parameters: list[ParameterInfo] = field(default_factory=list)
    return_type: Optional[str] = None


@dataclass
class DetectedAPI:
    """An API usage detected in source code via AST analysis."""

    api_name: str                         # e.g. "HTTPClient.old_connect"
    line: int
    col: int = 0
    arguments: list[str] = field(default_factory=list)
    keyword_arguments: dict[str, str] = field(default_factory=dict)
    node_type: str = "Call"               # AST node type
    receiver_variable: Optional[str] = None   # e.g. "client"
    inferred_class: Optional[str] = None      # e.g. "HTTPClient"
