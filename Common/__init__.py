"""Common building blocks for the staged remote architecture refactor."""

from .logging_utils import make_component_logger
from .models import CaptureCapabilities, PipeRequest, PipeResponse, SessionDescriptor, TransportSettings
from .runtime_paths import (
    get_app_dir,
    get_default_service_pipe_name,
    get_program_data_dir,
    get_runtime_dir,
    get_runtime_log_file,
)

__all__ = [
    "CaptureCapabilities",
    "PipeRequest",
    "PipeResponse",
    "SessionDescriptor",
    "TransportSettings",
    "get_app_dir",
    "get_default_service_pipe_name",
    "get_program_data_dir",
    "get_runtime_dir",
    "get_runtime_log_file",
    "make_component_logger",
]
