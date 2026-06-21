import os
import json
import requests
import chromadb
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from openai import OpenAI

# 1. Load Environment Variables
load_dotenv()

# --- CONFIGURATION ---
CHROMA_HOST = "agent-vectordb"
CHROMA_PORT = 8000

# --- CLASS DEFINITIONS ---

class TaskLibrary:
    def __init__(self, tasks_path: str = "tasks.json"):
        self.tasks_path = tasks_path
        self.data = self._load_tasks()

    def _load_tasks(self):
        if not os.path.exists(self.tasks_path):
            return []
        try:
            with open(self.tasks_path, 'r') as f:
                raw_data = json.load(f)
                if isinstance(raw_data, dict) and "templates" in raw_data:
                    return raw_data["templates"]
                elif isinstance(raw_data, list):
                    return raw_data
                return []
        except:
            return []

    def find_matching_plan(self, user_task: str):
        normalized_input = user_task.lower().strip()
        for template in self.data:
            if template.get("name", "").lower() in normalized_input:
                return self._format_output(template)
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
        # Keep this simple or expand based on your registry.json
        return "Available Agents: Worker, Researcher"


# --- INITIALIZATION (CRITICAL: MUST BE BEFORE ENDPOINTS) ---

app = FastAPI(title="Orchestrator Agent")

# Add Middleware immediately
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Services
registry = AgentRegistry() 
task_library = TaskLibrary()
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# Data Models
class TaskRequest(BaseModel):
    task: str

class SearchRequest(BaseModel):
    query: str
    tag: str = "all"


# --- ENDPOINTS (MUST BE AFTER 'app' IS DEFINED) ---

@app.get("/knowledge/files")
def get_ingested_files():
    try:
        chroma_client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
        collection = chroma_client.get_collection("knowledge_base")
        
        data = collection.get(include=['metadatas'])
        
        if not data or not data['metadatas']:
             return {"files": []}

        unique_map = {}
        for meta in data['metadatas']:
            if meta and 'source' in meta:
                source = meta['source']
                category = meta.get('category', 'general')
                unique_map[source] = category

        file_list = [{"name": k, "tag": v} for k, v in unique_map.items()]
        
        return {"files": file_list, "count": len(file_list)}

    except Exception as e:
        print(f"Chroma Error: {e}")
        return {"files": [], "error": str(e)}


@app.post("/knowledge/search")
def search_knowledge(request: SearchRequest):
    """
    RAG Endpoint: Retrieves chunks -> Synthesizes answer via LLM -> Returns HTML.
    """
    try:
        chroma_client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
        
        # 1. Setup Embedding
        emb_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="all-MiniLM-L6-v2"
        )
        
        # 2. Get Collection
        try:
            collection = chroma_client.get_collection(
                name="knowledge_base", 
                embedding_function=emb_fn 
            )
        except Exception:
            return {"answer": "<b>Knowledge Base is empty.</b> Please ingest documents first.", "results": []}
        
        # 3. Build Filter & Query
        where_filter = {"category": request.tag} if request.tag != "all" else None
        
        print(f"Searching for: '{request.query}' with tag '{request.tag}'")
        results = collection.query(
            query_texts=[request.query],
            n_results=5, # Fetch more context for the LLM
            where=where_filter
        )
        
        # 4. Construct Context for LLM
        if not results.get('documents') or not results['documents'][0]:
            return {"answer": "No relevant information found in the documents.", "results": []}
            
        context_text = ""
        hits = []
        
        for i, doc in enumerate(results['documents'][0]):
            meta = results['metadatas'][0][i] or {}
            source_name = meta.get('source', 'Unknown')
            
            # Build context string for the AI
            context_text += f"---\nSource: {source_name} (Page {meta.get('page')})\nContent: {doc}\n"
            
            # Keep raw hits for the frontend "Citations" section
            hits.append({
                "content": doc[:150] + "...",
                "source": source_name,
                "page": meta.get('page', 0),
                "tag": meta.get('category', 'general') 
            })

        # 5. GENERATE ANSWER (The RAG Step)
        try:
            rag_system_prompt = """
            You are a helpful Knowledge Assistant. 
            1. Answer the user's question using ONLY the provided Context.
            2. If the answer is not in the context, say "I couldn't find that information."
            3. Format your response using HTML tags for readability:
               - Use <b> for key terms.
               - Use <ul><li> for lists.
               - Use <p> for paragraphs.
               - Do NOT use Markdown (no ** or #). Return pure HTML body content.
            """
            
            user_prompt = f"Context:\n{context_text}\n\nQuestion: {request.query}"
            
            ai_response = client.chat.completions.create(
                model=os.environ.get("LLM_MODEL", "gpt-3.5-turbo"),
                messages=[
                    {"role": "system", "content": rag_system_prompt}, 
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3 # Keep it factual
            )
            
            generated_answer = ai_response.choices[0].message.content
            
        except Exception as e:
            generated_answer = f"<p style='color:red'>AI Generation Failed: {str(e)}</p>"

        return {"answer": generated_answer, "results": hits}

    except Exception as e:
        print(f"Search Crash: {e}") 
        return {"answer": "System Error during search.", "results": [], "error": str(e)}

    except Exception as e:
        print(f"Search Crash: {e}") # Print to docker logs for debugging
        # Return an empty list so the frontend doesn't break
        return {"results": [], "error": str(e)}

class DeleteFileRequest(BaseModel):
    filename: str

@app.delete("/knowledge/files")
def delete_file(request: DeleteFileRequest):
    """Deletes all chunks associated with a specific filename from ChromaDB."""
    try:
        chroma_client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
        collection = chroma_client.get_collection("knowledge_base")
        
        # ChromaDB delete syntax
        # We delete where metadata 'source' matches the filename
        collection.delete(
            where={"source": request.filename}
        )
        
        print(f"Deleted file: {request.filename}")
        return {"status": "success", "message": f"Forgot knowledge: {request.filename}"}

    except Exception as e:
        print(f"Delete Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/decompose")
def decompose_task(request: TaskRequest):
    if not request.task:
        raise HTTPException(status_code=400, detail="Task cannot be empty")

    final_plan = None
    meta_info = {}

    # 1. Check Library
    predefined_plan = task_library.find_matching_plan(request.task)
    if predefined_plan:
        final_plan = predefined_plan
        meta_info = {"source": "tasks_library", "message": "Matched predefined template."}

    # 2. AI Generation
    else:
        try:
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
            
            response = client.chat.completions.create(
                model=os.environ.get("LLM_MODEL", "gpt-3.5-turbo"),
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                temperature=0.2,
                response_format={"type": "json_object"}
            )
            final_plan = json.loads(response.choices[0].message.content)
            meta_info = {"source": "ai_generated", "message": "New plan generated."}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # 3. Handoff
    execution_msg = "Handoff skipped"
    if final_plan:
        try:
            worker_url = "http://host.docker.internal:8001/execute"
            requests.post(worker_url, json=final_plan, timeout=1)
            execution_msg = "Handed off to worker"
        except:
            execution_msg = "Worker unreachable"

    return {"meta": meta_info, "plan": final_plan, "execution_status": execution_msg}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("agent:app", host="0.0.0.0", port=8000, reload=True)