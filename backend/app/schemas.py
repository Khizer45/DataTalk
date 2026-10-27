from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    max_rows: int = Field(default=100, ge=1, le=500)


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

