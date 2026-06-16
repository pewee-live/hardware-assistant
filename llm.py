import os
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv

def get_llm():
    """
    Initialize and return the LLM instance.
    Supports any OpenAI-compatible API.
    """
    load_dotenv()
    
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY or DEEPSEEK_API_KEY is not set in the environment variables.")
        
    base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("DEEPSEEK_BASE_URL")
    model = os.getenv("OPENAI_MODEL") or os.getenv("LLM_MODEL", "deepseek-chat")
    
    # Default to DeepSeek base URL if using a DeepSeek model and no base URL is specified
    if not base_url and "deepseek" in model:
        base_url = "https://api.deepseek.com/v1"
    
    kwargs = {
        "model": model,
        "api_key": api_key,
        "max_tokens": 2048,
        "temperature": 0.0
    }
    
    if base_url:
        kwargs["base_url"] = base_url
        
    llm = ChatOpenAI(**kwargs)
    
    return llm
