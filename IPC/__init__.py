"""Named-pipe based IPC for the staged service/agent split."""

from .named_pipe import NamedPipeCommandClient, NamedPipeCommandServer, is_named_pipe_available

__all__ = [
    "NamedPipeCommandClient",
    "NamedPipeCommandServer",
    "is_named_pipe_available",
]
