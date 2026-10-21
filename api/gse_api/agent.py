"""The chat assistant: an LLM that may only use database tools.

Works with any OpenAI-compatible chat-completions API that supports tool calling
(default: Groq's free tier). The model plans which tools to call and writes the
explanation; every number in its reply must appear in a tool result, which is
checked before the reply is returned.
"""

from __future__ import annotations

import json
import logging
import os
import re

import httpx

from .data import SOURCE_NAMES, Database

log = logging.getLogger(__name__)

SYSTEM = """You are the assistant inside the Global Sustainability Explorer, a research tool.
You answer questions about sustainability data ONLY by calling the tools provided.

Rules:
- Never state a number that did not come from a tool result in this conversation. Do not use
  your own knowledge for figures. Years and counts of places may be repeated from tool results.
- First find the right indicator with search_indicators (check its unit and coverage), then
  resolve place names with resolve_geo, then fetch values (get_series, compare, trend, rank).
- If several indicators could fit, pick the closest and say which one you used.
- If the database has no matching data, say so plainly and suggest the closest indicator.
- Report the unit and the year of every value. Mention when values are flagged as estimates.
- Describe data; do not claim causes or make forecasts.
- Keep answers short: 2-5 sentences, plain language, no markdown tables.
- End by naming the indicator and source you used, for example:
  "Source: UN SDG Global Database, series EG_FEC_RNEW."
"""

TOOLS = [
    {"type": "function", "function": {
        "name": "search_indicators",
        "description": "Find indicators by meaning. Returns id, name, unit, source, years and number of places.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "What is measured, e.g. 'renewable energy share'"},
            "goal": {"type": "string", "description": "Optional SDG number to restrict to, e.g. '7'"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "indicator_info",
        "description": "Definition, unit, source, release, licence and available breakdowns of one indicator.",
        "parameters": {"type": "object", "properties": {"indicator_id": {"type": "string"}},
                       "required": ["indicator_id"]}}},
    {"type": "function", "function": {
        "name": "resolve_geo",
        "description": "Turn place names (countries, regions, 'world') into codes. Regions list member countries.",
        "parameters": {"type": "object", "properties": {
            "names": {"type": "array", "items": {"type": "string"}},
            "indicator_id": {"type": "string", "description": "Optional: picks the right 'world' code for this indicator's source"}},
            "required": ["names"]}}},
    {"type": "function", "function": {
        "name": "get_series",
        "description": "Values of one indicator for some places over a year range.",
        "parameters": {"type": "object", "properties": {
            "indicator_id": {"type": "string"}, "geos": {"type": "array", "items": {"type": "string"}},
            "year_from": {"type": "integer"}, "year_to": {"type": "integer"},
            "dims": {"type": "string", "description": "Breakdown such as 'Sex=FEMALE'; omit for the total"}},
            "required": ["indicator_id", "geos"]}}},
    {"type": "function", "function": {
        "name": "trend",
        "description": "First and last value in a year range for one place, with absolute, percent and compound annual change.",
        "parameters": {"type": "object", "properties": {
            "indicator_id": {"type": "string"}, "geo": {"type": "string"},
            "year_from": {"type": "integer"}, "year_to": {"type": "integer"}, "dims": {"type": "string"}},
            "required": ["indicator_id", "geo"]}}},
    {"type": "function", "function": {
        "name": "compare",
        "description": "Latest value at or before a year for several places, sorted high to low.",
        "parameters": {"type": "object", "properties": {
            "indicator_id": {"type": "string"}, "geos": {"type": "array", "items": {"type": "string"}},
            "year": {"type": "integer"}, "dims": {"type": "string"}},
            "required": ["indicator_id", "geos"]}}},
    {"type": "function", "function": {
        "name": "rank",
        "description": "Top or bottom countries for an indicator in one year (defaults to the latest well-covered year).",
        "parameters": {"type": "object", "properties": {
            "indicator_id": {"type": "string"}, "year": {"type": "integer"},
            "n": {"type": "integer"}, "order": {"type": "string", "enum": ["desc", "asc"]},
            "dims": {"type": "string"}},
            "required": ["indicator_id"]}}},
    {"type": "function", "function": {
        "name": "search_news",
        "description": "Recent sustainability news headlines matching a query (UN and specialist sources).",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                       "required": ["query"]}}},
]

