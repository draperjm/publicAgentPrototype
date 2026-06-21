import os
import json
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from openai import OpenAI

# 1. Load Environment Variables
load_dotenv()

# 2. Define the Registry Logic
class AgentRegistry:
    def __init__(self, registry_path: str = "registry.json"):
        self.registry_path = registry_path
        self.data = self._load_registry()

    def _load_registry(self):
        """Safely loads the JSON registry."""
        if not os.path.exists(self.registry_path):
            print(f"WARNING: {self.registry_path} not found. Returning empty registry.")
            return {"agents": [], "tools": [], "knowledge": [], "operations": []}
        
        with open(self.registry_path, 'r') as f:
            return json.load(f)

    def generate_context_string(self) -> str:
        """
        Converts the JSON data into a clean text block for the LLM prompt.
        """
        lines = ["### AVAILABLE RESOURCES ###"]
        
        # Agents
        lines.append("\n[AVAILABLE AGENTS]")
        for agent in self.data.get("agents", []):
            lines.append(f"- ID: {agent['id']} | Name: {agent['name']}")
            # FIXED: Used .get() correctly here
            lines.append(f"  Capabilities: {', '.join(agent.get('capabilities', []))}")

        # Tools
        lines.append("\n[AVAILABLE TOOLS]")
        for tool in self.data.get("tools", []):
            lines.append(f"- ID: {tool['id']} | Name: {tool['name']}")
            lines.append(f"  Use Case: {tool.get('use_case', 'General purpose')}")

        # Operations
        lines.append("\n[STANDARD OPERATIONS]")
        for op in self.data.get("operations", []):
            lines.append(f"- ID: {op['id']} | Name: {op['name']}")
            lines.append(f"  Triggers: {', '.join(op.get('trigger_keywords', []))}")

        # Knowledge
        lines.append("\n[KNOWLEDGE BASES]")
        for kb in self.data.get("knowledge", []):
            lines.append(f"- ID: {kb['id']} | Name: {kb['name']}")
            # FIXED: Syntax error resolved here
            lines.append(f"  Topics: {', '.join(kb.get('topics', []))}")

        return "\n".join(lines)

# 3. Initialize App & Components
app = FastAPI(title="Orchestrator Agent")
registry = AgentRegistry()  # Load the registry once at startup
client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY"),
    # base_url=os.environ.get("OPENAI_BASE_URL") # Uncomment if using non-OpenAI endpoint
)

class TaskRequest(BaseModel):
    task: str

@app.post("/decompose")
def decompose_task(request: TaskRequest):
    """
    Analyzes the task against the registry and creates a resource-aware plan.
    """
    if not request.task:
        raise HTTPException(status_code=400, detail="Task string cannot be empty")

    # A. Get the current available resources
    registry_context = registry.generate_context_string()

    # B. Construct the Intelligent Prompt
    system_prompt = f"""
    You are an Expert AI Orchestrator. 
    Your goal is to break down a complex User Task into actionable steps.
    
    CRITICAL INSTRUCTION:
    You must assign the most appropriate resource from the "AVAILABLE RESOURCES" list to each step.
    If no specific agent/tool is suitable, assign it to "Responder_Core".

    {registry_context}
    """

    user_prompt = f"""
    USER TASK: {request.task}

    OUTPUT FORMAT:
    Please provide the plan in JSON format with the following structure:
    {{
      "plan_overview": "One sentence summary",
      "steps": [
        {{
          "step_number": 1,
          "description": "What to do",
          "assigned_resource_id": "agent-id-or-tool-id",
          "reasoning": "Why you chose this resource"
        }}
      ]
    }}
    """

    try:
        response = client.chat.completions.create(
            model=os.environ.get("LLM_MODEL", "gpt-3.5-turbo"),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.2, # Low temperature for strict routing logic
            response_format={"type": "json_object"} # Forces valid JSON output
        )
        
        result_text = response.choices[0].message.content
        
        # Parse JSON to ensure it's valid before returning
        return json.loads(result_text)

    except Exception as e:
        print(f"Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    # Reload=True allows you to edit registry.json and see changes instantly
    uvicorn.run("agent:app", host="0.0.0.0", port=8000, reload=True)