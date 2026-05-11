./evaluation/run_experiment.sh --scenario trickle_calibration_ip --app trickle --runs 10 --keep-going
python3 evaluation/analyze_trickle_calibration.py results/trickle_calibration_ip__expanded
