.PHONY: test dependencies
test: .requirements
	tox

pip-tools: .venv
	.venv/bin/pip install pip-tools

dependencies: pip-tools requirements.txt test-requirements.txt optional-requirements.txt

.venv:
	python -m virtualenv .venv

.venv/installed: dependencies .venv
	.venv/bin/pip install -r requirements.txt -r test-requirements.txt -r optional-requirements.txt
	touch $@

install: .venv/installed

%.txt: %.in pip-tools .venv
	.venv/bin/pip-compile -v --generate-hashes --output-file $@ $<