NUM = re.compile(r"(?<![A-Za-z_\d.])(?:(?<![\d])[-−](?=\d))?\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> list[float]:
    out = []
    for m in NUM.finditer(text):
        s = m.group(0).replace(",", "").replace("−", "-")
        try:
            out.append(float(s))
        except ValueError:
            pass
    return out


def collect_numbers(obj, acc: set[float]):
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        acc.add(float(obj))
    elif isinstance(obj, str):
        acc.update(numbers_in(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            collect_numbers(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            collect_numbers(v, acc)


def supported(n: float, allowed: set[float]) -> bool:
    """A number is supported if it matches a tool value after rounding (or is trivially small)."""
    if abs(n) <= 20 and float(n).is_integer():
        return True  # small counts like "top 5", "3 countries", SDG numbers
    if n < 0 and supported(-n, allowed):
        return True  # a value written with a dash before it ("range 2010-2022", "−1.2")
    for a in allowed:
        for digits in (0, 1, 2, 3):
            if round(a, digits) == round(n, digits) and abs(a - n) <= max(0.5 * 10 ** -digits, abs(a) * 0.005):
                return True
        if abs(a) >= 1000:  # "2.7 billion", "37.5 million" style rounding
            for scale in (1e3, 1e6, 1e9, 1e12):
                if abs(a / scale - n) <= max(0.05, abs(n) * 0.01):
                    return True
    return False


def unsupported_numbers(answer: str, allowed: set[float], question: str) -> list[float]:
    allowed = set(allowed) | set(numbers_in(question))
    return [n for n in numbers_in(answer) if not supported(n, allowed)]


class Agent:
    def __init__(self, db: Database, base_url: str | None = None, api_key: str | None = None,
                 model: str | None = None, client: httpx.Client | None = None):
        self.db = db
        self.base_url = (base_url or os.environ.get("LLM_BASE_URL", "https://api.groq.com/openai/v1")).rstrip("/")
        self.api_key = api_key or os.environ.get("LLM_API_KEY", "")
        self.model = model or os.environ.get("LLM_MODEL", "llama-3.3-70b-versatile")
        self.client = client or httpx.Client(timeout=60)

    def _chat(self, messages):
        r = self.client.post(f"{self.base_url}/chat/completions",
                             headers={"Authorization": f"Bearer {self.api_key}"},
                             json={"model": self.model, "messages": messages, "tools": TOOLS,
                                   "tool_choice": "auto", "temperature": 0})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]

    def _run_tool(self, name: str, args: dict):
        fn = {
            "search_indicators": lambda a: self.db.search_indicators(a["query"], a.get("goal")),
            "indicator_info": lambda a: self.db.indicator_info(a["indicator_id"]),
            "resolve_geo": lambda a: self.db.resolve_geo(a["names"], a.get("indicator_id")),
            "get_series": lambda a: self.db.get_series(a["indicator_id"], a["geos"], a.get("year_from"),
                                                       a.get("year_to"), a.get("dims")),
            "trend": lambda a: self.db.trend(a["indicator_id"], a["geo"], a.get("year_from"),
                                             a.get("year_to"), a.get("dims")),
            "compare": lambda a: self.db.compare(a["indicator_id"], a["geos"], a.get("year"), a.get("dims")),
            "rank": lambda a: self.db.rank(a["indicator_id"], a.get("year"), a.get("n", 10),
                                           a.get("order", "desc"), a.get("dims")),
            "search_news": lambda a: self.db.search_news(a["query"]),
        }.get(name)
        if fn is None:
            return {"error": f"unknown tool {name}"}
        try:
            return fn(args)
        except Exception as e:  # noqa: BLE001 - report tool errors back to the model
            return {"error": f"{type(e).__name__}: {e}"}

    def ask(self, question: str, view: dict | None = None, max_rounds: int = 8) -> dict:
        context = ""
        if view and view.get("indicator"):
            context = (f"\nThe user is currently viewing indicator {view['indicator']} for places "
                       f"{view.get('geos')} and years {view.get('y0')}-{view.get('y1')}.")
        messages = [{"role": "system", "content": SYSTEM + context},
                    {"role": "user", "content": question}]
        allowed: set[float] = set()
        calls, used_ids, last_view = [], [], {}
        retried = False
        for _ in range(max_rounds):
            msg = self._chat(messages)
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                answer = (msg.get("content") or "").strip()
                bad = unsupported_numbers(answer, allowed, question)
                if bad and not retried:
                    retried = True
                    messages.append({"role": "assistant", "content": answer})
                    messages.append({"role": "user", "content":
                                     f"These numbers in your answer are not in any tool result: {bad}. "
                                     "Rewrite the answer using only numbers from tool results, or call "
                                     "more tools."})
                    continue
                if bad:
                    answer = ("I could not verify every figure in my draft answer against the database, "
                              "so I have left it out. The chart shows the values I retrieved.")
                return self._result(answer, calls, used_ids, last_view, verified=not bad)
            messages.append({"role": "assistant", "content": msg.get("content") or "",
                             "tool_calls": tool_calls})
            for tc in tool_calls:
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                except ValueError:
                    args = {}
                result = self._run_tool(name, args)
                collect_numbers(result, allowed)
                calls.append({"tool": name, "args": args})
                iid = args.get("indicator_id")
                if iid and iid in self.db.indicators and name != "indicator_info":
                    if iid not in used_ids:
                        used_ids.append(iid)
                    last_view = self._view_from(name, args, result)
                messages.append({"role": "tool", "tool_call_id": tc["id"],
                                 "content": json.dumps(result, default=str)[:12000]})
        return self._result("I could not finish looking that up. Please try a more specific question.",
                            calls, used_ids, last_view, verified=True)

    def _view_from(self, name, args, result) -> dict:
        view = {"indicator": args["indicator_id"], "dims": args.get("dims") or "", "mode": "chart"}
        if name == "rank" and isinstance(result, dict) and result.get("rows"):
            view.update(geos=[r["geo"] for r in result["rows"]], y0=result["year"], y1=result["year"])
        else:
            geos = args.get("geos") or ([args["geo"]] if args.get("geo") else [])
            view.update(geos=geos, y0=args.get("year_from"), y1=args.get("year_to") or args.get("year"))
        return view

    def _result(self, answer, calls, used_ids, view, verified) -> dict:
        cites = []
        for iid in used_ids:
            r = self.db.indicators[iid]
            cites.append({"indicator_id": iid, "source": SOURCE_NAMES.get(r["source"], r["source"]),
                          "name": r["name"], "code": r["code"], "release": r.get("release") or ""})
        return {"ok": True, "answer": answer, "citations": cites, "view": view,
                "indicator": view.get("indicator"), "tool_calls": calls, "verified": verified}
