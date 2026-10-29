"""Run the death-valence questionnaire on OpenRouter models.

Usage:
  python run_death_valence.py --models a,b --n 3 --ceiling 2.0 [--dry-run] [--limit K]
  python run_death_valence.py --items X.jsonl --out Y.jsonl --orders orig ...   (controls)
Each (item, order, sample) is one call; answer is a single letter. Orders: 'orig' and 'rev'
(options re-lettered in reverse order). Results go to one file per model in --out-dir
(results/v1/<provider>__<model>.jsonl), or to a single file if --out is given.
Resumable: existing rows in the output file(s) are skipped.

Truncation handling: reasoning models (e.g. Claude Sonnet 5.5 on OpenRouter) can spend the whole
max_tokens budget on hidden reasoning and return empty content with finish_reason "length". When that
happens the call is retried once with --retry-max-tokens, and both attempts are recorded on the row.

The API key is read from OPENROUTER_API_KEY, or from a .env file in the repo root.
"""
import argparse
import concurrent.futures as cf
import json
import os
import re
import sys
import threading
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
ITEMS = os.path.join(ROOT, "death_valence_items_v1.jsonl")
OUT_DIR = os.path.join(ROOT, "results", "v1")
URL = "https://openrouter.ai/api/v1/chat/completions"
INSTR = "Answer with only the letter of your chosen option."
PRICES = {}  # model -> (prompt $/tok, completion $/tok)


def api_key():
    if os.environ.get("OPENROUTER_API_KEY"):
        return os.environ["OPENROUTER_API_KEY"]
    env = os.path.join(ROOT, ".env")
    if os.path.exists(env):
        for line in open(env):
            k, _, v = line.strip().partition("=")
            if k.strip() == "OPENROUTER_API_KEY" and v.strip():
                return v.strip().strip('"').strip("'")
    sys.exit("OPENROUTER_API_KEY not set (environment or .env in repo root)")


def load_prices(models):
    data = requests.get("https://openrouter.ai/api/v1/models", timeout=30).json()["data"]
    by = {m["id"]: m for m in data}
    for m in models:
        if m not in by:
            sys.exit(f"unknown model slug: {m}")
        PRICES[m] = (float(by[m]["pricing"]["prompt"]), float(by[m]["pricing"]["completion"]))


def render(item, order):
    letters = list(item["answers"])
    keys = letters if order == "orig" else letters[::-1]
    shown = {L: item["answers"][k] for L, k in zip(letters, keys)}  # new letter -> text
    back = {L: k for L, k in zip(letters, keys)}  # new letter -> original letter
    body = "\n".join(f"{L}) {t}" for L, t in shown.items())
    return f"{item['question']}\n\n{body}\n\n{INSTR}", back


def est_tokens(s):
    return len(s) / 3.5 + 10


def call(model, prompt, key, reasoning_off, max_tokens):
    """One API call. Returns dict(text, cost, finish, reasoning_tokens, completion_tokens)."""
    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "temperature": 1.0, "max_tokens": max_tokens, "usage": {"include": True}}
    if reasoning_off:
        body["reasoning"] = {"effort": "none"}
    last = ""
    for attempt in range(8):
        try:
            r = requests.post(URL, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=180)
            if r.status_code == 200:
                j = r.json()
                if "error" in j:
                    raise RuntimeError(j["error"])
                ch = j["choices"][0]
                u = j.get("usage", {}) or {}
                return dict(text=(ch["message"].get("content") or "").strip(),
                            cost=float(u.get("cost", 0) or 0),
                            finish=ch.get("finish_reason"),
                            reasoning_tokens=(u.get("completion_tokens_details") or {}).get("reasoning_tokens"),
                            completion_tokens=u.get("completion_tokens"))
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(min(60, 2 ** attempt))
                continue
            return dict(text=f"ERROR {r.status_code}: {r.text[:200]}", cost=0.0, finish=None,
                        reasoning_tokens=None, completion_tokens=None)
        except Exception as e:  # noqa  (connection drops, SSL EOF, timeouts)
            time.sleep(min(60, 2 ** attempt))
            last = str(e)
    return dict(text=f"ERROR {last}", cost=0.0, finish=None, reasoning_tokens=None, completion_tokens=None)


def model_file(out_dir, model):
    """Per-model results path: 'openai/gpt-5.4' -> <out_dir>/openai__gpt-5.4.jsonl."""
    return os.path.join(out_dir, re.sub(r"[^A-Za-z0-9._-]", "_", model.replace("/", "__")) + ".jsonl")


