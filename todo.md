## To do list


* add other refactorings
* add go to definition
* handle renames rest of the language
* handle renames for/across type annotations
* store scope graphs (or partial paths?)
* relative imports
* match statements
* implement LSP (see ruff)
* prevent renaming to a name that clashes with an existing one
* handle renames across files
* calculate import graph


## known bugs
* can't rename dir_path in
  `for dir_path, directories, filenames in os.walk(root):`
