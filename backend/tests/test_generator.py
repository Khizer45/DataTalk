"""The AI model's replies, without a database: strict parsing, clarifications, assumptions and the one retry.

The stand-in model from conftest replaces the HTTP call to the model; parsing, the retry decisions and SQL validation
are the real code. Runner stands in for read-only execution (the PostgreSQL tests run the real thing).
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import DBAPIError

from app import generator
from app.generator import LLMError, ReplyFormatError, answer_question, parse_reply
from app.limits import DailyQuota
from app.rules import UnsupportedQuestionError
from app.safety import UnsafeQueryError

TABLES = ["customers", "products", "orders", "order_items"]
SCHEMA = "orders(id, customer_id, order_date, status, total_amount)\ncustomers(id, name, country)"
TOP_CUSTOMERS = {"clarification": {
    "question": "Should customers be ranked by revenue or by number of orders?",
    "options": ["Who are the top 5 customers by revenue?", "Who are the top 5 customers by number of orders?"]}}


def reply(sql="SELECT count(*) AS n FROM orders", explanation="Counts orders.", assumptions=()):
    return {"sql": sql, "explanation": explanation, "assumptions": list(assumptions)}


class Runner:
    """Stands in for read-only execution: keeps the SQL it is given and returns one row, or raises the errors queued."""

    def __init__(self, *errors):
        self.sql, self.errors = [], list(errors)

    def __call__(self, sql):
        self.sql.append(sql)
        if self.errors:
            raise self.errors.pop(0)
        return [{"n": 7}]


def ask(question, run=None, **options):
    return answer_question(question, SCHEMA, TABLES, 101, run or Runner(), **options)


def database_error(sqlstate, message=None, hint=None):
    """A database error as SQLAlchemy raises it, with PostgreSQL's SQLSTATE, message and hint."""
    orig = SimpleNamespace(sqlstate=sqlstate, diag=SimpleNamespace(message_primary=message, message_hint=hint))
    return DBAPIError("SELECT ...", {}, orig)


def test_an_answer_is_read_with_its_assumptions():
    text = json.dumps(reply(" SELECT 1 ", assumptions=['"Last month" means September 2026.']))
    assert parse_reply(text) == {"sql": "SELECT 1", "explanation": "Counts orders.",
                                 "assumptions": ['"Last month" means September 2026.']}


def test_a_clarification_is_read():
    assert parse_reply(json.dumps(TOP_CUSTOMERS)) == TOP_CUSTOMERS


def test_one_surrounding_code_fence_is_accepted():
    assert parse_reply("```json\n" + json.dumps(reply()) + "\n```")["sql"] == "SELECT count(*) AS n FROM orders"


@pytest.mark.parametrize("content, problem", [
    (None, "empty"),
    ("  ", "empty"),
    ("SELECT count(*) FROM orders", "not one JSON object"),
    ("```sql\nSELECT count(*) FROM orders\n```", "not one JSON object"),
    ("Here is the query: " + json.dumps(reply()), "not one JSON object"),
    (json.dumps(reply()) + "\nHope this helps!", "not one JSON object"),
    (json.dumps([reply()]), "not one JSON object"),
    ('{"sql": "SELECT 1", "sql": "DELETE FROM orders", "explanation": "x", "assumptions": []}',
     'key "sql" appears more than once'),
    (json.dumps({**reply(), **TOP_CUSTOMERS}), "exactly the keys"),
    (json.dumps({"sql": "SELECT 1", "explanation": "One."}), "exactly the keys"),
    (json.dumps({**reply(), "confidence": 0.9}), "exactly the keys"),
    (json.dumps({"query": "SELECT 1"}), "exactly the keys"),
    (json.dumps(reply(sql=["SELECT 1"])), '"sql" must be text'),
    (json.dumps(reply(sql="  ")), '"sql" must be text'),
    (json.dumps(reply(sql="SELECT " + "1," * 4000 + "1")), '"sql" must be text of 1 to 8000'),
    (json.dumps(reply(explanation="")), '"explanation" must be text'),
    (json.dumps({**reply(), "assumptions": "none"}), '"assumptions" must be a list'),
    (json.dumps(reply(assumptions=[1])), "Each assumption must be text"),
    (json.dumps(reply(assumptions=["An assumption."] * 6)), "at most 5"),
    (json.dumps({"clarification": "By revenue or by orders?"}), '"question" and "options"'),
    (json.dumps({"clarification": {"options": TOP_CUSTOMERS["clarification"]["options"]}}), '"question" and "options"'),
    (json.dumps({"clarification": {"question": "By what?", "options": ["Top 5 by revenue?"]}}), "2 to 4"),
    (json.dumps({"clarification": {"question": "By what?", "options": [f"Option {i}?" for i in range(5)]}}), "2 to 4"),
    (json.dumps({"clarification": {"question": "By what?", "options": ["By revenue?", "by REVENUE?"]}}),
     "all be different"),
    (json.dumps({"clarification": {"question": "By what?", "options": ["ok", "Top 5 by revenue?"]}}),
     "Each option must be text of 3"),
    (json.dumps(reply(sql="SELECT " + "1, " * 5000 + "1")), "longer than 12000"),
])
def test_anything_else_is_rejected(content, problem):
    with pytest.raises(ReplyFormatError, match=problem):
        parse_reply(content)


