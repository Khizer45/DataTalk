from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    max_rows: int = Field(default=100, ge=1, le=500)
    # An uploaded dataset to ask about; its access token goes in the X-Dataset-Token header. None = demo data.
    dataset_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{16}$")


class ColumnInfo(BaseModel):
    name: str
    type: str


class TableInfo(BaseModel):
    name: str
    columns: list[ColumnInfo]


class QueryResponse(BaseModel):
    question: str
    sql: str
    rows: list[dict]
    row_count: int
    truncated: bool
    generator: str
    explanation: str

