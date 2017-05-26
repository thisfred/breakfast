test-continually:
	rerun -i=.cache -i=neomake.log -i=.mypy_cache -i=.tox -i=.coverage -i=.venv --verbose tox
