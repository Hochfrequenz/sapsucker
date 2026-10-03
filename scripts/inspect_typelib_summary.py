import json
from collections import Counter
d = json.load(open("typelib.json"))
t = d["types"]
w = {k: v for k, v in t.items() if v.get("members")}
print("with members:", len(w))
print("typekinds:", Counter(v.get("typekind") for v in t.values()))
print()
for k, v in sorted(w.items(), key=lambda kv: -len(kv[1]["members"]))[:25]:
    print(f"{len(v['members']):5d}  {k}")
print()
print(", ".join(sorted(t)))
