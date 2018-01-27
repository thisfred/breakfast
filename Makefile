test:
	tox
test-continually:
	rerun -i =*.egg-info -i=.coverage* -i=.cache -i=neomake.log -i=.mypy_cache -i=.tox -i=.coverage -i=.venv --verbose tox
