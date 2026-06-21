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

# --- HELPER CLASSES ---
class TaskLibrary:
    def __init__(self, tasks_path: str = "tasks.json"):
        self.tasks_path = tasks_path
        self.data = self._load_tasks()
        self.digital_workers = self._load_workers()

    def _load_tasks(self):
        if not os.path.exists(self.tasks_path): return []
        try:
            with open(self.tasks_path, 'r', encoding='utf-8') as f:
                raw = json.load(f)
                return raw.get("templates", raw) if isinstance(raw, dict) else raw
        except: return []

    def _load_workers(self):
        if not os.path.exists(self.tasks_path): return []
        try:
            with open(self.tasks_path, 'r', encoding='utf-8') as f:
                raw = json.load(f)
                return raw.get("digital_workers", []) if isinstance(raw, dict) else []
        except: return []

    def find_matching_plan(self, user_task: str):
        normalized_input = user_task.lower().strip()
        for t in self.data:
            if t.get("name", "").lower() in normalized_input: return self._format(t)
            triggers = t.get("triggers", [])
            if isinstance(triggers, list):
                if any(trig.lower() in normalized_input for trig in triggers): return self._format(t)
        return None

    def _format(self, t):
        return {"plan_overview": t.get("description", "Predefined"), "steps": t.get("steps", [])}

class AgentRegistry:
    def __init__(self, path: str = "registry.json"):
        self.path = path
        self.data = self._load()
    def _load(self):
        try:
            with open(self.path, 'r') as f: return json.load(f)
        except: return {"agents":[]}
    def generate_context_string(self) -> str:
        return "Available Agents: " + ", ".join([a.get('name','Unk') for a in self.data.get("agents", [])])

# --- INITIALIZATION ---
app = FastAPI(title="Responder Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

registry = AgentRegistry()
task_library = TaskLibrary()
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

class TaskRequest(BaseModel):
    task: str

@app.get("/tasks")
def list_tasks():
    """Return the list of available task templates and digital workers for the UI."""
    return {
        "templates": [
            {
                "template_id":        t.get("template_id"),
                "worker_id":          t.get("worker_id"),
                "name":               t.get("name"),
                "description":        t.get("description", ""),
                "steps":              t.get("steps", []),
                "estimated_duration": t.get("estimated_duration"),
            }
            for t in task_library.data
        ],
        "digital_workers": [
            {"worker_id": w.get("worker_id"), "name": w.get("name"), "description": w.get("description", "")}
            for w in task_library.digital_workers
        ]
    }

@app.post("/decompose")
def decompose_task(request: TaskRequest):
    if not request.task:
        raise HTTPException(status_code=400, detail="Task cannot be empty")

    final_plan = None
    meta_info = {}

    # 1. Library Check
    predefined_plan = task_library.find_matching_plan(request.task)
    if predefined_plan:
        final_plan = predefined_plan
        meta_info = {"source": "tasks_library", "message": "Matched predefined template."}
    else:
        # 2. AI Generation
        system_prompt = (
            f"You are an Expert AI Decomposer. Break down the task.\n{registry.generate_context_string()}\n"
            f"OUTPUT JSON: {{ 'plan_overview': '', 'steps': [{{ 'step_number': 1, 'description': '...', 'name': 'Action Name', 'assigned_resource_id': 'agent-id' }}] }}"
        )
        try:
            response = client.chat.completions.create(
                model=os.environ.get("LLM_MODEL", "gpt-3.5-turbo"),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"TASK: {request.task}"}
                ],
                temperature=0.2,
                response_format={"type": "json_object"}
            )
            final_plan = json.loads(response.choices[0].message.content)
            meta_info = {"source": "ai_generated", "message": "New plan generated."}
        except Exception as e:
            print(f"AI Error: {e}")
            raise HTTPException(status_code=500, detail=f"AI Generation failed: {str(e)}")

    # 3. Handoff to Orchestrator (Initialize State)
    execution_data = {}
    execution_msg = "Handoff skipped (No plan)"

    if final_plan and final_plan.get("steps"):
        try:
            orch_url = os.environ.get("ORCHESTRATOR_URL", "http://orchestrator:8001/execute")
            print(f"Initializing plan with Orchestrator at {orch_url}...")

            resp = requests.post(orch_url, json=final_plan, timeout=5)

            if resp.status_code == 200:
                execution_data = resp.json()
                execution_msg = "Plan initialized. Ready for User Execution."
            else:
                execution_msg = f"Orchestrator Error: {resp.status_code}"
                print(f"Orchestrator failed: {resp.text}")

        except Exception as e:
            execution_msg = f"Failed to contact Orchestrator: {str(e)}"
            print(f"Handoff exception: {e}")

    return {
        "meta": meta_info,
        "plan": final_plan,
        "execution_status": execution_msg,
        "orchestrator_data": execution_data
    }
