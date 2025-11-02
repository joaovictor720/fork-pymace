#!/bin/bash

# Caminho absoluto do diretório atual
DIR="$(cd "$(dirname "$0")" && pwd)"

# Abre 3 terminais xterm, um para cada nó
xterm -hold -e "$DIR/crdt-replica -id 1.1 -config /home/mace/git/fork-pymace/apps/crdt-replica/node-config/crdt-config.json" &
xterm -hold -e "$DIR/crdt-replica -id 1.2 -config /home/mace/git/fork-pymace/apps/crdt-replica/node-config/crdt-config.json" &
xterm -hold -e "$DIR/crdt-replica -id 1.3 -config /home/mace/git/fork-pymace/apps/crdt-replica/node-config/crdt-config.json" &
