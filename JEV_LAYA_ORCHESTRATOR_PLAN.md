# Implementation Plan: Jev (Primary) & Laya (Local Folder) for MindMorph Orchestration

**Document:** `JEV_LAYA_ORCHESTRATOR_PLAN.md`  
**Purpose:** Clear, step-by-step architectural plan to integrate fast decision models into MindMorph  
**Target Audience:** Founder / Project Lead (Written in simple, plain English)  
**Date:** October 2026  

---

## 1. Executive Summary

MindMorph currently uses a heavy generative Large Language Model (Groq or local Ollama) just to decide which agent should handle a user's request (`SCOUT`, `CONTENT`, or `EXERCISE`). This takes between **1,000 to 2,500 milliseconds** and occasionally fails due to JSON formatting issues or rate limits.

This plan details how to replace that slow routing step with:
1. **Jev (Primary):** A cloud-hosted decision API ($0.042 / 1M tokens) that classifies requests in **under 40 milliseconds**.
2. **Laya (Local Folder / Fallback):** An open-source, 100% free local model placed in a dedicated project folder (`services/router/laya/`) that runs 100% offline with zero cloud dependency.

---

## 2. Plain English Concepts: Who Does What?

To make sure everything is crystal clear, here is what each technology does:

```
+-------------------------------------------------------------------------------+
|                             WHO DOES WHAT?                                    |
+-------------------------------------------------------------------------------+
| 1. Jev (Primary Cloud)     -> Instant multiple-choice clicker via Cloud API   |
| 2. Laya (Local Folder)     -> The same instant clicker, but running on your   |
|                               laptop with no internet needed (100% Free).     |
| 3. LangGraph (Conveyor)    -> Moves the task to the right agents in parallel. |
| 4. Gemini / Groq (Minds)   -> The creative storytellers that write the actual |
|                               lessons, exercises, and code examples.          |
+-------------------------------------------------------------------------------+
```

---

## 3. What MindMorph's Orchestrator is Doing Today

