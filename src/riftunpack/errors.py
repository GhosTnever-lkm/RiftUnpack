"""User-facing errors raised by RiftUnpack."""


class RiftUnpackError(Exception):
    """Base class for expected, displayable application errors."""


class UnsupportedFormatError(RiftUnpackError):
    """The archive format is unknown or no suitable backend is installed."""


class UnsafeArchiveError(RiftUnpackError):
    """The archive contains unsafe paths, links, or special filesystem objects."""


class ArchiveLimitError(RiftUnpackError):
    """Configured file-count, size, or compression-ratio limit was exceeded."""


class PasswordRequiredError(RiftUnpackError):
    """The archive needs a password or the supplied password is incorrect."""


class CancelledError(RiftUnpackError):
    """The user cancelled the current extraction."""
