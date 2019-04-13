.PHONY: clean test test-continually

test: .venv requirements.txt test-requirements.txt
	tox

.venv: requirements.txt test-requirements.txt
	test -d .venv || python3 -m venv .venv
	.venv/bin/pip install pip-tools
	.venv/bin/pip install -r requirements.txt -r test-requirements.txt
	touch $@

%.txt: %.in
	.venv/bin/pip-compile -v --output-file $@ $<

reformat: .venv
	.venv/bin/black -l 80 setup.py breakfast tests vim
	.venv/bin/isort -rc setup.py breakfast tests vim

test-continually: .venv requirements.txt test-requirements.txt
	.venv/bin/rerun -i =*.egg-info -i=.coverage* -i=.cache -i=neomake.log -i=.mypy_cache -i=.tox \
		-i=.coverage -i=.venv --verbose \
		".venv/bin/pytest --testmon -rf -l -s -x tests vim/ftplugin/python/tests"
