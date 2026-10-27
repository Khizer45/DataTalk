import pytest

from app import generator
from app.limits import DailyQuota
from app.rules import SAMPLE_QUESTIONS, UnsupportedQuestionError, rule_based_sql


@pytest.mark.parametrize("question", SAMPLE_QUESTIONS)
def test_every_suggested_question_is_answered(question):
    sql, explanation = rule_based_sql(question)
    assert sql.startswith("SELECT") and explanation


def test_top_n_uses_the_requested_number():
    assert rule_based_sql("Show the top 3 products by revenue")[0].endswith("LIMIT 3")


def test_case_spacing_and_punctuation_are_ignored():
    assert rule_based_sql("  how many PENDING orders are there?? ")[0] == rule_based_sql("How many orders are pending?")[0]


@pytest.mark.parametrize("question", [
    "How many customers are from Pakistan?",
    "How many customers are there in each country?",
    "What is each customer's phone number?",
    "Delete all orders",
    "Show revenue by month for the website channel",
])
def test_questions_with_extra_conditions_are_declined_not_guessed(question):
    with pytest.raises(UnsupportedQuestionError):
        rule_based_sql(question)


def test_date_ranges_get_a_specific_message():
    with pytest.raises(UnsupportedQuestionError, match="date ranges"):
        rule_based_sql("Which products generated the most revenue last quarter?")


@pytest.mark.parametrize("question", ["What was total revenue by month?", "Show the top 5 products by revenue",
                                      "Show revenue by customer country"])
def test_revenue_counts_completed_orders_only(question):
    assert "status = 'completed'" in rule_based_sql(question)[0]


@pytest.fixture
def model_calls(monkeypatch):
    monkeypatch.setattr(generator.settings, "llm_api_key", "test-key")
    monkeypatch.setattr(generator.settings, "llm_model", "test-model")
    calls = []
    monkeypatch.setattr(generator, "_llm_sql", lambda question, schema: calls.append(question) or ("SELECT 1", "llm", ""))
    return calls


def test_daily_ai_limit_falls_back_to_demo_mode(model_calls, monkeypatch):
    monkeypatch.setattr(generator, "_llm_quota", DailyQuota(1))
    assert generator.generate_sql("Which channel grew fastest?", "")[1] == "llm"
    assert generator.generate_sql("How many orders are pending?", "")[1] == "rules"
    with pytest.raises(UnsupportedQuestionError, match="today's limit"):
        generator.generate_sql("Which channel grew fastest?", "")
    assert model_calls == ["Which channel grew fastest?"]
