import logging
import sys
import os
from typing import Optional


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(PROJECT_ROOT)

logger = logging.getLogger(__name__)


from agents.orchestrator.orchestrator_agents_personality import  AgentMetadata
from prompts.orchestrator_prompt import ORCHESTRATOR_SYSTEM_PROMPT
from prompts.prompt_registry_wrapper_method import setup_agent_prompt
from config import llm
from agents.orchestrator.orchestrator_agent_output_schema import Orchestrator_Output_Schema


class OrchestratorAgent:

    """Orchestrator agent that routes queries to appropriate sub-agents"""
    
    def __init__(self, push_to_langsmith: bool = False):
        self.llm = llm
        self.agent_metadata = AgentMetadata()
        self.structured_llm = self.llm.with_structured_output(
            Orchestrator_Output_Schema, method="json_schema")
        
        self.chat_prompt = setup_agent_prompt(
            system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
            agent_descriptions=self.agent_metadata.get_agent_descriptions(),
            human_template="User Query: {user_query}",
            push_to_langsmith=push_to_langsmith,
            prompt_identifier="orchestrator_agent_routing_prompt",
            description="Prompt used by the Orchestrator Agent to route user queries.",
            tags=["orchestrator", "routing", "agent"]
        )
        


    
            
    def  route_query(self, user_query: str) -> Optional[Orchestrator_Output_Schema]:
            """
            Route user query to appropriate agent with error handling and retry logic.
            
            Args:
                user_query: The user's input query
                
            Returns:
                Orchestrator_Output_Schema object with assigned agent and reasoning, or None on failure
                
            Raises:
                ValueError: If user_query is empty or invalid
            """
            
            # Input validation
            if not user_query or not isinstance(user_query, str):
                raise ValueError("User query must be a non-empty string")
            
            user_query = user_query.strip()
            if len(user_query) == 0:
                raise ValueError("User query cannot be empty after stripping whitespace")
            
            
            
            logger.info("Orchestrator: routing query %r", user_query)
            try:
                    # Format prompt with user query
                    chat_prompt = self.chat_prompt.format_messages(user_query=user_query)

                    # Invoke structured LLM
                    response = self.structured_llm.invoke(chat_prompt)

                    # Validate response structure
                    if not isinstance(response, Orchestrator_Output_Schema):
                        raise TypeError(f"Expected Orchestrator_Output_Schema, got {type(response)}")

                    # Validate assigned agent is one of expected values
                    valid_agents = {"SCOUT", "CONTENT", "EXERCISE"}
                    if response.Assigned_Agent not in valid_agents:
                        raise ValueError(f"Invalid agent: {response.Assigned_Agent}. Must be one of {valid_agents}")

                    logger.info("Orchestrator: routed to %s", response.Assigned_Agent)
                    return response

            except Exception as e:
                logger.exception("Orchestrator: failed to route query")
                raise RuntimeError(f"Failed to route query: {str(e)}")
                    


        
if __name__ == "__main__":
    agent = OrchestratorAgent(push_to_langsmith=False)
    response = agent.route_query("Teach me pointers concept in c language using exercises")
    print(response)



        
'''

    def _setup_prompt(self, push_to_langsmith: bool = False):
        """Setup the prompt templates for the orchestrator agent"""
        
        agents_descriptions = self.agent_metadata.get_agent_descriptions()
        formatted_system_prompt = ORCHESTRATOR_SYSTEM_PROMPT.format(
            agent_descriptions = agents_descriptions
        )

        system_template = SystemMessagePromptTemplate.from_template(formatted_system_prompt)
        
        human_template = HumanMessagePromptTemplate.from_template("User Query: {user_query}")

        self.chat_prompt = ChatPromptTemplate.from_messages([system_template, human_template])

        if push_to_langsmith:

            try:

                self.langsmith_client.push_prompt(
                        object= self.chat_prompt,
                        description="Prompt used by the Orchestrator Agent to route user queries.",
                        tags=["orchestrator", "routing", "agent"],
                        prompt_identifier="orchestrator_agent_routing_prompt" 

                    )
                print("Prompt pushed to langsmith successfully.")

            except Exception as e:
                print(f"Warning: Failed to push orchestrator agent's system prompt to langsmith. Error: {str(e)}")

'''