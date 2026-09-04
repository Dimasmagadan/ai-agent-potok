MCPB_VERSION := 2.1.2
MCPB_STAGE := dist/mcpb
MCPB_ARCHIVE := dist/potok-recruiting-agent.mcpb

.PHONY: test demo mcpb

test:
	PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_talent_pool.py
	PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_job_seeker.py
	PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_mcp_server.py
	PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_mcpb_entry.py
	PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_tg_bot.py

mcpb:
	rm -rf $(MCPB_STAGE) $(MCPB_ARCHIVE)
	mkdir -p $(MCPB_STAGE)/scripts $(MCPB_STAGE)/fixtures
	cp manifest.json LICENSE $(MCPB_STAGE)/
	cp scripts/mcpb_entry.py scripts/mcp_server.py scripts/talent_pool.py scripts/job_seeker.py scripts/mock_server.py $(MCPB_STAGE)/scripts/
	cp fixtures/*.json $(MCPB_STAGE)/fixtures/
	npx --yes @anthropic-ai/mcpb@$(MCPB_VERSION) validate $(MCPB_STAGE)/manifest.json
	npx --yes @anthropic-ai/mcpb@$(MCPB_VERSION) pack $(MCPB_STAGE) $(MCPB_ARCHIVE)
	unzip -l $(MCPB_ARCHIVE)
	@! unzip -l $(MCPB_ARCHIVE) | rtk rg '(^|/)(\.env|tg_bot|research|test_)'

demo:
	PYTHONDONTWRITEBYTECODE=1 python3 scripts/mock_server.py & server_pid=$$!; trap 'kill $$server_pid' EXIT; sleep 1; \
	export POTOK_BASE_URL=http://localhost:8765 POTOK_API_TOKEN=demo POTOK_API_V2_BASE_URL=http://localhost:8765 \
	       POTOK_OPEN_BASE_URL=http://localhost:8765/open POTOK_CONSTRUCTOR_ID=1 PYTHONDONTWRITEBYTECODE=1; \
	python3 scripts/talent_pool.py reserve > /tmp/potok_reserve.json; \
	python3 scripts/talent_pool.py dedup; \
	python3 scripts/talent_pool.py search '[{"term":"питон","kind":"original"},{"term":"python","kind":"synonym"}]' --reserve-file /tmp/potok_reserve.json; \
	python3 scripts/talent_pool.py cv-index --reserve-file /tmp/potok_reserve.json --cache-dir /tmp/potok_cv_cache --limit 3; \
	python3 scripts/talent_pool.py search '[{"term":"fastapi","kind":"original"}]' --reserve-file /tmp/potok_reserve.json --cv-cache-dir /tmp/potok_cv_cache; \
	python3 scripts/job_seeker.py jobs-list; \
	python3 scripts/job_seeker.py jobs-match '{"terms":[{"term":"python","kind":"original"},{"term":"django","kind":"original"}],"filters":{"schedule":"remote"}}'; \
	python3 scripts/talent_pool.py reopen '{"target_job_id":202,"source_job_id":201,"previous_criteria":{"salary_to":280000,"currency_type":"RUR","schedule_type":"fullDay","experience_minimum_years":3,"city":"1","role_terms":["python","backend"],"profile_terms_any":["django"]},"current_criteria":{"salary_to":350000,"currency_type":"RUR","schedule_type":"remote","experience_minimum_years":2,"city":"2","role_terms":["python","backend"],"profile_terms_any":["django","fastapi"]},"applicant_salary_currency":"RUR","context_terms":{"schedule":["удалённо"]},"declination_reason_mapping":{"experience_minimum":[8]}}'
