.PHONY: pilot dev test clean seed

GRAPH_DSN ?= postgresql://postgres:postgres@localhost:5433/s2p

pilot:
	docker compose up -d --build

dev:
	cd backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 8002 --reload

test:
	cd backend && python -m pytest tests/ -q --timeout=300

clean:
	docker compose down -v --remove-orphans

seed:
	python scripts/seed_s2p_graph.py --graph s2p_graph --dsn "$(GRAPH_DSN)"
