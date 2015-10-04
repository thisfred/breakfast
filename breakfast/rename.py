"""Rename refactorings."""
from astroid import as_string, parse, nodes, transforms


class AsStringVisitorImproved(as_string.AsStringVisitor):
    """Modifies AsStringVisitor that does not output unneeded parentheses."""

    def wrap_expression(self, node):
        """Leave parentheses off simple expressions."""
        stringed = node.accept(self)

        # XXX: hack to detect whether this is a leaf node, there may be
        # a better way.
        if not node._astroid_fields:
            return stringed

        return '(%s)' % stringed

    def visit_binop(self, node):
        return '%s %s %s' % (
            self.wrap_expression(node.left),
            node.op,
            self.wrap_expression(node.right))


def rename_variable(*, source, old_name, new_name):
    """Rename a local variable."""

    source_ast = parse(source)
    renamer = transforms.TransformVisitor()

    def change_name(node):
        """Modify the node's name."""
        node.name = new_name

    def has_old_name(node):
        """Return only nodes with the old name."""
        return node.name == old_name

    for node_type in (nodes.Name, nodes.AssignName, nodes.DelName):
        renamer.register_transform(
            node_type, change_name, predicate=has_old_name)

    transformed = renamer.visit(source_ast)
    stringer = AsStringVisitorImproved(indent='    ')
    return stringer(transformed)
