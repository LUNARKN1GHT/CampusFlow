"""A002：任务先后依赖的图规则。

只处理"先完成前置"的有向边；包含关系不在这里推断。
成环时给出具体路径，调用方不得把环内任务当作可执行顺序。
节点按编号排序后再遍历，保证同一张图每次得到相同的路径与分量顺序。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping


def dependency_cycle(edges: Mapping[int, Iterable[int]]) -> list[int] | None:
    """返回第一条依赖环（首尾同一任务），无环返回 None。

    节点与邻接表都按编号排序，重复调用得到相同路径。
    """
    adjacency = {node: tuple(sorted(set(targets))) for node, targets in edges.items()}
    nodes = sorted(
        set(adjacency) | {target for targets in adjacency.values() for target in targets}
    )
    # state：0 未访问、1 在当前递归栈上、2 已完成；命中 1 说明回到路径中的节点。
    state: dict[int, int] = {}
    path: list[int] = []
    position: dict[int, int] = {}

    def visit(node: int) -> list[int] | None:
        state[node] = 1
        position[node] = len(path)
        path.append(node)
        for target in adjacency.get(node, ()):
            if state.get(target, 0) == 0:
                found = visit(target)
                if found is not None:
                    return found
            elif state.get(target) == 1:
                # 从环的起点截取路径并追加终点，首尾相同，便于直接展示。
                return [*path[position[target] :], target]
        path.pop()
        position.pop(node)
        state[node] = 2
        return None

    for node in nodes:
        if state.get(node, 0) == 0:
            found = visit(node)
            if found is not None:
                return found
    return None


def cyclic_components(edges: Mapping[int, Iterable[int]]) -> list[list[int]]:
    """返回所有含环的强连通分量；自依赖也计为环。

    使用 Tarjan 算法，一次遍历覆盖整张图；结果按分量最小节点编号排序，
    调用方可以稳定地报告同一组问题。无环图返回空列表。
    """
    adjacency = {node: tuple(sorted(set(targets))) for node, targets in edges.items()}
    nodes = sorted(
        set(adjacency) | {target for targets in adjacency.values() for target in targets}
    )
    index: dict[int, int] = {}
    low: dict[int, int] = {}
    on_stack: set[int] = set()
    stack: list[int] = []
    counter = 0
    components: list[list[int]] = []

    def connect(node: int) -> None:
        nonlocal counter
        index[node] = low[node] = counter
        counter += 1
        stack.append(node)
        on_stack.add(node)
        for target in adjacency.get(node, ()):
            if target not in index:
                connect(target)
                low[node] = min(low[node], low[target])
            elif target in on_stack:
                low[node] = min(low[node], index[target])
        if low[node] == index[node]:
            # low == index 时弹栈得到一棵强连通分量；单节点还要检查自环。
            component: list[int] = []
            while True:
                member = stack.pop()
                on_stack.discard(member)
                component.append(member)
                if member == node:
                    break
            component.sort()
            if len(component) > 1 or component[0] in adjacency.get(component[0], ()):
                components.append(component)

    for node in nodes:
        if node not in index:
            connect(node)
    components.sort(key=lambda item: item[0])
    return components
