# -*- coding: utf-8 -*-
import sys, os, io
sys.path.insert(0, '.')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

results = []
try:
    from services.agent_router import router, check_router_status
    results.append("agent_router: OK")
    cfg = check_router_status()
    results.append("  GLM OK: " + str(cfg.get('glm',{}).get('configured')))
    results.append("  DS OK: " + str(cfg.get('ds',{}).get('configured')))
except Exception as e:
    results.append("agent_router: FAIL - " + str(e))

try:
    from services.agent_skills import registry
    results.append("agent_skills: OK, skills=" + str(len(registry.get_all())))
except Exception as e:
    results.append("agent_skills: FAIL - " + str(e))

try:
    from services.agent_orchestrator import Orchestrator
    results.append("agent_orchestrator: OK")
except Exception as e:
    results.append("agent_orchestrator: FAIL - " + str(e))

try:
    from services.llm_service import check_llm_config
    results.append("llm_service: OK")
except Exception as e:
    results.append("llm_service: FAIL - " + str(e))

for r in results:
    print(r)
print("DONE")
