#!/bin/bash
set -e

# Configurações Globais
RUNS_PER_EXPERIMENT=10
ALGORITHMS=("broadcast" "rapid")

# Lista exata dos nomes dos cenários que criamos (devem bater com o nome da pasta/json em scenarios/)
SCENARIOS=(
    "scenario_A_baseline"
    "scenario_B_mobility"
    "scenario_C_stress"
)

# Caminho para o script original que você forneceu
RUN_SCRIPT="/home/mace/git/fork-pymace/evaluation/run_experiment.sh"

echo "=================================================="
echo "INICIANDO BATERIA COMPLETA DE TESTES PARA O ARTIGO"
echo "Algoritmos: ${ALGORITHMS[*]}"
echo "Cenários  : ${SCENARIOS[*]}"
echo "Runs/Cada : $RUNS_PER_EXPERIMENT"
echo "=================================================="
echo ""

# Loop Principal
for SCENARIO in "${SCENARIOS[@]}"; do
    echo "##################################################"
    echo "### INICIANDO CENÁRIO: $SCENARIO"
    echo "##################################################"

    for ALGO in "${ALGORITHMS[@]}"; do
        echo ">>> Executando Algoritmo: $ALGO"
        
        # Chama o seu script original
        # O set -e no topo garante que se o run_experiment.sh falhar, este script para.
        $RUN_SCRIPT --scenario "$SCENARIO" --algorithm "$ALGO" --runs "$RUNS_PER_EXPERIMENT"
        
        echo ">>> Concluído: $ALGO em $SCENARIO"
        echo "--------------------------------------------------"
        
        # Opcional: Pausa pequena para o SO limpar sockets/buffers se necessário
        sleep 2 
    done
    echo ""
done

echo "=================================================="
echo "BATERIA FINALIZADA COM SUCESSO!"
echo "Verifique os resultados na pasta ../results/"
echo "=================================================="