.PHONY: test demo

test:
	PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_talent_pool.py
	PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_job_seeker.py
	PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_mcp_server.py
	PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_tg_bot.py

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
	python3 scripts/talent_pool.py reopen '{"target_job_id":202,"source_job_id":201,"source_represents_previous_criteria":true,"applicant_salary_currency":"RUR"}'
