class ObserverError(Exception):
    """Error safe to normalize at the MCP boundary."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message
