#!/bin/bash
set -e

# Configurações Globais
RUNS_PER_EXPERIMENT=5

# Lista exata dos nomes dos cenários que criamos (devem bater com o nome da pasta/json em scenarios/)
SCENARIOS=(
    "important_batman"
    "important_ip"
)

# Caminho para o script original que executa os experimentos
RUN_SCRIPT="/home/mace/git/fork-pymace/evaluation/run_experiment.sh"

echo "=================================================="
echo "INICIANDO BATERIA COMPLETA DE TESTES PARA O ARTIGO"
echo "Runs/Cada : $RUNS_PER_EXPERIMENT"
echo "=================================================="
echo ""

# Loop Principal
for SCENARIO in "${SCENARIOS[@]}"; do
    echo "##################################################"
    echo "### INICIANDO CENÁRIO: $SCENARIO"
    echo "##################################################"

    # Escolha do algoritmo baseada no nome do cenário
    if [[ "$SCENARIO" == *"_batman"* ]]; then
        ALGO="broadcast"
    elif [[ "$SCENARIO" == *"_ip"* ]]; then
        ALGO="rapid"
    else
        echo "ERRO: cenário desconhecido (não termina em _batman ou _ip): $SCENARIO" >&2
        exit 1
    fi

    echo ">>> Executando Algoritmo: $ALGO"

    # Chama o script original
    $RUN_SCRIPT --scenario "$SCENARIO" --algorithm "$ALGO" --runs "$RUNS_PER_EXPERIMENT"

    echo ">>> Concluído: $ALGO em $SCENARIO"
    echo "--------------------------------------------------"

    # Opcional: Pausa pequena para o SO limpar sockets/buffers se necessário
    sleep 2
    echo ""
done

echo "=================================================="
echo "BATERIA FINALIZADA COM SUCESSO!"
echo "Verifique os resultados na pasta ../results/"
echo "=================================================="
