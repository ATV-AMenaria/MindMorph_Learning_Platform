
import logging
import sys
import os
from typing import Optional, List, Dict, Any


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)

from langchain_mcp_adapters.client import MultiServerMCPClient
from dotenv import load_dotenv
import asyncio
import os
import json
from config import llm
import re

from tools.mcp_timeout import with_mcp_timeout

load_dotenv()

logger = logging.getLogger(__name__)

class JobScraperService:
    def __init__(self):
        self.client = None
        self.token = os.getenv("APIFY_API_KEY")
        self.tools = []

    async def initialize(self):
        """Initialize the MCP client connection.

        The handshake (connect + ``get_tools``) costs ~4.3s and was being re-paid on every
        request, even though this service is a per-process singleton. The tool objects are
        loop-agnostic — each ``ainvoke`` opens its own HTTP session — so they survive the
        fresh event loop that ``learning_service._run_async`` creates per request. Any
        failure below (and in the search/fetch callers) clears ``self.tools``, which makes
        the next call re-handshake rather than reuse a wedged client.
        """
        if self.tools:
            logger.debug("JobScraper MCP: reusing cached tools (%d)", len(self.tools))
            return self.client
        try:
            logger.info("JobScraper MCP: connecting to server...")
            self.client = MultiServerMCPClient(
                {
                    "apify": {
                        "transport": "http",
                        "url": "https://mcp.apify.com/?tools=fantastic-jobs/advanced-linkedin-job-search-api",
                        "headers": {
                            "Authorization": f"Bearer {self.token}"
                        }
                    }
                }
            )
            logger.info("JobScraper MCP: client initialized, fetching available tools...")
            self.tools = await with_mcp_timeout(self.client.get_tools(), what="apify get_tools")
            return self.client
        except asyncio.TimeoutError:
            # Reset the reused client so the next request's initialize() rebuilds clean.
            self.client = None
            self.tools = []
            logger.warning("JobScraper MCP: initialize timed out; client reset")
            raise
        except Exception:
            logger.exception("JobScraper MCP: error initializing client")
            raise

    # Apify actor name for the LinkedIn job-search actor (as exposed by the MCP server).
    SEARCH_TOOL_NAME = "fantastic-jobs--advanced-linkedin-job-search-api"
    OUTPUT_TOOL_NAME = "get-dataset-items"

    async def search_jobs(self, query: str, location: str = "United States") -> Optional[str]:
        """
        Executes the job search actor and returns the resulting Dataset ID.

        The actor returns a run-summary JSON; the dataset id lives at
        storages.datasets.default.id (with an itemCount we use to skip empty runs).
        """
        try:
            job_search_tool = next(
                tool for tool in self.tools
                if tool.name == self.SEARCH_TOOL_NAME
            )

            logger.info("JobScraper: searching for %r in %r", query, location)
            # `limit` must be >= 10 per the actor's input schema.
            results = await with_mcp_timeout(
                job_search_tool.ainvoke({
                    "titleSearch": query,
                    "locationSearch": location,
                    "limit": 10
                }),
                what="apify search_jobs",
            )

            for item in results:
                if item.get('type') != 'text':
                    continue
                run = self._loads_json_object(item['text'])
                if not run:
                    continue
                default_ds = (
                    run.get('storages', {})
                    .get('datasets', {})
                    .get('default', {})
                )
                dataset_id = default_ds.get('id')
                item_count = default_ds.get('itemCount', 0)
                if dataset_id:
                    logger.info("JobScraper: dataset %s ready (%s items)", dataset_id, item_count)
                    if not item_count:
                        # Run succeeded but matched no postings — nothing to fetch.
                        return None
                    return dataset_id

            return None

        except asyncio.TimeoutError:
            self.client = None
            self.tools = []
            logger.warning("JobScraper: search timed out for %r; client reset", query)
            return None
        except StopIteration:
            # A partial cached tool list would otherwise wedge every later request.
            self.client = None
            self.tools = []
            logger.warning("JobScraper: %r tool is not available; client reset", self.SEARCH_TOOL_NAME)
            return None
        except Exception:
            # Tools are cached across requests now, so a wedged client must not persist.
            self.client = None
            self.tools = []
            logger.exception("JobScraper: error searching jobs; client reset")
            return None

    @staticmethod
    def _loads_json_object(text: str) -> Optional[Dict[str, Any]]:
        """Parse a JSON object from MCP text output, tolerant of surrounding noise."""
        try:
            start = text.find('{')
            end = text.rfind('}') + 1
            if start != -1 and end != 0:
                return json.loads(text[start:end], strict=False)
        except Exception:
            return None
        return None

    async def fetch_job_results(self, dataset_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Retrieves the dataset using the ID and parses the JSON text into a list of dictionaries.
        """
        try:
            get_output_tool = next(
                tool for tool in self.tools
                if tool.name == self.OUTPUT_TOOL_NAME
            )

            logger.info("JobScraper: retrieving results from dataset %s", dataset_id)
            results = await with_mcp_timeout(
                get_output_tool.ainvoke({
                    "datasetId": dataset_id,
                    "limit": limit
                }),
                what="apify get-dataset-items",
            )

            all_jobs = []
            for item in results:
                if item['type'] == 'text':
                    jobs = self._parse_job_data(item['text'])
                    all_jobs.extend(jobs)
            
            return all_jobs

        except asyncio.TimeoutError:
            self.client = None
            self.tools = []
            logger.warning("JobScraper: fetch timed out for dataset %s; client reset", dataset_id)
            return []
        except StopIteration:
            self.client = None
            self.tools = []
            logger.warning("JobScraper: %r tool is not available; client reset", self.OUTPUT_TOOL_NAME)
            return []
        except Exception:
            self.client = None
            self.tools = []
            logger.exception("JobScraper: error retrieving job results; client reset")
            return []

    def _parse_job_data(self, text_data: str) -> List[Dict]:
        """Internal helper to parse the text data to extract job postings as JSON"""
        try:
            # Remove markdown code blocks if present
            text_data = re.sub(r'^```json\s*', '', text_data)
            text_data = re.sub(r'\s*```$', '', text_data)
            
            # Extract the JSON part
            start_idx = text_data.find('[')
            end_idx = text_data.rfind(']') + 1
            
            if start_idx != -1 and end_idx != 0:
                json_str = text_data[start_idx:end_idx]
                return json.loads(json_str, strict=False)
            return []
        except json.JSONDecodeError:
            # Try alternative parsing - split by job objects
            try:
                jobs = []
                depth = 0
                start = -1
                current_job = ""
                
                for i, char in enumerate(text_data):
                    if char == '{':
                        if depth == 0:
                            start = i
                        depth += 1
                    elif char == '}':
                        depth -= 1
                        if depth == 0 and start != -1:
                            current_job = text_data[start:i+1]
                            try:
                                job = json.loads(current_job, strict=False)
                                jobs.append(job)
                            except:
                                pass
                            start = -1
                            current_job = ""
                return jobs
            except Exception:
                return []
        except Exception:
            logger.exception("JobScraper: error parsing job data")
            return []






































