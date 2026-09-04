import enum


class MatchStatus(str, enum.Enum):
    PENDING = "pending"
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    ANALYZED = "analyzed"
    FAILED = "failed"


class VideoStatus(str, enum.Enum):
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    METADATA_EXTRACTED = "metadata_extracted"
    PROCESSING = "processing"
    ANALYZED = "analyzed"
    FAILED = "failed"


class PlayerPosition(str, enum.Enum):
    GOALKEEPER = "goalkeeper"
    DEFENDER = "defender"
    MIDFIELDER = "midfielder"
    FORWARD = "forward"
    UNKNOWN = "unknown"
