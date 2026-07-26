"""Single domain error type, translated by each transport layer.

The service layer raises `ForPostError(status_code, detail)` for any expected
domain failure (mode violation, not found, workspace escape...). The transports
translate it once, centrally:
- REST: a FastAPI exception handler -> JSON response with `status_code`.
- MCP: a thin wrapper -> ValueError(detail) (FastMCP surfaces it as a ToolError).
"""


class ForPostError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