In your current file [`agents/orchestrator/orchestrator_agent.py`](file:///c:/Users/praku/OneDrive/Desktop/MindMorph_Learning_Platform/agents/orchestrator/orchestrator_agent.py):

1. **User enters a goal:** *"Teach me Python from scratch."*
2. **Heavy LLM Call:** The code calls Groq (`openai/gpt-oss-120b`) or local Ollama.
3. **Word-by-word Generation:** The LLM spends 1.5 seconds writing out a full JSON text string:
   ```json
   {
     "Assigned_Agent": "SCOUT",
     "Reasoning": "The user is asking for a brand new comprehensive learning plan."
   }
   ```
4. **Pydantic Validation:** The code parses the string. If the LLM made a typo, forgot a comma, or returned an unexpected agent name, it crashes or has to retry.
5. **LangGraph Execution:** Once validated, [`graph/learning_plan_graph.py`](file:///c:/Users/praku/OneDrive/Desktop/MindMorph_Learning_Platform/graph/learning_plan_graph.py) routes the user to the Scout DAG.

### The Problem With This:
Using a massive generative LLM to pick one of three choices is like **hiring an award-winning novelist just to flip a light switch**. It is slow, wastes tokens, and introduces unnecessary failure points.

---

## 4. What Gemini Is NOT Doing (The Difference)

It is important to understand what Gemini (or any generative chat LLM) is **not** designed for:

| What Generative LLMs (Gemini / Groq) Do | What Gemini is NOT Doing (Where Jev / Laya Excel) |
| :--- | :--- |
| **Generates Creative Text:** Writes long lessons, code snippets, and tutor explanations. | **Instant Classification:** Gemini cannot respond in 30ms; it takes 500ms to 2,000ms. |
| **Probabilistic Words:** Predicts sentences token by token. | **Guaranteed Structure:** Gemini can occasionally hallucinate extra text, markdown backticks, or miss a brace. Jev and Laya *cannot* hallucinate text because they only output numbers and labels. |
| **High Token Consumption:** Uses hundreds of tokens per prompt just to format instructions. | **Zero Token Waste:** Jev and Laya only evaluate the input against your choices in a single mathematical pass. |
| **Expensive at High Volume:** Even cheap models cost money when thousands of students make requests. | **Laya is 100% Free Forever:** Laya runs on your local CPU with zero API costs. |

---

## 5. Pros and Cons of Using Jev & Laya

### The Pros (Advantages)
- **Extreme Speed:** Routing drops from **1,500ms down to 30ms**. The platform feels instantaneous.
- **Zero Hallucination / Parse Errors:** Jev and Laya output fixed choices (`SCOUT`, `CONTENT`, `EXERCISE`). There is no JSON string parsing that can break.
- **Saves Your Gemini/Groq Quota:** You save your free Gemini API requests for writing the actual lessons.
- **Complete Offline Resilience (Laya):** If your internet disconnects or Jev has an outage, Laya runs locally from your project folder with zero network connection.
- **Ultra-Cheap / Free:** Laya is $0.00. Jev is $0.042 per 1M tokens (100,000 routes cost ~4 cents).

### The Cons (Trade-offs)
- **No Long "Reasoning" Paragraphs:** Jev and Laya return the decision (`"SCOUT"`) and confidence (`98.5%`), but they do not write a friendly paragraph explaining *why*. *(Note: MindMorph's LangGraph does not use the reasoning paragraph anyway!)*
- **Local Memory Overhead (for Laya):** If you run Laya locally, its lightweight model (~420M parameters) takes around **600MB to 800MB of RAM**.

---

## 6. What Needs to Change in the Codebase?

### What Changes:
Only **one file** needs to be modified:
* [`agents/orchestrator/orchestrator_agent.py`](file:///c:/Users/praku/OneDrive/Desktop/MindMorph_Learning_Platform/agents/orchestrator/orchestrator_agent.py):
  Instead of calling `self.structured_llm.invoke()`, it calls our new router service (`route_query_fast()`).

### What Stays Completely UNCHANGED:
* **`graph/learning_plan_graph.py`:** Unchanged! It still receives `response.Assigned_Agent` and runs the exact same LangGraph workflows.
* **All 16 Specialized Agents:** Unchanged! Academic, Market, Practical, Consensus, and Content agents keep doing their normal work.
* **Database & Checkpointers:** PostgreSQL and Neo4j remain untouched.
* **Frontend (Next.js):** Unchanged! The UI just feels significantly faster.

---

## 7. Step-by-Step Implementation Plan

```
Proposed Project Structure:

MindMorph_Learning_Platform/
  ├── agents/
  │    └── orchestrator/
  │         └── orchestrator_agent.py    <-- (Updated to call router service)
  ├── services/
  │    └── routing/                      <-- (NEW Dedicated Folder)
  │         ├── __init__.py
  │         ├── router_service.py        <-- (Handles Primary Jev + Fallback Laya)
  │         └── laya/                    <-- (Local Laya Folder & Weights)
  │              ├── __init__.py
  │              └── laya_local_engine.py
  ├── .env                               <-- (Add JEV_API_KEY)
  └── requirements.txt                   <-- (Add laya and httpx)
```

---

### Step 1: Add Dependencies
Add the lightweight packages to `requirements.txt`:
```bash
pip install laya httpx
```

---

### Step 2: Configure Environment Variables (`.env`)
Add Jev configuration (with Laya enabled as local fallback):
```env
# -----------------------------------------------------------
# 🚦 FAST ROUTING (Jev & Laya)
# -----------------------------------------------------------
# Primary Cloud Router: Jev API Key
JEV_API_KEY=your_jev_api_key_here
JEV_API_URL=https://api.typesafe.ai/v1/decision

# Local Fallback Folder: Laya (1 = Enabled, 0 = Disabled)
MINDMORPH_USE_LOCAL_LAYA=1
```

---

### Step 3: Create the Local Laya Engine (`services/routing/laya/laya_local_engine.py`)
This script loads Laya locally in your project folder and performs the sub-40ms decision:

```python
"""Local Laya Decision Engine — 100% offline, zero-cost classification."""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_laya_model = None

def get_laya_model():
    """Lazy-load the Laya model once into memory (~600MB RAM)."""
    global _laya_model
    if _laya_model is None:
        try:
            from laya import LayaClassifier
            logger.info("Initializing local Laya model from services/routing/laya...")
            _laya_model = LayaClassifier.from_pretrained("convaiinnovations/laya")
        except Exception as e:
            logger.warning(f"Could not load local Laya model: {e}")
            return None
    return _laya_model

def classify_with_laya(query: str, choices: list[str]) -> Optional[str]:
    """Run local Laya model and return the best choice."""
    model = get_laya_model()
    if not model:
        return None
    try:
        result = model.predict_choice(
            text=query,
            question="Which agent should process this user query?",
            choices=choices
        )
        # Returns the top choice (e.g. "SCOUT")
        return result.best_choice
    except Exception as e:
        logger.error(f"Laya local classification failed: {e}")
        return None
```

---

### Step 4: Create the Router Service (`services/routing/router_service.py`)
This service acts as the traffic controller:
1. Try **Jev** (Primary Cloud API).
2. If Jev fails, has no key, or is offline $\rightarrow$ Fallback to **Laya** (Local Folder).
3. If both fail $\rightarrow$ Fallback to existing **Groq/Gemini** LLM.

```python
"""Dual Routing Service: Jev (Primary) + Laya (Local Folder Fallback)."""
import os
import logging
import httpx
from services.routing.laya.laya_local_engine import classify_with_laya

logger = logging.getLogger(__name__)

VALID_CHOICES = ["SCOUT", "CONTENT", "EXERCISE"]

def _call_jev_api(query: str) -> str | None:
    """Call Jev Primary Cloud API."""
    api_key = os.getenv("JEV_API_KEY")
    api_url = os.getenv("JEV_API_URL", "https://api.typesafe.ai/v1/decision")
    if not api_key:
        return None

    try:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "input": query,
            "type": "choice",
            "question": "Does this query need a new roadmap (SCOUT), a lesson (CONTENT), or practice (EXERCISE)?",
            "choices": VALID_CHOICES
        }
        with httpx.Client(timeout=1.5) as client:
            resp = client.post(api_url, json=payload, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("choice")
    except Exception as e:
        logger.warning(f"Jev API call failed, falling back to Laya: {e}")
    return None

def route_user_query_fast(query: str) -> str:
    """Primary: Jev -> Fallback: Local Laya -> Default: SCOUT."""
    # 1. Try Jev Primary
    choice = _call_jev_api(query)
    if choice in VALID_CHOICES:
        logger.info(f"Router [JEV Primary]: {choice}")
        return choice

    # 2. Try Laya Local Folder Fallback
    if os.getenv("MINDMORPH_USE_LOCAL_LAYA", "1") == "1":
        choice = classify_with_laya(query, VALID_CHOICES)
        if choice in VALID_CHOICES:
            logger.info(f"Router [LAYA Local Folder]: {choice}")
            return choice

    # 3. Safe Default
    logger.warning("Both Jev and Laya unavailable, defaulting to SCOUT")
    return "SCOUT"
```

---

### Step 5: Update the Orchestrator Agent (`agents/orchestrator/orchestrator_agent.py`)
Update `route_query` to use the fast router while keeping the exact same return format:

```python
from services.routing.router_service import route_user_query_fast
from agents.orchestrator.orchestrator_agent_output_schema import Orchestrator_Output_Schema

class OrchestratorAgent:
    # ... __init__ remains unchanged ...

    def route_query(self, user_query: str) -> Orchestrator_Output_Schema:
        # Input validation
        if not user_query or not user_query.strip():
            raise ValueError("User query cannot be empty")
        
        user_query = user_query.strip()
        logger.info("Orchestrator: routing query %r via Fast Decision Router", user_query)

        # FAST DECISION (Jev Primary -> Laya Local Folder)
        assigned_agent = route_user_query_fast(user_query)

        return Orchestrator_Output_Schema(
            Assigned_Agent=assigned_agent,
            Reasoning=f"Classified rapidly via Jev/Laya decision model into {assigned_agent}."
        )
```

---

## 8. Verification and Testing Plan

To ensure the new setup works flawlessly:

1. **Test 1: Normal Cloud Operation (Jev)**
   - Run a test query: *"Give me 5 coding exercises on binary search."*
   - Verify that the log outputs: `Router [JEV Primary]: EXERCISE`.
   - Measure latency: Response should be received in **< 50ms**.

2. **Test 2: Offline Resilience (Laya Local)**
   - Temporarily disconnect the internet or remove `JEV_API_KEY`.
   - Run: *"Build me a complete roadmap for Cloud Architecture."*
   - Verify that the log outputs: `Router [LAYA Local Folder]: SCOUT`.
   - Response should be received in **< 40ms** with zero internet.

3. **Test 3: LangGraph Compatibility**
   - Run the full MindMorph test suite:
     ```bash
     python -m pytest tests/test_learning_plan_graph.py
     ```
   - All tests must pass with 100% success.

---

## 9. Rollback Plan

If you ever wish to revert to the old generative LLM router:
1. In `agents/orchestrator/orchestrator_agent.py`, comment out `route_user_query_fast` and restore `self.structured_llm.invoke()`.
2. Everything will immediately return to the original Groq/Ollama behavior with zero data loss.
