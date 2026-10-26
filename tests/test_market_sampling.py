"""The Market summary now samples every posting the scraper fetched, not just jobs[0].

Two things have to hold for the frequency claim it makes to be worth anything: the counts must be
exact (they are computed in Python precisely so the model cannot miscount them), and the sample
must actually be the whole fetch rather than one posting wearing a denominator.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from agents.market.market_agent import MarketAnalysisAgent


def _agent():
    """An agent without __init__ — no scraper, no real model, just the prompt-building code."""
    agent = MarketAnalysisAgent.__new__(MarketAnalysisAgent)
    agent.llm = MagicMock()
    agent.llm.ainvoke = AsyncMock(return_value=MagicMock(content="SUMMARY"))
    return agent


_REPOST_RUN = [
    # One aggregator, six reposts of the same role at different seniority labels.
    *[
        {"title": t, "organization": "Jobright.ai",
         "ai_key_skills": ["Python", "Machine Learning", "Recommendation Systems"]}
        for t in ("Senior Level", "Mid Level", "New Grad",
                  "Applied AI - New Grad", "Intern", "Entry Level")
    ],
    {"title": "ML Engineer", "organization": "Amgen",
     "ai_key_skills": ["Python", "Machine Learning"]},
    {"title": "Senior ML Engineer", "organization": "Amgen",
     "ai_key_skills": ["Python", "SQL"]},
    {"title": "ML Engineer", "organization": "TalentHop",
     "ai_key_skills": ["Python", "Machine Learning"]},
    {"title": "ML Engineer", "organization": "RemoteHunter",
     "ai_key_skills": ["Python", "SQL"]},
]


def _posting(*skills, org=None):
    job = {"title": "ML Engineer", "ai_key_skills": list(skills)}
    if org is not None:
        job["organization"] = org
    return job


def test_frequency_table_counts_every_employer_and_keeps_the_denominator():
    jobs = [
        _posting("Python", "PyTorch", org="A"),
        _posting("Python", "SQL", org="B"),
        _posting("Python", org="C"),
    ]

    table = MarketAnalysisAgent._frequency_table(MarketAnalysisAgent._group_by_employer(jobs))

    assert "Python - 3/3 employers" in table
    # Named once, so it is below the ranking threshold — counted, not silently dropped.
    assert "PyTorch" not in table
    assert "2 further skills are named by exactly 1/3 employers" in table


async def test_summary_prompt_carries_all_postings_not_just_the_first():
    agent = _agent()
    jobs = [
        _posting("Python", org="A"),
        _posting("Python", "Kubernetes", org="B"),
        _posting("Kubernetes", org="C"),
    ]

    assert await agent.summarize_job(jobs) == "SUMMARY"

    prompt = agent.llm.ainvoke.await_args.args[0]
    assert "Employer 3 of 3" in prompt
    assert "Kubernetes - 2/3 employers" in prompt


async def test_a_single_posting_dict_still_works_and_says_so():
    agent = _agent()

    assert await agent.summarize_job(_posting("Python")) == "SUMMARY"

    prompt = agent.llm.ainvoke.await_args.args[0]
    assert "1 job postings were sampled" in prompt
    # One employer cannot support a frequency claim, and the table says that rather than
    # reporting "Python - 1/1 employers" as if it were evidence of demand.
    assert "No skill is named by more than one employer." in prompt


async def test_sample_is_capped_so_a_larger_fetch_cannot_blow_the_token_budget():
    agent = _agent()

    await agent.summarize_job([_posting("Python", org=f"Org {i}") for i in range(25)])

    prompt = agent.llm.ainvoke.await_args.args[0]
    assert f"{MarketAnalysisAgent.MAX_POSTINGS} job postings were sampled" in prompt
    assert f"Employer {MarketAnalysisAgent.MAX_POSTINGS + 1} of" not in prompt


def test_salary_reads_the_keys_the_actor_actually_returns():
    # ai_salary_minvalue (no underscores) matched nothing, so every posting read "not stated".
    job = {
        "ai_salary_min_value": 150000,
        "ai_salary_max_value": 200000,
        "ai_salary_currency": "USD",
        "ai_salary_unit_text": "YEAR",
    }

    assert MarketAnalysisAgent._salary_line(job) == "150,000 - 200,000 USD per YEAR"
    assert MarketAnalysisAgent._salary_line({}) == "not stated"


def test_grouping_collapses_an_aggregator_repost_series_to_one_employer():
    # The verified run: ten rows, four employers, six of them one aggregator's repost series of
    # the same role. "Recommendation Systems" is named only by that series.
    jobs = _REPOST_RUN

    groups = MarketAnalysisAgent._group_by_employer(jobs)
    table = MarketAnalysisAgent._frequency_table(groups)

    assert len(groups) == 4
    assert [g["postings"] for g in groups] == [6, 2, 1, 1]
    # The denominator is employers, and it says so — "6/10 postings" read as market demand.
    assert "/10 postings" not in table
    assert "Python - 4/4 employers" in table
    # Named by one employer six times is one employer: below the ranking threshold, and it
    # survives only in the long-tail line.
    assert "Recommendation Systems" not in table.split("\n(")[0]
    assert "further skills are named by exactly 1/4 employers" in table


async def test_prompt_never_offers_the_posting_count_as_a_denominator():
    agent = _agent()

    await agent.summarize_job(_REPOST_RUN)

    prompt = agent.llm.ainvoke.await_args.args[0]
    # The honest sample size is visible both ways round: ten rows fetched, four employers counted.
    assert "10 job postings were sampled" in prompt
    assert "4 distinct employers" in prompt
    assert "Employer 1 of 4" in prompt
    assert "posted 6 variants of the same role" in prompt
    assert "/10 postings" not in prompt


async def test_market_node_persists_no_recruiter_or_contact_data():
    """`market_output` is shipped onto the wire and persisted with the session (see
    services/learning_service.py). Only `summary` is ever read, so the raw posting - which carries
    a named recruiter, their LinkedIn URL and a contact email - must not be in it at all.

    Asserted as an allowlist, not a blocklist of known-bad keys: a blocklist passes the day some
    other raw field is added back.
    """
    import graph.learning_plan_graph as glp

    market = MagicMock()
    market.extract_job_title = AsyncMock(return_value="ML Engineer")
    market.scraper.initialize = AsyncMock()
    market.scraper.search_jobs = AsyncMock(return_value="dataset123")
    market.scraper.fetch_job_results = AsyncMock(return_value=[{
        "title": "ML Engineer",
        "organization": "Acme",
        "recruiter_name": "A Person",
        "recruiter_url": "https://www.linkedin.com/in/a-person",
        "ai_hiring_manager_email_address": "a.person@example.com",
        "description_text": "the entire raw posting prose",
    }])
    market.summarize_job = AsyncMock(return_value="JOB_SUMMARY")

    out = await glp._run_market(market, "learn ML")

    assert set(out) == {"sampled", "summary"}
    assert out == {"sampled": 1, "summary": "JOB_SUMMARY"}