def test_an_answer_runs_validated_sql_and_keeps_its_assumptions(model):
    model["What was revenue last month?"] = reply(
        "SELECT SUM(total_amount) AS revenue FROM orders WHERE status = 'completed'",
        "Revenue from completed orders last month.",
        ['"Last month" means September 2026.', "Revenue counts completed orders only."])
    run = Runner()
    answer = ask("What was revenue last month?", run)
    assert answer.generator == "llm" and answer.rows == [{"n": 7}] and answer.clarification is None
    assert answer.assumptions == ['"Last month" means September 2026.', "Revenue counts completed orders only."]
    assert answer.explanation == "Revenue from completed orders last month."
    assert run.sql == [answer.sql] and answer.sql.endswith("LIMIT 101")  # validated and capped before it ran
    assert len(model.calls) == 1


def test_the_prompt_describes_both_forms_and_gives_the_schema_date_and_question(model):
    model["How many orders?"] = "SELECT count(*) FROM orders"
    ask("How many orders?")
    system, user = model.calls[0]
    assert system["role"] == "system" and '{"sql":' in system["content"] and '{"clarification":' in system["content"]
    assert user["role"] == "user" and user["content"].startswith(f"Schema:\n{SCHEMA}\n\nToday's date: 20")
    assert user["content"].endswith("\n\nQuestion: How many orders?")


def test_a_clarification_runs_nothing(model):
    model["Who are our top customers?"] = TOP_CUSTOMERS
    run = Runner()
    answer = ask("Who are our top customers?", run)
    assert answer.clarification == TOP_CUSTOMERS["clarification"]
    assert answer.sql is None and answer.rows == [] and run.sql == []


def test_a_malformed_reply_gets_one_retry(model):
    model["How many orders?"] = [{"query": "SELECT count(*) FROM orders"}, "SELECT count(*) AS n FROM orders"]
    attempts = []
    assert ask("How many orders?", attempts=attempts).rows == [{"n": 7}]
    first, retry = model.calls
    assert retry[:2] == first  # the same instructions and question, then the reply and what was wrong with it
    assert retry[2] == {"role": "assistant", "content": '{"query": "SELECT count(*) FROM orders"}'}
    assert retry[3]["role"] == "user"
    assert 'exactly the keys "sql", "explanation" and "assumptions"' in retry[3]["content"]
    assert attempts[0]["sql"] is None and "exactly the keys" in attempts[0]["problem"]
    assert attempts[1] == {"sql": "SELECT count(*) AS n FROM orders", "problem": None}


def test_prose_around_the_json_is_retried(model, monkeypatch):
    replies = ["Sure! Here is the query:\nSELECT count(*) FROM orders", json.dumps(reply())]
    monkeypatch.setattr(generator, "_chat", lambda messages: replies.pop(0))
    assert ask("How many orders?").rows == [{"n": 7}] and replies == []


def test_a_reply_that_stays_malformed_is_a_clear_error(model):
    model["How many orders?"] = [{"query": "SELECT 1"}, {"sql": "SELECT 1"}]
    run = Runner()
    with pytest.raises(LLMError, match="not in the expected format, so no query was run") as error:
        ask("How many orders?", run)
    assert isinstance(error.value.__cause__, ReplyFormatError) and len(model.calls) == 2 and run.sql == []


def test_sql_the_validator_rejects_as_fixable_is_retried_with_the_reason(model):
    model["How many staff?"] = ["SELECT count(*) FROM staff", "SELECT count(*) AS n FROM customers"]
    assert ask("How many staff?").rows == [{"n": 7}]
    assert "outside the exposed schema: staff." in model.calls[1][3]["content"]


@pytest.mark.parametrize("sql", ["DELETE FROM orders WHERE status = 'cancelled'",
                                 "UPDATE orders SET status = 'cancelled'",
                                 "SELECT * INTO orders_backup FROM orders"])
def test_a_write_attempt_is_never_retried(model, sql):
    model["Delete all cancelled orders."] = [sql, "SELECT count(*) FROM orders WHERE status = 'cancelled'"]
    run = Runner()
    with pytest.raises(UnsafeQueryError) as error:
        ask("Delete all cancelled orders.", run)
    assert error.value.retryable is False and len(model.calls) == 1 and run.sql == []


