from breakfast.names import Names
from breakfast.position import Position


class Application:

    def __init__(self, source):
        self._initial_source = source

    def rename(self, row, column, new_name):
        position = Position(self._initial_source, row=row, column=column)
        old_name = self._initial_source.get_name_at(position)
        visitor = Names()
        visitor.visit_source(self._initial_source)
        for source in self._get_additional_sources():
            visitor.visit_source(source)

        for occurrence in reversed(visitor.get_occurrences(old_name,
                                                           position)):
            occurrence.source.replace(
                position=occurrence,
                old=old_name,
                new=new_name)

    def _get_additional_sources(self):
        return []
