import os
import json
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from openai import OpenAI

# 1. Load Environment Variables
load_dotenv()

# --- DEFINITIONS ---

class TaskLibrary:
    def __init__(self, tasks_path: str = "tasks.json"):
        self.tasks_path = tasks_path
        self.data = self._load_tasks()

    def _load_tasks(self):
        """Safely loads the JSON registry."""
        if not os.path.exists(self.tasks_path):
            print(f"WARNING: {self.tasks_path} not found. Starting with empty task library.")
            return []
        
        try:
            with open(self.tasks_path, 'r') as f:
                raw_data = json.load(f)
                
                # HANDLE "templates" WRAPPER
                if isinstance(raw_data, dict) and "templates" in raw_data:
                    return raw_data["templates"]
                elif isinstance(raw_data, list):
                    return raw_data
                else:
                    print("WARNING: tasks.json structure unrecognized. Returning empty.")
                    return []
                    
        except json.JSONDecodeError:
            print(f"ERROR: {self.tasks_path} is not valid JSON.")
            return []

    def find_matching_plan(self, user_task: str):
        """Scans 'templates' for a match in 'name' or 'triggers'."""
        normalized_input = user_task.lower().strip()
        
        for template in self.data:
            # 1. Check Name
            if template.get("name", "").lower() in normalized_input:
                return self._format_output(template)

            # 2. Check Triggers List
            triggers = template.get("triggers", [])
            if isinstance(triggers, list):
                for t in triggers:
                    if t.lower() in normalized_input:
                        return self._format_output(template)
            elif isinstance(triggers, str):
                if triggers.lower() in normalized_input:
                    return self._format_output(template)
                    
        return None

    def _format_output(self, template):
        return {
            "plan_overview": template.get("description", "Predefined Task"),
            "steps": template.get("steps", [])
        }

class AgentRegistry:
    def __init__(self, registry_path: str = "registry.json"):
        self.registry_path = registry_path
        self.data = self._load_registry()

    def _load_registry(self):
        if not os.path.exists(self.registry_path):
            return {"agents": [], "tools": [], "knowledge": [], "operations": []}
        try:
            with open(self.registry_path, 'r') as f:
                return json.load(f)
        except:
            return {"agents": [], "tools": [], "knowledge": [], "operations": []}

    def generate_context_string(self) -> str:
        lines = ["### AVAILABLE RESOURCES ###"]
        lines.append("\n[AVAILABLE AGENTS]")
        for agent in self.data.get("agents", []):
            lines.append(f"- ID: {agent['id']} | Name: {agent['name']}")
            lines.append(f"  Capabilities: {', '.join(agent.get('capabilities', []))}")

        lines.append("\n[AVAILABLE TOOLS]")
        for tool in self.data.get("tools", []):
            lines.append(f"- ID: {tool['id']} | Name: {tool['name']}")
            lines.append(f"  Use Case: {tool.get('use_case', 'General purpose')}")
        
        lines.append("\n[KNOWLEDGE BASES]")
        for kb in self.data.get("knowledge", []):
            lines.append(f"- ID: {kb['id']} | Name: {kb['name']}")

        return "\n".join(lines)

# --- INITIALIZATION ---

# 1. Initialize App ONCE
app = FastAPI(title="Orchestrator Agent")

# 2. Add Middleware immediately
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 3. Initialize Services
registry = AgentRegistry() 
task_library = TaskLibrary()
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

class TaskRequest(BaseModel):
    task: str

# --- ENDPOINTS ---

@app.post("/decompose")
def decompose_task(request: TaskRequest):
    if not request.task:
        raise HTTPException(status_code=400, detail="Task cannot be empty")

    final_plan = None
    meta_info = {}

    # --- STEP 1: Check Predefined Tasks ---
    print(f"Checking library for: {request.task}")
    predefined_plan = task_library.find_matching_plan(request.task)

    if predefined_plan:
        final_plan = predefined_plan
        meta_info = {
            "source": "tasks_library",
            "message": "Matched predefined template."
        }

    # --- STEP 2: Generate New Plan (If no predefined match) ---
    else:
        print("No match found. Initiating AI planning...")
        registry_context = registry.generate_context_string()

        system_prompt = f"""
        You are an Expert AI Orchestrator. 
        Break down the User Task into steps using the AVAILABLE RESOURCES.
        {registry_context}
        """

        user_prompt = f"""
        USER TASK: {request.task}
        OUTPUT JSON FORMAT:
        {{
          "plan_overview": "Summary",
          "steps": [
            {{ "step_number": 1, "description": "Action", "assigned_resource_id": "id", "reasoning": "why" }}
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
                temperature=0.2,
                response_format={"type": "json_object"}
            )
            
            final_plan = json.loads(response.choices[0].message.content)
            meta_info = {
                "source": "ai_generated",
                "message": "New plan generated."
            }
            
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"AI Generation failed: {str(e)}")

    # --- STEP 3: Auto-Execution Handoff ---
    execution_msg = "Handoff skipped (No plan)"
    
    if final_plan:
        try:
            # UDPATE: Use env variable for Docker Compose compatibility
            # Default fallback to localhost if running locally without Docker
            default_worker = "http://localhost:8001/execute"
            worker_url = os.environ.get("WORKER_URL", default_worker)
            
            print(f"Handing off to Worker at {worker_url}...")
            requests.post(worker_url, json=final_plan, timeout=2)
            execution_msg = "Successfully handed off to worker"
            
        except requests.exceptions.ConnectionError:
            print("Warning: Worker Agent (port 8001) is unreachable.")
            execution_msg = "Failed: Worker unreachable"
        except Exception as e:
            print(f"Warning: Handoff error: {e}")
            execution_msg = f"Failed: {str(e)}"

    # --- STEP 4: Final Response ---
    return {
        "meta": meta_info,
        "plan": final_plan,
        "execution_status": execution_msg
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("agent:app", host="0.0.0.0", port=8000, reload=True)