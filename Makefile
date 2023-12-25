.PHONY: test coverage coverage-combine install lint dist-clean

test: .venv/installed
	.venv/bin/coverage run .venv/bin/pytest

install: .venv/installed

lint: .venv/installed
	.venv/bin/pre-commit run --all-files

dist-clean:
	rm -rf .venv

.venv:
	python -m venv .venv
	touch $@

.venv/installed: .venv pyproject.toml
	.venv/bin/pip install -e .[lsp,dev,test]
	touch $@
