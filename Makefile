.PHONY: quick check run install-browser

quick:
	python -m unittest discover -s tests -v
	python -m compileall -q app tests

check: quick
	node --check static/app.js

run:
	uvicorn app.main:app --host 0.0.0.0 --port 39057 --workers 1

install-browser:
	python -m playwright install chromium
