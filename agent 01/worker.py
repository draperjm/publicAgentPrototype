import time
import os
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import List, Optional, Any

app = FastAPI(title="Worker Agent")

# --- Data Models (Mirroring the Plan Structure) ---

class StepValidation(BaseModel):
    criteria: Optional[str] = None
    critical_fail: Optional[bool] = False

class StepResources(BaseModel):
    agent_id: Optional[str] = None
    tool_id: Optional[str] = None
    knowledge_id: Optional[str] = None

class PlanStep(BaseModel):
    step_number: int
    description: str
    name: Optional[str] = None
    assigned_resource_id: Optional[str] = None # For AI generated plans
    required_resources: Optional[StepResources] = None # For Predefined plans
    validation: Optional[StepValidation] = None

class ExecutionRequest(BaseModel):
    plan_overview: str
    steps: List[PlanStep]

# --- Execution Logic ---

def execute_plan_process(plan: ExecutionRequest):
    """
    Background task to simulate long-running execution of the plan.
    """
    print(f"\n[WORKER] 🚀 STARTING EXECUTION: {plan.plan_overview}")
    print("="*60)

    for step in plan.steps:
        # 1. Identify Resource
        resource = step.assigned_resource_id
        if step.required_resources:
            # Prioritize specific agent or tool if detailed breakdown exists
            resource = step.required_resources.agent_id or step.required_resources.tool_id
        
        # 2. Simulate Processing
        print(f"\n[STEP {step.step_number}] Processing: {step.name or 'Action'}")
        print(f"   📝 Description: {step.description}")
        print(f"   🤖 Assigned Resource: {resource if resource else 'General Worker'}")
        
        # Simulate work duration
        time.sleep(2) 
        
        # 3. Validation/Output Simulation
        if step.validation:
            print(f"   ✅ Validation Passed: {step.validation.criteria}")
        else:
            print(f"   ✅ Step Complete")
            
    print("="*60)
    print("[WORKER] 🏁 ALL TASKS COMPLETED SUCCESSFULLY.\n")

@app.post("/execute")
async def execute_plan(request: ExecutionRequest, background_tasks: BackgroundTasks):
    """
    Receives a plan and schedules it for execution.
    Returns immediately to acknowledge receipt.
    """
    if not request.steps:
        raise HTTPException(status_code=400, detail="Plan contains no steps.")
    
    # Run execution in background so we don't block the HTTP response
    background_tasks.add_task(execute_plan_process, request)
    
    return {
        "status": "accepted",
        "message": f"Plan '{request.plan_overview}' has been queued for execution.",
        "step_count": len(request.steps)
    }

if __name__ == "__main__":
    import uvicorn
    # Worker runs on port 8001 to avoid conflict with Orchestrator (8000)
    uvicorn.run("worker:app", host="0.0.0.0", port=8001, reload=True)