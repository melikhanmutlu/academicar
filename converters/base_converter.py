"""Base model converter shared by format-specific converters."""

from datetime import datetime
import logging
import os
from typing import Dict, Optional


class BaseConverter:
    def __init__(self):
        self.model_id: str = None
        self.original_filename: str = None
        self.status: str = "INITIALIZED"
        self.errors: list = []
        self.start_time: Optional[datetime] = None
        self.end_time: Optional[datetime] = None
        self.logger = logging.getLogger(__name__)
        self.max_dimension: float = 0  # No scaling by default - only scale if user explicitly sets it

    def validate(self, file_path: str) -> bool:
        """
        Run file format and safety checks.
        Args:
            file_path: Path to the file that should be checked.
        Returns:
            bool: Whether the file is valid.
        """
        if not os.path.exists(file_path):
            self.handle_error(f"File not found: {file_path}")
            return False

        if not os.path.isfile(file_path):
            self.handle_error(f"Invalid file: {file_path}")
            return False

        return True

    def prepare(self, file_path: str) -> bool:
        """
        Prepare before conversion.
        Args:
            file_path: Path to the file that should be prepared.
        Returns:
            bool: Whether preparation succeeded.
        """
        self.start_time = datetime.now()
        self.original_filename = os.path.basename(file_path)
        self.status = "PREPARING"
        return True

    def convert(self, input_path: str, output_path: str) -> bool:
        """
        Convert the source file. Implemented by subclasses.
        Args:
            input_path: Path to the file that should be converted.
            output_path: Path where the converted output should be written.
        Returns:
            bool: Whether conversion succeeded.
        """
        raise NotImplementedError("This method must be implemented by subclasses")

    def cleanup(self) -> None:
        """Clean up temporary conversion state."""
        self.end_time = datetime.now()
        self.status = "COMPLETED"

    def log_operation(self, message: str, level: str = "INFO") -> None:
        """
        Log a converter operation.
        Args:
            message: Log message.
            level: Log level.
        """
        log_levels = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL,
        }
        self.logger.log(log_levels.get(level, logging.INFO), message)

    def update_status(self, status: str) -> None:
        """
        Update converter status.
        Args:
            status: New status value.
        """
        self.status = status
        self.log_operation(f"Status updated: {status}")

    def handle_error(self, error: str) -> None:
        """
        Handle a converter error.
        Args:
            error: Error message.
        """
        self.errors.append(error)
        self.status = "ERROR"
        self.log_operation(error, "ERROR")

    def optimize_output(self, output_path: str) -> bool:
        """
        Optimize converter output.
        Args:
            output_path: Path to the output that should be optimized.
        Returns:
            bool: Whether optimization succeeded.
        """
        return True

    def get_conversion_time(self) -> float:
        """
        Calculate conversion duration.
        Returns:
            float: Conversion duration in seconds.
        """
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return 0.0

    def get_status(self) -> Dict:
        """
        Return the current converter status.
        Returns:
            dict: Status details.
        """
        return {
            "model_id": self.model_id,
            "original_filename": self.original_filename,
            "status": self.status,
            "errors": self.errors,
            "conversion_time": self.get_conversion_time(),
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
        }

    def set_max_dimension(self, max_dimension_meters: float) -> None:
        """
        Set maximum dimension in meters.
        Args:
            max_dimension_meters: Maximum dimension in meters (already converted from cm).
        """
        self.max_dimension = max_dimension_meters
        self.log_operation(f"Maximum dimension set to {max_dimension_meters:.4f} m ({max_dimension_meters * 100:.2f} cm)")

    def calculate_scale_factor(self, dimensions: Dict[str, float]) -> float:
        """
        Calculate scale factor based on maximum dimension.
        ALWAYS scales to target dimension (both up and down) for AR standardization.
        Args:
            dimensions: Dictionary containing x, y, z dimensions in meters.
        Returns:
            float: Scale factor to apply to the model (always applied if max_dimension is set).
        """
        max_current_dimension = max(dimensions.values())

        if max_current_dimension <= 0:
            self.log_operation("Warning: Model has zero or negative dimensions", "WARNING")
            return 1.0

        if self.max_dimension <= 0:
            return 1.0

        scale_factor = self.max_dimension / max_current_dimension

        if scale_factor > 1.0:
            self.log_operation(f"Scaling UP: {scale_factor:.4f}x (Current max: {max_current_dimension:.4f}m -> Target: {self.max_dimension:.4f}m)")
        elif scale_factor < 1.0:
            self.log_operation(f"Scaling DOWN: {scale_factor:.4f}x (Current max: {max_current_dimension:.4f}m -> Target: {self.max_dimension:.4f}m)")
        else:
            self.log_operation(f"No scaling needed (already at target: {self.max_dimension:.4f}m)")

        return scale_factor
