.PHONY: test demo

test:
	PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_talent_pool.py

demo:
	PYTHONDONTWRITEBYTECODE=1 python3 scripts/mock_server.py & server_pid=$$!; trap 'kill $$server_pid' EXIT; sleep 1; POTOK_BASE_URL=http://localhost:8765 POTOK_API_TOKEN=demo PYTHONDONTWRITEBYTECODE=1 python3 scripts/talent_pool.py reserve; POTOK_BASE_URL=http://localhost:8765 POTOK_API_TOKEN=demo PYTHONDONTWRITEBYTECODE=1 python3 scripts/talent_pool.py dedup; POTOK_BASE_URL=http://localhost:8765 POTOK_API_TOKEN=demo PYTHONDONTWRITEBYTECODE=1 python3 scripts/talent_pool.py search '[{"term":"питон","kind":"original"},{"term":"python","kind":"synonym"}]'
