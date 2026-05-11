cd /home/mace/git/fork-pymace

# 0) (Opcional) limpar agregados/plots antigos
rm -f results/all_results.csv results/all_capacity_samples.csv results/aggregated_results.csv
rm -f results/plots/*.png

# 1) Reparse: gerar summary.csv em cada (scenario__expanded/variant/app)
# (usa parse_metrics.py que percorre run_* e escreve summary.csv no diretório pai)
for d in results/*__expanded/*/{broadcast,rapid,multiunicast,trickle}; do
  [ -d "$d" ] || continue
  python3 evaluation/parse_metrics.py "$d" >/dev/null || echo "[WARN] parse_metrics falhou em $d"
done

# 2) Recoletar: juntar todos summary.csv + coverage_nodes.csv em CSVs globais
python3 evaluation/collect_all_results.py
mv all_results.csv results/
mv all_capacity_samples.csv results/

# 3) Reagregar: gerar results/aggregated_results.csv a partir dos dois CSVs globais
python3 evaluation/aggregate_results.py results/all_results.csv results/aggregated_results.csv

# 4) Plotar
python3 evaluation/plot_results.py
