"""Canonical string constants for status/state columns.

These mirror the exact string values already stored in the database, so adopting
them never changes behavior — it only removes the risk of a silent typo (e.g.
``"procesing"``) slipping through. Adopt gradually; raw literals elsewhere are
still valid until migrated.
"""
from __future__ import annotations


class JobStatus:
    """ConversionJob.status"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ProcessingStatus:
    """Model3D.processing_status"""
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    REPLACEMENT_FAILED = "replacement_failed"


class ReplacementStatus:
    """Model3D.replacement_status"""
    PROCESSING = "replacement_processing"
    READY = "ready"
    FAILED = "replacement_failed"


class VersionStatus:
    """ModelVersion.status"""
    QUEUED = "queued"
    READY = "ready"
    FAILED = "failed"


class PaperStatus:
    """Paper.status"""
    ACTIVE = "active"
    DELETED = "deleted"


class LicenseStatus:
    """Model3D.license_status"""
    ACTIVE = "active"


class PaymentStatus:
    """Payment.status"""
    PENDING = "pending"
    PAID = "paid"
