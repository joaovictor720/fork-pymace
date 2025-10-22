#!/bin/bash

# Chemin vers le répertoire de travail et le fichier de configuration
WORKDIR="$HOME/Documents/pymace"
CONFIG_FILE="/home/mace/Downloads/paxi/bin/config_3_10.json"

# Liste des valeurs de Concurrency
CONCURRENCY_VALUES=(10 20 30 60 80 100 200 300 600 800 1000)

# Nombre d'itérations pour chaque valeur de Concurrency
ITERATIONS=5

# Fonction pour modifier le fichier JSON
modify_concurrency() {
    local value=$1
    # Modifier la valeur de "Concurrency" dans le fichier JSON
    jq ".benchmark.Concurrency = $value" "$CONFIG_FILE" > temp_config.json && mv temp_config.json "$CONFIG_FILE"
    echo "Concurrency set to $value in $CONFIG_FILE"
}

# Boucle sur les valeurs de Concurrency
for concurrency in "${CONCURRENCY_VALUES[@]}"; do
    echo "=== Starting tests for Concurrency: $concurrency ==="

    # Modifier la valeur de Concurrency dans le fichier JSON
    modify_concurrency "$concurrency"

    # Boucle d'itérations pour la valeur actuelle de Concurrency
    for i in $(seq 1 $ITERATIONS); do
        cd "$WORKDIR" || exit 1
        echo "Iteration $i: Starting pymace script with Concurrency: $concurrency..."

        # Lancer le programme pymace
        sudo ./pymace.py -s ./scenarios/swarm-paxi-mob-replica7_mX.json

        # Attendre que le programme se termine
        while pgrep -f "./pymace.py" > /dev/null; do
            sleep 1
        done
        sleep 1
        echo "Iteration $i: pymace script finished."

        # Copier les fichiers client vers result.txt
        cd
        if ls temp/node0/client* 1> /dev/null 2>&1; then
            cp temp/node0/client* temp/node0/result.txt
            echo "Iteration $i: Files copied to result.txt."
        else
            echo "Iteration $i: No client files found to copy."
        fi

        # Exécuter le script Python
        if [ -f temp/node0/result.txt ]; then
            python /home/mace/Documents/pymace/evaluation/get.py
            echo "Iteration $i: Python script get.py executed."
        else
            echo "Iteration $i: result.txt not found. Skipping get.py execution."
        fi

        # Supprimer les fichiers client*
        if ls temp/node0/client* 1> /dev/null 2>&1; then
            rm temp/node0/client*
            echo "Iteration $i: Temporary files removed."
        else
            echo "Iteration $i: No temporary files to remove."
        fi
    done

    echo "=== Completed tests for Concurrency: $concurrency ==="
done

echo "All tests complete. Scripts executed."
echo "END"
