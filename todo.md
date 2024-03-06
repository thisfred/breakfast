## To do list

### Features:

* cache scope graphs (and/or partial paths?)
* add go to definition
* add Undo
* add other refactorings
  * inline function
  * inline method
  * inline variable
* extract static method when self is not used
* extract function to top level scope

### Refactoring


### Chores

* migrate to tox or nox, so we can test against all supported python
  versions locally as well as in github actions.

### Bugs:

* newline literals in source code seem to break Source.get_ast
* rename async / awaited functions
