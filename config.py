"""
config.py  ->  Saari settings yahan se aati hain (environment variables se).

Koi variable set nahi hai to default value use hoti hai.
Aap chaho to project folder me ek ".env" file bana ke bhi settings de sakte ho.
"""
import os
from dataclasses import dataclass

# PDF me di gayi 8 categories (bilkul yehi naam use hone chahiye)
CATEGORIES = [
    "Groceries", "Beverages", "Personal Care", "Household",
    "Electronics", "Fashion", "Home & Kitchen", "Other",
]


def read_env_file(path=".env"):
    """Agar .env file hai to uske variables environment me daal do.
    (Jo variable pehle se set hai, usse change nahi karte.)"""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            value = value.split(" #")[0]          # "abc   # comment" -> "abc"
            os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Settings:
    llm_provider: str = "mock"        # mock / ollama / groq
    llm_concurrency: int = 5          # ek time par max itne LLM calls
    mock_latency_ms: int = 200        # mock call kitni der lega
    mock_failure_rate: float = 0.1    # mock call kitni baar fail hoga (0 se 1)
    retries: int = 3                  # fail hone par 3 baar aur try (total 4 attempts)
    backoff_ms: int = 200             # pehli retry se pehle kitna ruko (phir double)
    db_path: str = "catalogiq.db"     # SQLite file
    port: int = 8000
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2:3b"
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-20b"

    @property
    def worker_threads(self):
        # Kaam karne wale threads. LLM_CONCURRENCY se zyada rakhte hain,
        # kyunki kuch threads retry ke liye "so" rahe hote hain (backoff).
        # LLM calls ki limit alag se Semaphore se lagti hai (pipeline.py dekho).
        return max(20, self.llm_concurrency * 2)


def load_settings():
    read_env_file()
    settings = Settings(
        llm_provider=os.getenv("LLM_PROVIDER", "mock").strip().lower(),
        llm_concurrency=int(os.getenv("LLM_CONCURRENCY", "5")),
        mock_latency_ms=int(os.getenv("MOCK_LATENCY_MS", "200")),
        mock_failure_rate=float(os.getenv("MOCK_FAILURE_RATE", "0.1")),
        backoff_ms=int(os.getenv("BACKOFF_MS", "200")),
        db_path=os.getenv("DB_PATH", "catalogiq.db"),
        port=int(os.getenv("PORT", "8000")),
        ollama_url=os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/"),
        ollama_model=os.getenv("OLLAMA_MODEL", "llama3.2:3b"),
        groq_api_key=os.getenv("GROQ_API_KEY", ""),   # key kabhi code me nahi likhte!
        groq_model=os.getenv("GROQ_MODEL", "openai/gpt-oss-20b"),
    )
    if settings.llm_concurrency < 1:
        raise ValueError("LLM_CONCURRENCY kam se kam 1 hona chahiye")
    if not 0 <= settings.mock_failure_rate <= 1:
        raise ValueError("MOCK_FAILURE_RATE 0 se 1 ke beech hona chahiye")
    return settings
