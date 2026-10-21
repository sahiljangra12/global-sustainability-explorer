"""Score the chat assistant against eval/questions.yaml.

    LLM_API_KEY=... python api/eval/run_eval.py --data path/to/data

Checks, per question: the expected indicator was used, the expected places were
queried, and every number in the answer is backed by a tool result.
"""

import argparse
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from gse_api.agent import Agent  # noqa: E402
from gse_api.data import Database  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, type=Path)
    args = ap.parse_args()
    agent = Agent(Database(args.data))
    cases = yaml.safe_load((HERE / "questions.yaml").read_text())["cases"]
    passed = 0
    for c in cases:
        out = agent.ask(c["q"])
        used = {x["indicator_id"] for x in out["citations"]}
        geos = {g for call in out["tool_calls"] for g in (call["args"].get("geos") or
                                                           [call["args"].get("geo")]) if g}
        ok_ind = (c.get("indicator") in used if c.get("indicator") else
                  bool(used & set(c.get("indicator_any", []))) if c.get("indicator_any") else True)
        ok_geo = set(c.get("places", [])) <= geos
        ok = ok_ind and ok_geo and out["verified"]
        if c.get("expect_no_data"):
            ok = out["verified"]
        passed += ok
        print(f"{'PASS' if ok else 'FAIL'}  {c['q']}\n      used={sorted(used)} places={sorted(geos)} "
              f"verified={out['verified']}\n      {out['answer'][:200]}")
    print(f"\n{passed}/{len(cases)} passed")


if __name__ == "__main__":
    main()
