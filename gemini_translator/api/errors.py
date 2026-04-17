from enum import Enum, auto

class ErrorType(Enum):
    GEOBLOCK = auto()
    QUOTA_EXCEEDED = auto()
    TEMPORARY_LIMIT = auto()
    MODEL_NOT_FOUND = auto()
    NETWORK = auto()
    VALIDATION = auto()
    API_ERROR = auto()
    CANCEL = auto()
    CONTENT_FILTER = auto() 
    PARTIAL_GENERATION = auto()

class WorkerAction(Enum):
    RETRY_NON_COUNTABLE = auto()
    RETRY_COUNTABLE = auto()
    MODEL_NOT_FOUND = auto()
    FAIL_AND_ATTEMPT_CHUNK = auto()
    FAIL_PERMANENTLY = auto()
    ABORT_WORKER = auto()
    
class SuccessSignal(Exception):
    def __init__(self, message="Задача успешно завершена досрочно.", status_code="SUCCESS_SIGNAL"):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)

class ContentFilterError(Exception): pass
class OperationCancelledError(Exception): pass
class LocationBlockedError(Exception): pass
class ModelNotFoundError(Exception): pass
class ValidationFailedError(Exception): pass

class RateLimitExceededError(Exception):
    def __init__(self, message):
        super().__init__(message)

class TemporaryRateLimitError(Exception):
    def __init__(self, message, delay_seconds=60):
        super().__init__(message)
        self.delay_seconds = delay_seconds

class NetworkError(Exception):
    def __init__(self, message, delay_seconds=30):
        super().__init__(message)
        self.delay_seconds = delay_seconds
        
class GracefulShutdownInterrupt(Exception):
    pass

class PartialGenerationError(Exception):
    def __init__(self, message, partial_text, reason):
        super().__init__(message)
        self.partial_text = partial_text
        self.reason = reason