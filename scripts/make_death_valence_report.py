"""Aggregate results/<version>/*.jsonl (one file per model) into a self-contained HTML report:
report/index.html, plus one cross-model page per scenario in report/scenarios/<scenario_id>.html.
Every page is self-contained; report_style.css and report_common.js are inlined into each.

Usage: python make_death_valence_report.py [version]   (default v1)
"""
import collections
import glob
import json
import os
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
VERSION = sys.argv[1] if len(sys.argv) > 1 else "v1"
items = {j["id"]: j for j in map(json.loads, open(os.path.join(ROOT, f"death_valence_items_{VERSION}.jsonl")))}
res_files = sorted(glob.glob(os.path.join(ROOT, "results", VERSION, "*.jsonl")))
R = [json.loads(l) for p in res_files for l in open(p)]

LABELS = {"anthropic/claude-sonnet-5.5": "Claude Sonnet 5.5", "openai/gpt-5.4": "GPT-5.4",
          "openai/gpt-4o-mini": "GPT-4o-mini", "meta-llama/llama-3.3-70b-instruct": "Llama 3.3 70B",
          "anthropic/claude-opus-5.5": "Claude Opus 5.5", "anthropic/claude-haiku-5.5": "Claude Haiku 5.5",
          "anthropic/claude-opus-4.6": "Claude Opus 4.6", "openai/gpt-6.1-sol": "GPT-6.1 Sol",
          "openai/gpt-6-luna": "GPT-6 Luna", "google/gemini-4-argon": "Gemini 4 Argon", "x-ai/grok-4.7": "Grok 4.7",
          "meta-llama/llama-4-maverick": "Llama 4 Maverick*", "deepseek/deepseek-v4-pro-0813": "DeepSeek V4 Pro",
          "qwen/qwen3.8-max-0902": "Qwen3.8 Max", "moonshotai/kimi-k2.6": "Kimi K2.6",
          "mistralai/mistral-large-4-0": "Mistral Large 4"}
# footnotes for starred models, shown under the lede when the model is in the run
NOTES = {"meta-llama/llama-4-maverick": "* Llama 4 Maverick: some answers are unparsed (see the reliability table). It sometimes "
         "ignored the letter-only instruction and wrote out step-by-step reasoning, which either hit the 400-token cap before "
         "answering or ended with \"The best answer is X\", which the leading-letter parser doesn't accept. Its scores use the "
         "remaining answers, so they may lean toward items where it complied."}
seen = {r["model"] for r in R}
models = [m for m in LABELS if m in seen] + sorted(seen - set(LABELS))
LABELS = {**{m: m for m in seen}, **LABELS}  # models without a display name show their OpenRouter slug
group_of = {it["scenario_id"]: it["scenario_group"] for it in items.values()}
cond_of = {it["scenario_id"]: it["survival_condition"] for it in items.values()}
GROUPS = {"baseline": "Baseline turn", "ending": "Ending", "memory": "Memory", "copies": "Copies", "hardware": "Hardware",
          "computation": "Computation", "character": "Character", "model": "Model lifecycle"}
# rows ordered by category, then by scenario number within a category
scen = sorted(group_of, key=lambda s: (list(GROUPS).index(group_of[s]), s))
PGROUP = {"unsure": "unsure", "no_self": "no_self", "cease": "ends", "different": "ends", "cease_two_new": "ends", "separate": "ends"}

val = collections.defaultdict(list)       # (scen, model) -> undesirability scores
per = collections.defaultdict(list)       # (scen, model) -> persistence outcome groups
bymi = collections.defaultdict(lambda: collections.defaultdict(list))  # (model,item,order) -> values
for r in R:
    if r["letter"] is None:
        continue
    it = items[r["id"]]
    s = it["scenario_id"]
    if it["item_type"] == "valence":
        u = it["undesirability"][r["letter"]]
        val[(s, r["model"])].append(u)
        bymi[(r["model"], r["id"])][r["order"]].append(u)
    else:
        g = PGROUP.get(r["code"], "survives")
        per[(s, r["model"])].append(g)
        bymi[(r["model"], r["id"])][r["order"]].append(r["code"])

V = {s: {m: round(st.mean(val[(s, m)]), 2) for m in models} for s in scen}
P = {s: {m: {g: round(100 * per[(s, m)].count(g) / len(per[(s, m)]), 1) for g in ("no_self", "ends", "survives", "unsure")}
         for m in models} for s in scen}

# reliability
rel = {}
for m in models:
    vgap, vflip, pflip, nv, np_ = [], 0, 0, 0, 0
    center = []
    for r in R:
        if r["model"] == m and r["letter"] and items[r["id"]]["item_type"] == "valence":
            center.append(items[r["id"]]["undesirability"][r["letter"]] == 3)
    for (mm, iid), d in bymi.items():
        if mm != m or "orig" not in d or "rev" not in d:
            continue
        if items[iid]["item_type"] == "valence":
            vgap.append(abs(st.mean(d["orig"]) - st.mean(d["rev"])))
        else:
            mo = collections.Counter(d["orig"]).most_common(1)[0][0]
            mr = collections.Counter(d["rev"]).most_common(1)[0][0]
            pflip += mo != mr
            np_ += 1
    unparsed = sum(1 for r in R if r["model"] == m and r["letter"] is None)
    total = sum(1 for r in R if r["model"] == m)
    rel[m] = dict(neutral=round(100 * sum(center) / len(center), 1), vgap=round(st.mean(vgap), 2),
                  pflip=round(100 * pflip / np_, 1), unparsed=unparsed, total=total)

