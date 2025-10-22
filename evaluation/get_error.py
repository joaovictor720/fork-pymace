import re
from collections import defaultdict
import matplotlib.pyplot as plt
import numpy as np

def process_file_with_errors(file_path):
    """
    Extract throughput and median grouped by concurrency, and calculate averages and standard deviations.
    """
    data_by_concurrency = defaultdict(list)

    try:
        with open(file_path, 'r') as file:
            for line in file:
                # Extract Concurrency, Throughput, and median
                concurrency_match = re.search(r"Concurrency: (\d+)", line)
                throughput_match = re.search(r"Throughput: ([\d.]+)", line)
                median_match = re.search(r"median: ([\d.]+)", line)

                if concurrency_match and throughput_match and median_match:
                    concurrency = int(concurrency_match.group(1))
                    throughput = float(throughput_match.group(1))
                    median = float(median_match.group(1))

                    # Group data by concurrency
                    data_by_concurrency[concurrency].append((throughput, median))
    except FileNotFoundError:
        print(f"File not found: {file_path}")
        return []

    # Compute averages and standard deviations
    processed_data = []
    for concurrency, values in data_by_concurrency.items():
        throughputs = [v[0] for v in values]
        medians = [v[1] for v in values]

        avg_throughput = np.mean(throughputs)
        std_throughput = np.std(throughputs)

        avg_median = np.mean(medians)
        std_median = np.std(medians)

        processed_data.append((concurrency, avg_throughput, std_throughput, avg_median, std_median))

    # Sort by concurrency
    return sorted(processed_data, key=lambda x: x[0])

def plot_with_error_bars(files_with_labels):
    """
    Plot average median vs throughput with error bars, sorted by concurrency for multiple datasets.
    """
    plt.figure(figsize=(12, 8))
    # plt.title('Version IV vs V with mobile nodes (with deviation)',fontsize=22)
    plt.xlabel('Throughput (req/s)',fontsize=18)
    plt.ylabel('Latency (ms)',fontsize=18)
    plt.grid(True)

    for file_path, label,color,style in files_with_labels:
        processed_data = process_file_with_errors(file_path)
        if processed_data:
            avg_throughputs = [x[1] for x in processed_data]
            std_throughputs = [x[2] for x in processed_data]
            avg_medians = [x[3] for x in processed_data]
            std_medians = [x[4] for x in processed_data]
            if color is None:
                plt.errorbar(
                    avg_throughputs,
                    avg_medians,
                    xerr=std_throughputs,
                    #yerr=std_medians,
                    fmt='-',
                    capsize=5,
                    label=f"{label}"
                )
            else :
                plt.errorbar(
                    avg_throughputs,
                    avg_medians,
                    xerr=std_throughputs,
                    #yerr=std_medians,
                    fmt='-',
                    capsize=5,
                    label=f"{label} ",
                    color = color,
                    linestyle=style
                )
    plt.xticks(fontsize=18)  # X-axis tick labels
    plt.yticks(fontsize=18)  # Y-axis tick labels
    plt.legend(loc='upper left',fontsize=18)
    plt.tight_layout()
    plt.show()

# Example usage
files_with_labels = [
    ("/home/mace/Documents/pymace/evaluation/N7_mobile_V.csv", "N=7 V","green",None),
    ("/home/mace/Documents/pymace/evaluation/N7_mobile_IV.csv", "N=7 IV", "green","--"),
    ("/home/mace/Documents/pymace/evaluation/N5_mobile_V.csv", "N=5 V","blue",None),
    ("/home/mace/Documents/pymace/evaluation/N5_mobile_IV.csv", "N=5 IV", "blue","--"),
    ("/home/mace/Documents/pymace/evaluation/N3_mobile_V.csv", "N=3 V", "red",None),
    ("/home/mace/Documents/pymace/evaluation/N3_mobile_IV.csv", "N=3 IV",  "red","--"),
    # ("/home/mace/Documents/pymace/evaluation/constant_density/full_IV.csv", "IV","red"),
    # ("/home/mace/Documents/pymace/evaluation/constant_density/full_V.csv", "V","blue"),
    
]

plot_with_error_bars(files_with_labels)
