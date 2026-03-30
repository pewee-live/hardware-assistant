import os
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv

def get_llm():
    """
    Initialize and return the DeepSeek LLM instance.
    """
    load_dotenv()
    
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY is not set in the environment variables.")
        
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    
    # DeepSeek is OpenAI-API compatible.
    llm = ChatOpenAI(
        model="deepseek-chat",
        api_key=api_key,
        base_url=base_url,
        max_tokens=2048,
        temperature=0.0
    )
    
    return llm