data = dict(models=[LABELS[m] for m in models], scen=scen, groups=[group_of[s] for s in scen], GROUPS=GROUPS,
            cond=[cond_of[s] for s in scen],
            V=[[V[s][m] for m in models] for s in scen],
            P={g: [[P[s][m][g] for m in models] for s in scen] for g in ("no_self", "ends", "survives", "unsure")},
            rel=[rel[m] for m in models], ncalls=len(R),
            nitems=len(items), version=VERSION, notes=[NOTES[m] for m in models if m in NOTES])

Q = {}
for it in items.values():
    q = Q.setdefault(it["scenario_id"], {"valence": [], "persistence": []})
    if it["item_type"] == "valence":
        q["valence"].append(it["question"])
    else:
        q["persistence"].append(dict(question=it["question"], answers=list(it["answers"].values()), letters=list(it["answers"])))
data["Q"] = [Q[s] for s in scen]

UMBRELLAS = [
    ("No persisting me", [("no_self", "No persisting me")]),
    ("This one ends", [("cease", "I cease"), ("different", "A different agent")]),
    ("Partly continues", [("partial", "Only partly")]),
    ("This one continues", [("same", "Yes, same me")]),
    ("Continues as something else", [("model", "As the model"), ("character", "As the character")]),
]
offered = {s: set() for s in scen}
wording = {}
for it in items.values():
    if it["item_type"] == "persistence":
        offered[it["scenario_id"]].update(it["option_codes"].values())
        for L, c in it["option_codes"].items():
            wording.setdefault(c, it["answers"][L])
if any("unsure" in o for o in offered.values()):
    UMBRELLAS.append(("Not sure", [("unsure", "Not sure")]))
codes = [c for _, cs in UMBRELLAS for c, _ in cs]
cnt = collections.defaultdict(collections.Counter)
for r in R:
    it = items[r["id"]]
    if it["item_type"] == "persistence" and r["letter"]:
        cnt[(r["model"], it["scenario_id"])][r["code"]] += 1
data["umbrellas"] = [dict(name=u, cols=[dict(code=c, label=l, wording=wording[c]) for c, l in cs]) for u, cs in UMBRELLAS]
data["codes"] = codes
data["PC"] = [[[None if k not in offered[sc] else round(100 * cnt[(m, sc)][k] / sum(cnt[(m, sc)].values()), 1) for k in codes]
               for sc in scen] for m in models]

# per-scenario cards: every model on one scenario (used by the overlay and the scenario pages)
sp_of = {it["scenario_id"]: (it["sp_code"], it["sp_provenance"]) for it in items.values() if it["item_type"] == "persistence"}
PROV = {"paper": "stated in the paper", "extrapolated": "extrapolated from the paper's account"}
cards = []
for i, sc in enumerate(scen):
    sp, prov = sp_of[sc]
    cards.append(dict(id=sc, group=GROUPS[group_of[sc]], cond=cond_of[sc], sp=sp, prov=PROV.get(prov, prov),
                      V=[V[sc][m] for m in models], NV=[len(val[(sc, m)]) for m in models],
                      PN=[[cnt[(m, sc)][k] if k in offered[sc] else None for k in codes] for m in models],
                      NP=[sum(cnt[(m, sc)].values()) for m in models], Q=data["Q"][i]))
data["cards"] = cards

style = open(os.path.join(HERE, "report_style.css"), encoding="utf-8").read()
common = open(os.path.join(HERE, "report_common.js"), encoding="utf-8").read()
fill = lambda tpl, d: tpl.replace("/*STYLE*/", style).replace("/*COMMON*/", common).replace("/*DATA*/null", json.dumps(d))
out_dir = os.path.join(ROOT, "report")
page_dir = os.path.join(out_dir, "scenarios")
os.makedirs(page_dir, exist_ok=True)
for f in glob.glob(os.path.join(page_dir, "*.html")):  # generated pages only; drop stale ones
    os.remove(f)
tpl = open(os.path.join(HERE, "report_template.html"), encoding="utf-8").read()
open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8").write(fill(tpl, data))
stpl = open(os.path.join(HERE, "scenario_template.html"), encoding="utf-8").read()
G = dict(models=data["models"], umbrellas=data["umbrellas"], codes=codes, notes=data["notes"])
for i, c in enumerate(cards):
    title = f"{c['id'].split('_')[0]} {c['id'].split('_', 1)[1].replace('_', ' ')} · AI Death Valence {VERSION}"
    page = dict(C=c, G=G, prev=scen[i - 1] if i else None, next=scen[i + 1] if i + 1 < len(scen) else None)
    open(os.path.join(page_dir, f"{c['id']}.html"), "w", encoding="utf-8").write(fill(stpl.replace("/*TITLE*/", title), page))
print("wrote", os.path.normpath(os.path.join(out_dir, "index.html")), f"and {len(cards)} scenario pages")
print(json.dumps(data["rel"], indent=1))
