class ServiceError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class AdjudicationError(ServiceError):
    """页态与库态漂移，引擎已按裁决处理；携带裁决编号供页面与回看侧对照。"""

    def __init__(self, message: str, status_code: int = 409, adjudication: dict | None = None, db_status: str | None = None):
        super().__init__(message, status_code)
        self.adjudication = adjudication
        self.db_status = db_status
