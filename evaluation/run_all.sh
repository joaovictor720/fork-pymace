#!/bin/bash
set -e

# Configurações Globais
RUNS_PER_EXPERIMENT=5
ALGORITHMS=("broadcast" "rapid")

# Lista exata dos nomes dos cenários que criamos (devem bater com o nome da pasta/json em scenarios/)
SCENARIOS=(
    "article_scenario_1_density"
    "article_scenario_2_mobility"
    "article_scenario_3_stress"
)

# Caminho para o script original que você forneceu
RUN_SCRIPT="./run_experiment.sh"

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