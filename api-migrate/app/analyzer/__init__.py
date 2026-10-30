"""
Analyzer package.

Provides API detection, symbol tracking, and lightweight
semantic analysis over parsed ASTs.
"""

from app.analyzer.api_detector import APIDetector, detect_deprecated_apis
from app.analyzer.symbol_analyzer import SymbolTable, SymbolInfo, SymbolKind, build_symbol_table
from app.analyzer.semantic_analyzer import (
    SemanticAnalyzer,
    SemanticReport,
    SemanticIssue,
    IssueSeverity,
    IssueKind,
    load_target_specs,
)

__all__ = [
    "APIDetector",
    "detect_deprecated_apis",
    "SymbolTable",
    "SymbolInfo",
    "SymbolKind",
    "build_symbol_table",
    "SemanticAnalyzer",
    "SemanticReport",
    "SemanticIssue",
    "IssueSeverity",
    "IssueKind",
    "load_target_specs",
]
