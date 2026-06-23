import sys, inspect
sys.path.insert(0, '.')

from server.observability.callbacks import LangFuseTracer
src = inspect.getsource(LangFuseTracer._create_generation)
if "if session_id else" in src:
    print("callbacks.py: NEW")
else:
    print("callbacks.py: OLD - missing null guard")

import server.ai.aggregator as agg_mod
if hasattr(agg_mod, "REVIEWER_PRIORITY"):
    print("aggregator.py: NEW")
    print(f"  priority: {agg_mod.REVIEWER_PRIORITY}")
else:
    print("aggregator.py: OLD - no priority")

if hasattr(agg_mod, "EMBEDDING_SIMILARITY_THRESHOLD"):
    print("  embedding: YES")
else:
    print("  embedding: NO")

from server.routers.review import trigger_review
src2 = inspect.getsource(trigger_review)
if "force" in src2:
    print("review.py: NEW (force)")
else:
    print("review.py: OLD (no force)")

from server.ai.prompts.logic_review import LOGIC_REVIEW_PROMPT
if "你是一名" in LOGIC_REVIEW_PROMPT:
    print("logic prompt: CHINESE")
else:
    print("logic prompt: ENGLISH")
