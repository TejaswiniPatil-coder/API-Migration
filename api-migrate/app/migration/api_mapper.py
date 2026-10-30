"""
API Mapper — matches detected deprecated APIs to migration mappings.

Given a list of DetectedAPI instances (from the detector) and the
migration knowledge base, produces matched pairs that the planner
can consume.

This is a thin lookup layer — the matching logic itself lives in
the detector.  The mapper enriches detections with their corresponding
MigrationMapping objects for downstream use.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.models.api_models import DetectedAPI
from app.models.migration_models import MigrationMapping


@dataclass
class MappedAPI:
    """A detected deprecated API call paired with its migration mapping."""

    detection: DetectedAPI
    mapping: MigrationMapping


def match_detections_to_mappings(
    detections: list[DetectedAPI],
    mappings: list[MigrationMapping],
) -> list[MappedAPI]:
    """Match each detection to its corresponding migration mapping.

    Parameters
    ----------
    detections : list[DetectedAPI]
        Detected deprecated API calls.
    mappings : list[MigrationMapping]
        Available migration rules.

    Returns
    -------
    list[MappedAPI]
        Matched pairs, one per detection.  Unmatched detections
        are silently skipped (should not happen if the detector
        used the same mappings).
    """
    mapping_by_old_api: dict[str, MigrationMapping] = {
        m.old_api: m for m in mappings
    }

    results: list[MappedAPI] = []
    for det in detections:
        mapping = mapping_by_old_api.get(det.api_name)
        if mapping is not None:
            results.append(MappedAPI(detection=det, mapping=mapping))

    return results
