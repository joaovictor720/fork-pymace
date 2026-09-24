"""The evaluation path supports the VM's CORE 8.2 and the host's CORE 9.2."""

import inspect


def add_node(session, node_class, *, name, x, y):
    if "position" in inspect.signature(session.add_node).parameters:
        from core.nodes.base import Position

        return session.add_node(node_class, name=name, position=Position(x=x, y=y))
    from core.emulator.data import NodeOptions

    return session.add_node(node_class, options=NodeOptions(name=name, x=x, y=y))
