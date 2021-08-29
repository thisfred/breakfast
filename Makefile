.PHONY: test
test: .requirements
	.venv/bin/tox

pip-tools:
	pip install pip-tools

.requirements: pip-tools requirements.txt test-requirements.txt
	pip install -r requirements.txt -r test-requirements.txt
	touch $@

%.txt: %.in pip-tools
	.venv/bin/pip-compile -v --generate-hashes --output-file $@ $<
