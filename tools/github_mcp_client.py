import logging
import re
from langchain_mcp_adapters.client import MultiServerMCPClient
from dotenv import load_dotenv
import asyncio
import os

from tools.mcp_timeout import with_mcp_timeout

load_dotenv()

logger = logging.getLogger(__name__)


# --- query normalization -------------------------------------------------------
#
# Callers hand this client the Scout agent's natural-language PRACTICAL question. GitHub's
# `q` is a keyword search with a hard length limit, so prose fails two ways:
#   1. Over-length -> `ToolException: Search.q (invalid): The search is longer than 256
#      characters`, which used to bubble up and silently degrade the caller to ungrounded.
#      The documented limit is 256, but in logs/mindmorph.log the shortest rejected query was
#      203 raw characters while 197 succeeded. The mechanism is undetermined (the MCP server may
#      add qualifiers to `q`), so 200 is a conservative margin rather than the stated 256.
#   2. Even in-bounds prose is a poor query: GitHub ANDs bare terms across name/description/
#      readme, so a whole question matches little and a long keyword soup matches nothing.
# Hence: extract a few salient terms, then bound the result. No LLM — this sits on a latency
# -sensitive path.
MAX_QUERY_CHARS = 200
MAX_QUERY_TERMS = 6

# Scout writes typographic punctuation (U+2011 non-breaking hyphens in "open‑source",
# curly quotes); fold it to ASCII so tokenizing sees "open-source" as one term.
_PUNCT_FOLD = str.maketrans(
    {
        "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-",
        "‘": "'", "’": "'", "“": '"', "”": '"',
    }
)

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9+#._-]*")

# English function words plus the generic research vocabulary the Scout prompt keeps producing
# ("what common open-source projects illustrate..."). Dropping these is what leaves the actual
# topic terms behind. "github" is noise in a GitHub search.
_STOPWORDS = frozenset(
    """
    a an and are as at basic basics be been build building built by can common commonly compared
    concepts demonstrate demonstrates do does e.g etc for found from frequently github hands how i.e
    illustrate illustrates implement implemented implementations in into is it its learn learning like
    make making many most of on open open-source or other others patterns popular practical practice
    practitioners problem problems production project projects real real-world really rely showcase show
    solve some the their them these they this those to typically understand use used uses using want
    ways well what when where which while who why with work world would you your example examples code
    codebases thing things type types
    """.split()
)


def _truncate_on_word(text: str, limit: int = MAX_QUERY_CHARS) -> str:
    """Cut ``text`` to at most ``limit`` chars without splitting a word."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut = head.rfind(" ")
    return (head[:cut] if cut > 0 else head).strip()


def to_search_query(text: str) -> str:
    """Turn a natural-language question into a short, bounded GitHub keyword query.

    Technology-ish tokens win the limited slots first (mid-sentence capitals like "Rust" /
    "Kubernetes", and anything non-alphabetic like "C++", "node.js", "3.11"); the remaining
    slots are filled with content words in the order they appear. Falls back to a
    word-boundary truncation of the original when extraction strips everything, because an
    empty `q` is also rejected by GitHub. The return value is always <= MAX_QUERY_CHARS.
    """
    normalized = " ".join((text or "").translate(_PUNCT_FOLD).split())
    tokens = _TOKEN_RE.findall(normalized)
    # Index 0 is skipped for the capitalization test: a sentence-initial capital says nothing.
    tech = [t for i, t in enumerate(tokens) if i and (t[0].isupper() or not t.isalpha())]

    terms: list[str] = []
    seen: set[str] = set()
    for token in tech + tokens:
        term = token.strip("._-")  # trailing sentence punctuation; "node.js"/"C++" survive intact
        key = term.lower()
        if len(key) < 2 or key in _STOPWORDS or key in seen:
            continue
        seen.add(key)
        terms.append(term)
        if len(terms) == MAX_QUERY_TERMS:
            break

    return _truncate_on_word(" ".join(terms) or normalized)



class MCPClientInitialization:
    def __init__ (self):
        self.client = None
        self.tools = None
        self.token = os.getenv("GITHUB_PERSONAL_TOKEN")
        


    async def initialize(self):
        try:

            logger.info("GitHub MCP: connecting to server...")
            # Initialize the MCP client for GitHub
            self.client = MultiServerMCPClient(
                {
                    "github": {
                        "transport": "http",
                        "url": "https://api.githubcopilot.com/mcp/",
                        "headers": {
                            "Authorization": f"Bearer {self.token}"
                        }
                    }
                },
            
            )


                            
            logger.info("GitHub MCP: client created, fetching available tools...")

            tools = await with_mcp_timeout(self.client.get_tools(), what="github get_tools")
            # Cache: search_github_repositories used to re-fetch this same list (a second ~5s
            # round-trip per graph build).
            self.tools = tools
            logger.info("GitHub MCP: initialized with %d tool(s)", len(tools))

            # Full tool details only at DEBUG level (verbose).
            for tool in tools:
                logger.debug("GitHub MCP tool: %s — %s — args=%s", tool.name, tool.description, tool.args)

        except asyncio.TimeoutError:
            # Timed out before the client is usable — drop it so a later call rebuilds.
            self.client = None
            self.tools = None
            logger.warning("GitHub MCP: initialize timed out; client discarded")
            raise
        except Exception:
            logger.exception("GitHub MCP: error initializing client")
            raise


    async def search_github_repositories(self, query):
        # Callers pass prose; GitHub wants bounded keywords. Normalizing here (rather than at each
        # call site) keeps the full question available to the caller for its LLM prompt while making
        # it structurally impossible for any caller to trip the `q` length limit again.
        q = to_search_query(query)
        if not q:
            logger.warning("GitHub MCP: empty search query after normalization; skipping search")
            return None
        if q != query:
            logger.info("GitHub MCP: normalized %d-char query to %r", len(query or ""), q)
        try:
            tools = self.tools or await with_mcp_timeout(self.client.get_tools(), what="github get_tools")
            search_tool = next(tool for tool in tools if tool.name == "search_repositories")
            result = await with_mcp_timeout(
                search_tool.ainvoke({"query": q, "perPage": 5}),
                what="github search_repositories",
            )
            logger.info("GitHub MCP: search returned results for %r", q)
            logger.debug("GitHub MCP search results for %r:\n%s", q, result)
            return result
        except asyncio.TimeoutError:
            # Half-open client — discard so the next call rebuilds cleanly.
            self.client = None
            self.tools = None
            logger.warning("GitHub MCP: search timed out for %r; client discarded", q)
            return None
        except StopIteration:
            logger.warning("GitHub MCP: 'search_repositories' tool is not available")
            return None
        except Exception:
            logger.exception("GitHub MCP: error invoking search tool")
            return None
            
    async def run(self):
        await self.initialize()
        await self.search_github_repositories("langchain MCP projects in python")
        print("\n" + "=" * 60 + "\n")

async def main():
    """Main function to run all operations in a single event loop"""
    client = MCPClientInitialization()
    await client.run()


if __name__ == "__main__":
    # Run everything in a single event loop
    asyncio.run(main())
 


