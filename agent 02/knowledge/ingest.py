import os
import time
import chromadb
from chromadb.utils import embedding_functions
from pypdf import PdfReader

# Configuration
DOCS_FOLDER = "/app/documents"
CHROMA_HOST = os.environ.get("CHROMA_HOST", "localhost")
CHROMA_PORT = os.environ.get("CHROMA_PORT", "8002")

def get_chroma_client():
    print(f"🔌 Connecting to ChromaDB at {CHROMA_HOST}:{CHROMA_PORT}...")
    return chromadb.HttpClient(host=CHROMA_HOST, port=int(CHROMA_PORT))

def ingest_documents():
    client = get_chroma_client()
    
    emb_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="all-MiniLM-L6-v2"
    )

    collection = client.get_or_create_collection(
        name="knowledge_base",
        embedding_function=emb_fn
    )

    print("👀 Watching for PDFs in:", DOCS_FOLDER)
    
    # Track processed files to avoid re-indexing every loop
    # Format: "category/filename.pdf"
    processed_files = set()

    while True:
        # Walk through all folders
        for root, dirs, files in os.walk(DOCS_FOLDER):
            for file in files:
                if not file.endswith(".pdf"):
                    continue

                # Determine Category based on folder name
                # If in root (/app/documents), category is "general"
                # If in (/app/documents/finance), category is "finance"
                rel_dir = os.path.relpath(root, DOCS_FOLDER)
                category = "general" if rel_dir == "." else rel_dir
                
                # Unique ID for tracking
                file_id = f"{category}/{file}"

                if file_id in processed_files:
                    continue

                print(f"\npg Found new file: {file} (Tag: {category})")
                
                try:
                    file_path = os.path.join(root, file)
                    reader = PdfReader(file_path)
                    
                    text_chunks = []
                    ids = []
                    metadatas = []

                    for i, page in enumerate(reader.pages):
                        text = page.extract_text()
                        if text:
                            text_chunks.append(text)
                            # ID format: "finance/report.pdf_p1"
                            ids.append(f"{file_id}_p{i}")
                            # STORE THE TAG HERE
                            metadatas.append({
                                "source": file, 
                                "category": category, 
                                "page": i
                            })

                    if text_chunks:
                        print(f"   🧠 Indexing {len(text_chunks)} pages...")
                        collection.add(
                            documents=text_chunks,
                            ids=ids,
                            metadatas=metadatas
                        )
                        print(f"   ✅ Indexed with tag: '{category}'")
                    
                    processed_files.add(file_id)

                except Exception as e:
                    print(f"   ❌ Error processing {file}: {e}")

        time.sleep(10)

if __name__ == "__main__":
    time.sleep(5) 
    ingest_documents()