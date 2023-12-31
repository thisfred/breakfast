from typing import Any

from breakfast.names import Pop, Push
from breakfast.scope_graph import (
    NodeType,
    ScopeGraph,
    ScopeNode,
)

try:
    import graphviz
except ImportError:
    graphviz = None


def view_graph(graph: ScopeGraph) -> None:
    if graphviz is None:
        return

    visualization = graphviz.Digraph()
    visualization.attr(rankdir="BT")
    for same_rank_nodes in graph.group_by_rank():
        subgraph = graphviz.Digraph()
        subgraph.attr(rankdir="BT")
        subgraph.attr(rank="same")

        for node_id in same_rank_nodes:
            node = graph.nodes[node_id]
            render_node(node, subgraph, graph)

        visualization.subgraph(subgraph)

    for from_id, to_nodes in graph.edges.items():
        for edge, to_node_id in to_nodes:
            if edge.to_enclosing_scope:
                label = "e"
            elif edge.priority:
                label = str(edge.priority)
            else:
                label = ""
            visualization.edge(
                str(from_id),
                str(to_node_id),
                label=label,
            )

    visualization.render(view=True)
    breakpoint()


def render_node(node: ScopeNode, subgraph: Any, scope_graph: ScopeGraph) -> None:
    if isinstance(node.action, Pop):
        subgraph.node(
            name=str(node.node_id),
            label=f"↑{node.node_id} {node.action.path}"
            + (
                f"<{node.position.row}, {node.position.column}>"
                if node.position
                else "<>"
            )
            + (
                f"{{{','.join(r.__doc__ for r in node.rules if r.__doc__)}}}"
                if node.rules
                else ""
            ),
            shape="box",
            peripheries="2" if node.node_type is NodeType.DEFINITION else "",
            style="dashed" if node.node_type is not NodeType.DEFINITION else "",
            color="#B10000",
            fontcolor="#B10000",
        )
    elif isinstance(node.action, Push):
        subgraph.node(
            name=str(node.node_id),
            label=f"↓{node.node_id} {node.action.path} "
            + (
                f"<{node.position.row}, {node.position.column}>"
                if node.position
                else "<>"
            )
            + (
                f"{{{','.join(r.__doc__ for r in node.rules if r.__doc__)}}}"
                if node.rules
                else ""
            ),
            shape="box",
            style="dashed",
            color="#00B1B1",
            fontcolor="#00B1B1",
        )

    elif node.action:
        subgraph.node(
            str(node.node_id), node.name or str(node.node_id) or "", shape="box"
        )
    else:
        subgraph.node(
            name=str(node.node_id),
            label=node.name or str(node.node_id) or "",
            shape="circle",
            style="filled" if node is scope_graph.root else "",
            fillcolor="black" if node is scope_graph.root else "",
            fixedsize="true",
            width="0.4" if node.name else "0.3",
            height="0.4" if node.name else "0.3",
            color="#B100B1" if node.name else "",
            fontcolor="#B100B1" if node.name else "",
        )
