# agent.py
import os
from dotenv import load_dotenv  # <--- Add this
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from openai import OpenAI

load_dotenv()  # <--- This loads the .env file into os.environ

# Initialize the App
app = FastAPI(title="Task Decomposer Agent")

# Initialize LLM Client
# This expects OPENAI_API_KEY to be set in the environment
client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY"), 
    # base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
)

# Define the expected input format
class TaskRequest(BaseModel):
    task: str

@app.post("/decompose")
def decompose_task(request: TaskRequest):
    """
    Receives a task string, sends it to the LLM, and returns the breakdown.
    """
    if not request.task:
        raise HTTPException(status_code=400, detail="Task string cannot be empty")

    prompt = f"""
    You are an expert project manager. 
    Break down the following task into a numbered list of clear, actionable steps.
    
    TASK: {request.task}
    """

    try:
        response = client.chat.completions.create(
            model=os.environ.get("LLM_MODEL", "gpt-3.5-turbo"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7
        )
        
        # Extract the content
        result_text = response.choices[0].message.content
        return {"original_task": request.task, "steps": result_text}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)