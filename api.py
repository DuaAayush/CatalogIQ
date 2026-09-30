"""
api.py  ->  Saare API routes ka logic.

handle(method, path, query, body) -> (status_code, dict)
Ye function HTTP se alag hai (HTTP wala kaam server.py karta hai),
isliye samajhna aur test karna aasan hai.
Har error is shape me aata hai:  {"error": "message"}
"""
import json
from urllib.parse import unquote

from config import CATEGORIES


class BadRequest(Exception):
    """Galat input. Message user ko error ki tarah dikhega (status 400)."""


# ------------------------------------------------------------ input check karne wale chhote functions
def read_json_body(body):
    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise BadRequest("body must be valid JSON")


def check_products(data):
    """POST /api/jobs ke products check karo. Theek hain to saaf list wapas do."""
    if not isinstance(data, dict) or not isinstance(data.get("products"), list):
        raise BadRequest('body must look like {"products": [...]}')
    if len(data["products"]) == 0:
        raise BadRequest("products list is empty")

    clean = []
    for number, product in enumerate(data["products"], start=1):
        if not isinstance(product, dict):
            raise BadRequest("product #%d must be an object" % number)
        sku, title, description = product.get("sku"), product.get("raw_title"), product.get("raw_description")
        if isinstance(sku, int) and not isinstance(sku, bool):
            sku = str(sku)
        if not isinstance(sku, str) or not sku.strip():
            raise BadRequest("product #%d has no sku" % number)
        if not isinstance(title, str) or not title.strip():
            raise BadRequest("product #%d has no raw_title" % number)
        if description is not None and not isinstance(description, str):
            raise BadRequest("product #%d: raw_description must be a string" % number)
        clean.append({"sku": sku.strip(), "raw_title": title, "raw_description": description})
    return clean


def check_changes(data):
    """PATCH ke changes check karo."""
    if not isinstance(data, dict):
        raise BadRequest("body must be a JSON object")
    changes = {}

    if "clean_title" in data:
        title = data["clean_title"]
        if not isinstance(title, str) or not title.strip():
            raise BadRequest("clean_title cannot be empty")
        changes["clean_title"] = title.strip()

    if "category" in data:
        if data["category"] not in CATEGORIES:
            raise BadRequest("category must be one of: " + ", ".join(CATEGORIES))
        changes["category"] = data["category"]

    if "tags" in data:
        tags = data["tags"]
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise BadRequest("tags must be a list of strings")
        clean_tags = []
        for tag in tags:
            tag = tag.strip().lower()
            if tag and tag not in clean_tags:
                clean_tags.append(tag)
        if len(clean_tags) > 5:
            raise BadRequest("at most 5 tags are allowed")
        changes["tags"] = clean_tags

    if not changes:
        raise BadRequest("send at least one of: clean_title, category, tags")
    return changes


def read_number(query, name, default):
    """?page=abc jaisa galat number ho to 400 do."""
    if name not in query:
        return default
    try:
        return int(query[name][0])
    except ValueError:
        raise BadRequest(name + " must be a number")


# ------------------------------------------------------------ main class
class Api:
    def __init__(self, pipeline, database, settings):
        self.pipeline = pipeline
        self.db = database
        self.settings = settings

    def handle(self, method, path, query, body):
        try:
            return self.route(method, path, query, body)
        except BadRequest as err:
            return 400, {"error": str(err)}
        except Exception as err:                       # koi bug ho to bhi JSON error do
            return 500, {"error": "internal server error: " + str(err)}

    def route(self, method, path, query, body):
        parts = [unquote(p) for p in path.strip("/").split("/")]     # /api/jobs/x -> ["api","jobs","x"]
        if len(parts) < 2 or parts[0] != "api":
            return 404, {"error": "not found"}
        name = parts[1]
        extra = parts[2:]

        if name == "health" and not extra:
            return self.only(method, "GET", self.health)
        if name == "metrics" and not extra:
            return self.only(method, "GET", self.metrics)
        if name == "jobs" and not extra:
            return self.only(method, "POST", lambda: self.create_job(body))
        if name == "jobs" and len(extra) == 1:
            return self.only(method, "GET", lambda: self.get_job(extra[0]))
        if name == "products" and not extra:
            return self.only(method, "GET", lambda: self.list_products(query))
        if name == "products" and len(extra) == 1:
            if method == "GET":
                return self.get_product(extra[0])
            if method == "PATCH":
                return self.update_product(extra[0], body)
            return 405, {"error": "method not allowed"}
        return 404, {"error": "not found"}

    @staticmethod
    def only(method, allowed, function):
        if method != allowed:
            return 405, {"error": "method not allowed"}
        return function()

    # ------------------------------------------------------------ routes
    def health(self):
        return 200, {
            "status": "ok",
            "llm_provider": self.settings.llm_provider,
            "llm_concurrency": self.settings.llm_concurrency,
        }

    def metrics(self):
        return 200, self.pipeline.get_metrics()

    def create_job(self, body):
        products = check_products(read_json_body(body))
        job = self.pipeline.submit_job(products)       # turant wapas aata hai
        return 202, job

    def get_job(self, job_id):
        job = self.db.get_job(job_id)
        if job is None:
            return 404, {"error": "job not found"}
        return 200, job

    def list_products(self, query):
        page = max(1, read_number(query, "page", 1))
        page_size = min(100, max(1, read_number(query, "page_size", 20)))
        category = query.get("category", [""])[0] or None
        q = query.get("q", [""])[0].strip() or None
        items, total = self.db.list_products(category, q, page, page_size)
        return 200, {"items": items, "page": page, "page_size": page_size, "total": total}

    def get_product(self, sku):
        product = self.db.get_product(sku)
        if product is None:
            return 404, {"error": "product not found"}
        return 200, product

    def update_product(self, sku, body):
        if self.db.get_product(sku) is None:
            return 404, {"error": "product not found"}
        changes = check_changes(read_json_body(body))
        return 200, self.db.update_product(sku, changes)
