.PHONY: test
test: .venv requirements.txt test-requirements.txt
	tox

.PHONY: test-continuously
test-continuously: .venv requirements.txt test-requirements.txt
	.venv/bin/ptw -- --testmon -rf -l -s -x tests

.venv: requirements.txt test-requirements.txt
	test -d .venv || virtualenv -p python3.7 .venv || python3.7 -m venv .venv
	.venv/bin/pip install pip-tools
	.venv/bin/pip install -r requirements.txt -r test-requirements.txt
	touch $@

%.txt: %.in
	.venv/bin/pip-compile -v --output-file $@ $<

.PHONY: autoformat
autoformat: .venv
	.venv/bin/black setup.py breakfast tests vim
	.venv/bin/isort -rc setup.py breakfast tests vim

.PHONY: test-changed
test-changed: .venv requirements.txt test-requirements.txt
	.venv/bin/pytest --testmon -rf -l -s -x tests
