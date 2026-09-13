import struct, json, sys
from pathlib import Path

path = Path(sys.argv[1])
d = path.read_bytes()
ln = struct.unpack_from('<I', d, 12)[0]
j = json.loads(d[20:20+ln])
nodes = j.get('nodes', [])
names = {i: (n.get('name') or '<unnamed>') for i, n in enumerate(nodes)}

children = {i: list(n.get('children', [])) for i, n in enumerate(nodes)}
has_parent = set()
for i, ch in children.items():
    for c in ch:
        has_parent.add(c)

roots = [i for i in range(len(nodes)) if i not in has_parent]
def go(idx, depth):
    print('  ' * depth + names[idx] + ('  [mesh]' if nodes[idx].get('mesh') is not None else '') + ('  [skin]' if 'skin' in nodes[idx] else ''))
    for c in children[idx]:
        go(c, depth + 1)

for r in roots:
    go(r, 0)
print('total nodes:', len(nodes))
