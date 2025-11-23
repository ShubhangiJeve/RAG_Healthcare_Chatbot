import warnings
import torch
from flask import Flask, request, jsonify, render_template
from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline
from sentence_transformers import SentenceTransformer
import faiss
import numpy as np
from pypdf import PdfReader
import os

# Suppress warnings
warnings.filterwarnings("ignore")

# Initialize Flask app
app = Flask(__name__, template_folder='.')

# Global variables for model and data
model = None
tokenizer = None
embedding_model = None
index = None
documents = []
device = None

def load_pdf(file_path):
    """Load and extract text from PDF file"""
    reader = PdfReader(file_path)
    text = ""
    for page in reader.pages:
        text += page.extract_text() + "\n"
    return text

def split_text(text, chunk_size=500, overlap=50):
    """Split text into chunks"""
    chunks = []
    for i in range(0, len(text), chunk_size - overlap):
        chunk = text[i:i + chunk_size]
        if chunk.strip():
            chunks.append(chunk)
    return chunks

def initialize_models():
    """Initialize all models"""
    global model, tokenizer, embedding_model, device
    
    # Check if CUDA is available
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Using device: {device}")
    
    # Initialize the language model (using a smaller model for 4GB VRAM)
    model_name = "microsoft/DialoGPT-small"  # Lightweight conversational model
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForCausalLM.from_pretrained(model_name).to(device)
    
    # Initialize the embedding model
    embedding_model = SentenceTransformer('all-MiniLM-L6-v2')

def create_vector_store(text_chunks):
    """Create FAISS vector store from text chunks"""
    global index, documents
    
    # Store documents
    documents = text_chunks
    
    # Generate embeddings
    embeddings = embedding_model.encode(text_chunks)
    
    # Create FAISS index
    dimension = embeddings.shape[1]
    index = faiss.IndexFlatL2(dimension)
    index.add(embeddings.astype(np.float32))
    
    print(f"Created vector store with {len(documents)} documents")

def initialize_rag_system():
    """Initialize the entire RAG system"""
    global index, documents
    
    try:
        # Initialize models
        initialize_models()
        
        # Load and process PDF
        pdf_path = "health.pdf"
        if os.path.exists(pdf_path):
            text = load_pdf(pdf_path)
            text_chunks = split_text(text)
            create_vector_store(text_chunks)
            print("RAG system initialized successfully")
        else:
            # Create dummy documents if PDF doesn't exist
            dummy_docs = [
                "This is a healthcare information document.",
                "For medical advice, always consult with a qualified healthcare professional.",
                "The information provided is for educational purposes only.",
                "Healthcare chatbot system is ready to answer your questions."
            ]
            create_vector_store(dummy_docs)
            print("RAG system initialized with dummy documents")
            
    except Exception as e:
        print(f"Error initializing RAG system: {e}")
        # Create minimal fallback
        dummy_docs = ["Fallback healthcare document content."]
        create_vector_store(dummy_docs)

def retrieve_documents(query, k=4):
    """Retrieve relevant documents for a query"""
    if index is None or not documents:
        return ["No documents available."]
    
    # Generate query embedding
    query_embedding = embedding_model.encode([query])
    
    # Search for similar documents
    distances, indices = index.search(query_embedding.astype(np.float32), min(k, len(documents)))
    
    # Retrieve documents
    retrieved_docs = [documents[i] for i in indices[0]]
    return retrieved_docs

def generate_response(query, context_docs):
    """Generate response using the language model"""
    if model is None or tokenizer is None:
        return "I don't know."
    
    # Combine context
    context = "\n".join(context_docs)
    
    # Create prompt
    prompt = f"""
You are a helpful healthcare assistant. Answer the question using only the provided context.
If you don't know the answer, say "I don't know."
Keep your answer concise and in a single line.

Context: {context}

Question: {query}

Answer:"""
    
    # Tokenize input
    inputs = tokenizer.encode(prompt, return_tensors="pt", truncation=True, max_length=512).to(device)
    
    # Generate response
    with torch.no_grad():
        outputs = model.generate(
            inputs, 
            max_length=inputs.shape[1] + 100,
            num_return_sequences=1,
            temperature=0.7,
            do_sample=True,
            pad_token_id=tokenizer.eos_token_id
        )
    
    # Decode response
    response = tokenizer.decode(outputs[0], skip_special_tokens=True)
    
    # Extract only the answer part
    if "Answer:" in response:
        answer = response.split("Answer:")[-1].strip()
        return answer
    else:
        return response.strip()

# Initialize the RAG system when the app starts
with app.app_context():
    initialize_rag_system()

# Landing page
@app.route("/")
def index_page():
    return render_template("index.html")

# Chatbot page
@app.route("/bot")
def bot_page():
    return render_template("bot.html")

# API endpoint for chat
@app.route('/api/chat', methods=['POST'])
def chat():
    try:
        data = request.json
        if not data:
            return jsonify({'result': "Invalid request format."}), 400
            
        question = data.get('question', '')
        if not question:
            return jsonify({'result': "Please ask a valid question."}), 400

        # Retrieve relevant documents
        context_docs = retrieve_documents(question)
        
        # Generate response
        answer = generate_response(question, context_docs)
        
        return jsonify({'result': answer})
    except Exception as e:
        return jsonify({'result': f"An error occurred: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True)