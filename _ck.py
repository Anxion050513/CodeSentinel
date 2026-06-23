import json
with open('server/eval/golden_reviews.json','r',encoding='utf-8') as f:
    data = json.load(f)
n = len(data)
desc0 = data[0]['description'][:30]
print(f"Entries: {n}")
print(f"First desc: {desc0}")
if n == 5:
    print("OLD - 5 English entries")
else:
    print("NEW version")