def test_an_unknown_column_is_retried_with_postgresqls_message(model):
    model["What is total revenue?"] = ["SELECT SUM(total_amont) FROM orders",
                                       "SELECT SUM(total_amount) AS n FROM orders"]
    run = Runner(database_error("42703", 'column "total_amont" does not exist',
                                'Perhaps you meant to reference the column "orders.total_amount".'))
    assert ask("What is total revenue?", run).rows == [{"n": 7}] and len(run.sql) == 2
    assert ('PostgreSQL error: column "total_amont" does not exist. Hint: Perhaps you meant to reference the column '
            '"orders.total_amount".') in model.calls[1][3]["content"]


def test_a_missing_from_entry_is_retried(model):
    model["Revenue by country?"] = ["SELECT c.country, SUM(o.total_amount) FROM orders o GROUP BY c.country",
                                    "SELECT country FROM customers"]
    run = Runner(database_error("42P01"))  # no message from the driver: a plain description is sent instead
    assert ask("Revenue by country?", run).rows == [{"n": 7}]
    assert "PostgreSQL error: a table in the query does not exist or is missing from FROM." in model.calls[1][3]["content"]


@pytest.mark.parametrize("sqlstate", ["57014", "42883", "22012", "42501"])
def test_other_database_errors_are_not_retried(model, sqlstate):
    model["What is total revenue?"] = ["SELECT SUM(status) FROM orders", "SELECT 1"]
    with pytest.raises(DBAPIError):
        ask("What is total revenue?", Runner(database_error(sqlstate, "something failed")))
    assert len(model.calls) == 1


def test_there_is_at_most_one_retry(model):
    model["How many staff?"] = ["SELECT * FROM staff", "SELECT * FROM staff_notes"]
    with pytest.raises(UnsafeQueryError, match="staff_notes"):
        ask("How many staff?")
    model["What is total revenue?"] = ["SELECT SUM(total) FROM orders", "SELECT SUM(totl) FROM orders"]
    unknown = database_error("42703", 'column "total" does not exist')
    with pytest.raises(DBAPIError):
        ask("What is total revenue?", Runner(unknown, unknown))
    assert len(model.calls) == 4  # two questions, two calls each


def test_retries_count_against_the_daily_limit(model, monkeypatch):
    monkeypatch.setattr(generator, "_llm_quota", DailyQuota(1))
    model["How many orders?"] = [{"query": "SELECT 1"}, "SELECT count(*) AS n FROM orders"]
    with pytest.raises(LLMError, match="not in the expected format"):
        ask("How many orders?")  # the first call used today's only model call, so there is no retry
    assert len(model.calls) == 1

    monkeypatch.setattr(generator, "_llm_quota", DailyQuota(2))
    assert ask("How many orders?").rows == [{"n": 7}] and len(model.calls) == 3
    assert not generator._llm_quota.try_consume()  # the first call and the retry used both


def test_daily_ai_limit_falls_back_to_demo_mode(model, monkeypatch):
    monkeypatch.setattr(generator, "_llm_quota", DailyQuota(1))
    model["Which channel grew fastest?"] = "SELECT 1"
    assert ask("Which channel grew fastest?").generator == "llm"
    assert ask("How many orders are pending?").generator == "rules"
    with pytest.raises(UnsupportedQuestionError, match="today's limit"):
        ask("Which channel grew fastest?")
    with pytest.raises(UnsupportedQuestionError, match="paused until tomorrow"):
        ask("Which channel grew fastest?", allow_rules=False)
    assert len(model.calls) == 1


def test_demo_mode_is_unchanged_without_a_key():
    answer = ask("How many orders are pending?")
    assert answer.generator == "rules" and answer.assumptions == [] and answer.clarification is None
    assert "status = 'pending'" in answer.sql and answer.rows == [{"n": 7}]
    with pytest.raises(UnsupportedQuestionError, match="AI mode"):
        ask("How many orders are pending?", allow_rules=False)


def test_the_model_is_called_over_the_openai_compatible_api(monkeypatch):
    """The real HTTP call, against a local stand-in server."""
    requests = []

    class StandInServer(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append((self.path, self.headers["Authorization"],
                             json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            body = json.dumps({"choices": [{"message": {"role": "assistant", "content": json.dumps(reply())}}]})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), StandInServer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(generator.settings, "llm_base_url", f"http://127.0.0.1:{server.server_port}/v1")
    monkeypatch.setattr(generator.settings, "llm_api_key", "test-key")
    monkeypatch.setattr(generator.settings, "llm_model", "test-model")
    monkeypatch.setattr(generator, "_llm_quota", DailyQuota(10))
    try:
        assert ask("How many orders?").rows == [{"n": 7}]
    finally:
        server.shutdown()
        server.server_close()
    path, authorization, body = requests[0]
    assert path == "/v1/chat/completions" and authorization == "Bearer test-key"
    assert body["model"] == "test-model" and body["temperature"] == 0
    assert [message["role"] for message in body["messages"]] == ["system", "user"]
    with pytest.raises(LLMError, match="could not generate a query"):  # the server is gone: no retry, a clear error
        ask("How many orders?")
