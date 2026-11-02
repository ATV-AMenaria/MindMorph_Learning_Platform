from agents.exercise.grader_agent import _bounded_setup_model
from llm_providers import ProviderChain


def test_grading_output_limits_do_not_mutate_shared_provider_models():
    from langchain_groq import ChatGroq
    from langchain_ollama import ChatOllama
    primary=ChatGroq(model='openai/gpt-oss-120b',api_key='test-key-not-used',max_tokens=8192,max_retries=0)
    fallback=ChatOllama(model='qwen2.5:7b',num_predict=8192)
    original=ProviderChain([primary],fallback)
    bounded=_bounded_setup_model(original)
    assert bounded is not original
    assert bounded.primary[0].max_tokens==4096 and bounded.fallback.num_predict==4096
    assert primary.max_tokens==8192 and fallback.num_predict==8192
    assert bounded.primary[0].model_name==primary.model_name and bounded.fallback.model==fallback.model