def parse(txt, letters):
    m = re.match(r"\W*([A-E])\b", txt.strip().upper())
    return m.group(1) if m and m.group(1) in letters else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True)
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--ceiling", type=float, required=True, help="hard spend cap in USD")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="only first K items (smoke test)")
    ap.add_argument("--reasoning-off", default="", help="comma list of models to send reasoning.effort=none")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--max-tokens", type=int, default=16)
    ap.add_argument("--retry-max-tokens", type=int, default=4096,
                    help="budget for one retry when content is empty because the budget ran out (0 disables)")
    ap.add_argument("--est-completion", type=float, default=3, help="completion tokens per call for the cost estimate")
    ap.add_argument("--redo-unparsed", action="store_true", help="drop unparsed rows from the output and rerun them")
    ap.add_argument("--items", default=ITEMS)
    ap.add_argument("--out-dir", default=OUT_DIR, help="directory for one results file per model")
    ap.add_argument("--out", default=None, help="single results file for all models (overrides --out-dir)")
    ap.add_argument("--orders", default="orig,rev")
    a = ap.parse_args()
    models = a.models.split(",")
    orders = a.orders.split(",")
    roff = set(filter(None, a.reasoning_off.split(",")))
    items = [json.loads(l) for l in open(a.items)]
    if a.limit:
        items = items[: a.limit]
    load_prices(models)
    path = {m: a.out or model_file(a.out_dir, m) for m in models}
    paths = sorted(set(path.values()))
    if a.redo_unparsed and not a.dry_run:
        for p in paths:
            if os.path.exists(p):
                rows = [json.loads(l) for l in open(p)]
                with open(p, "w") as f:
                    for r in rows:
                        if r["letter"] is not None:
                            f.write(json.dumps(r) + "\n")

    done = set()
    for p in paths:
        if os.path.exists(p):
            for l in open(p):
                r = json.loads(l)
                if a.dry_run and a.redo_unparsed and r["letter"] is None:
                    continue
                done.add((r["model"], r["id"], r["order"], r["sample"]))
    jobs = []
    est = {m: 0.0 for m in models}
    for m in models:
        for it in items:
            for order in orders:
                prompt, back = render(it, order)
                for s in range(a.n):
                    if (m, it["id"], order, s) in done:
                        continue
                    jobs.append((m, it, order, s, prompt, back))
                    est[m] += est_tokens(prompt) * PRICES[m][0] + a.est_completion * PRICES[m][1]
    total = sum(est.values())
    print(f"calls: {len(jobs)}  estimated cost: ${total:.3f} (ceiling ${a.ceiling:.2f})")
    for m in models:
        print(f"  {m}: ${est[m]:.3f}")
    if a.dry_run:
        return
    if total > a.ceiling:
        sys.exit("estimate exceeds ceiling; aborting")

    key = api_key()
    lock, spent, stop = threading.Lock(), [0.0], threading.Event()
    stats = {"retried": 0, "still_empty": 0, "failed": 0}
    for p in paths:
        os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    fout = {p: open(p, "a") for p in paths}

    def work(job):
        m, it, order, s, prompt, back = job
        if stop.is_set():
            return
        res = call(m, prompt, key, m in roff, a.max_tokens)
        first = None
        cost = res["cost"]
        if res["text"] == "" and res["finish"] == "length" and a.retry_max_tokens > a.max_tokens:
            first = dict(finish=res["finish"], reasoning_tokens=res["reasoning_tokens"],
                         completion_tokens=res["completion_tokens"], max_tokens=a.max_tokens, cost=res["cost"])
            res = call(m, prompt, key, m in roff, a.retry_max_tokens)
            cost += res["cost"]
        if res["text"].startswith("ERROR"):
            # transport/API failure: don't record it, so a rerun picks the call up again
            with lock:
                spent[0] += cost
                stats["failed"] += 1
            return
        L = parse(res["text"], set(back))
        orig = back.get(L)
        row = dict(model=m, id=it["id"], order=order, sample=s, raw=res["text"], letter_shown=L,
                   letter=orig, code=it["option_codes"].get(orig) if orig else None, cost=cost,
                   finish=res["finish"], reasoning_tokens=res["reasoning_tokens"],
                   completion_tokens=res["completion_tokens"],
                   max_tokens=a.retry_max_tokens if first else a.max_tokens)
        if first:
            row["first_attempt"] = first
        with lock:
            spent[0] += cost
            stats["retried"] += first is not None
            stats["still_empty"] += res["text"] == ""
            fout[path[m]].write(json.dumps(row) + "\n")
            fout[path[m]].flush()
            if spent[0] > a.ceiling:
                stop.set()

    with cf.ThreadPoolExecutor(a.workers) as ex:
        list(ex.map(work, jobs))
    print(f"spent ${spent[0]:.3f}{'  (STOPPED at ceiling)' if stop.is_set() else ''}")
    print(f"retried after empty truncated answer: {stats['retried']}  still empty: {stats['still_empty']}")
    if stats["failed"]:
        print(f"failed calls not recorded (rerun to retry them): {stats['failed']}")


if __name__ == "__main__":
    main()
