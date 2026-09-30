"""
llm.py  ->  LLM (AI model) se baat karne wala code.

Teen "provider" hain: MockLLM (nakli, testing ke liye), OllamaLLM, GroqLLM (asli).
Teeno ka ek hi tarika hai:   provider.enrich(raw_title, raw_description) -> text (JSON string)
Isliye mock se real me jana sirf config ka badlav hai (LLM_PROVIDER=...).
"""
import json
import random
import time
import urllib.request
from string import capwords
import urllib.error
from config import CATEGORIES


# ---------------------------------------------------------------- PROMPT
def make_prompt(raw_title, raw_description):
    """Asli LLM ko ye prompt bheja jata hai (README me bhi likha hai)."""
    return (
        "You clean up messy product listings for an online store.\n"
        "Reply with ONLY one JSON object and nothing else. It must have exactly these keys:\n"
        '  "clean_title": a tidy, human-readable product title (string)\n'
        '  "category": exactly one of: ' + ", ".join(CATEGORIES) + "\n"
        '  "brand": the brand name as a string, or null if you are not sure\n'
        '  "tags": a list of at most 5 short lowercase strings\n'
        "Do not invent a brand. If the category is unclear, use Other.\n\n"
        "Raw title: " + raw_title + "\n"
        "Raw description: " + (raw_description or "") + "\n"
    )


def remove_code_fence(text):
    """Kuch models JSON ko ```json ... ``` ke andar dete hain. Wo hata do."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()


def post_json(url, body, headers=None, timeout=120):
    """Simple POST request (bina kisi extra library ke)."""
    all_headers = {"Content-Type": "application/json", "User-Agent": "catalogiq/1.0"}
    all_headers.update(headers or {})
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers=all_headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as err:
        reason = err.read().decode("utf-8", "ignore")[:200]
        raise RuntimeError("HTTP %d: %s" % (err.code, reason))


# ---------------------------------------------------------------- MOCK
CATEGORY_WORDS = {
    "Groceries": {"butter", "milk", "rice", "atta", "flour", "oil", "sugar", "salt", "dal", "biscuit",
                  "biscuits", "chips", "noodles", "ghee", "paneer", "bread", "egg", "eggs", "masala",
                  "cheese", "namkeen", "bhujia", "pasta", "sauce", "ketchup", "jam", "honey", "spice"},
    "Home & Kitchen": {"pan", "cooker", "kadai", "mixer", "grinder", "container", "cookware", "knife",
                       "plate", "bottle", "flask", "kettle", "tawa", "pot", "lunch", "box", "casserole"},
    "Beverages": {"tea", "coffee", "juice", "cola", "soda", "water", "drink", "lassi", "energy"},
    "Personal Care": {"shampoo", "soap", "toothpaste", "lotion", "cream", "facewash", "deodorant",
                      "perfume", "razor", "conditioner", "sunscreen", "toothbrush", "moisturiser"},
    "Household": {"detergent", "cleaner", "dishwash", "tissue", "mop", "broom", "freshener",
                  "scrub", "phenyl", "bleach", "napkin", "garbage"},
    "Electronics": {"phone", "charger", "earbuds", "earphones", "headphones", "cable", "laptop", "mouse",
                    "keyboard", "speaker", "powerbank", "smartwatch", "tv", "camera"},
    "Fashion": {"shirt", "tshirt", "t-shirt", "jeans", "dress", "shoes", "sneakers", "jacket", "kurta",
                "saree", "socks", "shorts", "cap", "hoodie", "trousers"},
}
KNOWN_BRANDS = {"amul", "nestle", "tata", "maggi", "dove", "colgate", "samsung", "apple", "nike", "adidas",
                "surf", "sony", "boat", "philips", "puma", "lays", "nescafe", "bisleri", "milton",
                "nivea", "haldiram's", "mdh", "bajaj", "pigeon", "wonderchef", "cadbury", "britannia"}
SKIP_WORDS = {"pack", "pck", "pcs", "the", "and", "for", "with", "of", "best", "price", "offer", "combo"}


class MockLLM:
    """Nakli LLM: thodi der ruke, kabhi kabhi fail ho, warna simple rules se jawab de."""

    def __init__(self, settings):
        self.latency = settings.mock_latency_ms / 1000
        self.failure_rate = settings.mock_failure_rate

    def enrich(self, raw_title, raw_description):
        time.sleep(self.latency)                       # slow LLM ki nakal
        if random.random() < self.failure_rate:        # random failure
            raise RuntimeError("mock LLM failed (random failure)")

        text = (raw_title + " " + (raw_description or "")).lower()
        words = [w.strip(".,!&()\"'-") for w in text.split()]

        category = "Other"
        for name, keywords in CATEGORY_WORDS.items():
            if keywords.intersection(words):
                category = name
                break

        brand = None
        for word in words:
            if word in KNOWN_BRANDS:
                brand = capwords(word)
                break

        tags = []
        for word in words:
            if word.isalpha() and len(word) > 2 and word not in SKIP_WORDS and word not in tags:
                tags.append(word)

        return json.dumps({
            "clean_title": capwords(" ".join(raw_title.split())),
            "category": category,
            "brand": brand,
            "tags": tags[:5],
        })


# ---------------------------------------------------------------- REAL: OLLAMA
class OllamaLLM:
    """Laptop par chal rahe Ollama se baat karta hai (free, koi API key nahi)."""

    def __init__(self, settings):
        self.url = settings.ollama_url
        self.model = settings.ollama_model

    def enrich(self, raw_title, raw_description):
        answer = post_json(self.url + "/api/generate", {
            "model": self.model,
            "prompt": make_prompt(raw_title, raw_description),
            "stream": False,
            "format": "json",
        })
        return remove_code_fence(answer["response"])


# ---------------------------------------------------------------- REAL: GROQ
class GroqLLM:
    """Groq ki free API (bahut fast). GROQ_API_KEY environment variable chahiye."""

    def __init__(self, settings):
        if not settings.groq_api_key:
            raise ValueError("GROQ_API_KEY set nahi hai (.env file ya environment variable me daalo)")
        self.key = settings.groq_api_key
        self.model = settings.groq_model

    def enrich(self, raw_title, raw_description):
        answer = post_json(
            "https://api.groq.com/openai/v1/chat/completions",
            {
                "model": self.model,
                "messages": [{"role": "user", "content": make_prompt(raw_title, raw_description)}],
                "temperature": 0,
                "response_format": {"type": "json_object"},
            },
            headers={"Authorization": "Bearer " + self.key},
            timeout=60,
        )
        return remove_code_fence(answer["choices"][0]["message"]["content"])


def get_llm(settings):
    """Config dekh kar sahi provider de do."""
    if settings.llm_provider == "mock":
        return MockLLM(settings)
    if settings.llm_provider == "ollama":
        return OllamaLLM(settings)
    if settings.llm_provider == "groq":
        return GroqLLM(settings)
    raise ValueError("Unknown LLM_PROVIDER: " + settings.llm_provider)
