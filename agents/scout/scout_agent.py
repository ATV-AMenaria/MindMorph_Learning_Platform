import logging
import sys
import os
from typing import Optional


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(PROJECT_ROOT)

logger = logging.getLogger(__name__)


from config import get_chat_model
from prompts.prompt_registry_wrapper_method import setup_agent_prompt
from prompts.scout_prompt_for_prompt import SCOUT_SYSTEM_PROMPT_FOR_PROMPT 
from prompts.scout_prompt_for_queries import SCOUT_SYSTEM_PROMPT_FOR_QUERIES
from agents.scout.scout_agents_personalities import scout_agent_personalities
from agents.scout.scout_agent_output_schema import ScoutOutputSchema



class ScoutAgent:
    '''Scout agent that gathers preliminary information from orchestrator agent output. And routes the query to the evry sub-agent that it has. Before that it generates specialized queries for every sub-agent.'''
    
    def __init__(self, push_to_langsmith: bool = False, output_variant: Optional[str] = None):
        self.llm = get_chat_model("complex")  # plans sub-agent queries once per session
        
        if output_variant is not None and output_variant not in ("Prompt", "Query"):
            raise ValueError("output_variant must be either 'Prompt' or 'Query'")
        
        self.output_variant = output_variant or "Query"
        self.agent_personality = scout_agent_personalities()
        self.chat_prompt = setup_agent_prompt(
            system_prompt= SCOUT_SYSTEM_PROMPT_FOR_PROMPT if self.output_variant == "Prompt" else SCOUT_SYSTEM_PROMPT_FOR_QUERIES,
            agent_descriptions=self.agent_personality.get_scout_agent_description(),
            human_template="User Query: {user_query}",
            push_to_langsmith=push_to_langsmith,
            prompt_identifier="scout_agent_system_prompt",
            description="Prompt used by the Scout Agent to generate specialized queries for sub-agents(Academic, Market, Practical).",
            tags=["scout", " specialized query generation", "agent"]
        )

        self.structured_llm = self.llm.with_structured_output(ScoutOutputSchema, method="json_schema")

    
    def generate_specialized_queries(self, user_query:str):
        '''Generates specialized queries for each sub-agent based on the user query.'''

        # Input validation
        if not user_query or not isinstance(user_query, str):
                raise ValueError("User query must be a non-empty string")
            
        user_query = user_query.strip()
        if len(user_query) == 0:
                raise ValueError("User query cannot be empty after stripping whitespace")
        
        logger.info("Scout: generating specialized sub-agent queries for %r", user_query)

        try:
             chat_prompt = self.chat_prompt.format_messages(user_query=user_query)
             response = self.structured_llm.invoke(chat_prompt)
             return response
        except Exception:
                logger.exception("Scout: error generating specialized queries")
                return None

            
            


        
# Example usage
if __name__ == "__main__":
    scout_agent = ScoutAgent(push_to_langsmith = False, output_variant="Query")
    response = scout_agent.generate_specialized_queries("I want to learn web development")
    print(response)
