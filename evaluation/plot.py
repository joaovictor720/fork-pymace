import re
import matplotlib.pyplot as plt
from collections import defaultdict
from itertools import cycle

def plot_median_vs_throughput_sorted_by_concurrency(input_file, title):
    """
    Plot median vs throughput grouped by concurrency for a given file.
    
    Args:
        input_file (str): Path to the input file.
        title (str): Title for the plot.
    """
    data_by_concurrency = defaultdict(list)

    # Read the input file
    with open(input_file, 'r') as file:
        for line in file:
            # Extract Concurrency, Throughput, and median using regex
            concurrency_match = re.search(r"Concurrency: (\d+)", line)
            throughput_match = re.search(r"Throughput: ([\d.]+)", line)
            median_match = re.search(r"median: ([\d.]+)", line)

            if concurrency_match and throughput_match and median_match:
                concurrency = int(concurrency_match.group(1))
                throughput = float(throughput_match.group(1))
                median = float(median_match.group(1))

                # Group data by Concurrency
                data_by_concurrency[concurrency].append((throughput, median))

    # Sort data by Concurrency
    sorted_concurrency_values = sorted(data_by_concurrency.keys())

    # Define a color palette with 15 distinct colors
    colors = cycle(plt.cm.tab20.colors[:15])  # Tab20 palette with up to 15 colors

    plt.figure(figsize=(12, 8))

    # Process each concurrency group
    load_vals = (10, 20, 30, 60, 80, 100, 200, 300, 600, 800, 1000)
    load_dic = {}
    for l,i in enumerate(load_vals):
        load_dic[i] = l

    for concurrency in sorted_concurrency_values:
        # Sort each group's data by throughput
        sorted_data = sorted(data_by_concurrency[concurrency])
        throughputs, medians = zip(*sorted_data)

        # Plot data for this concurrency group
        plt.plot(throughputs, medians, marker='o', linestyle='-', label=f' {load_dic[concurrency]}', color=next(colors))

    # Configure plot
    # plt.title(title, fontsize = 22)
    plt.xticks(fontsize=18)  # X-axis tick labels
    plt.yticks(fontsize=18)  # Y-axis tick labels
    plt.ylim(0, 220)
    plt.xlim(0, 7500)  
    plt.xlabel('Throughput (req/s)',fontsize = 18)
    plt.ylabel('Latency (ms)',fontsize = 18)
    plt.grid(True)
    plt.legend(title="System Load", loc='upper left', bbox_to_anchor=(1, 1), fontsize=18)
    plt.tight_layout()

# Example usage
files_with_titles = [
    # ("/home/mace/Documents/pymace/evaluation/constant_density/N7_m14.csv", "M=14 V"),
    # ("/home/mace/Documents/pymace/evaluation/constant_density/N7_m12.csv", "M=12 V"),
    # ("/home/mace/Documents/pymace/evaluation/constant_density/N7_m10.csv", "M=10 V"),
    # ("/home/mace/Documents/pymace/evaluation/constant_density/N7_m9.csv", "M=9 V"),
    ("/home/mace/Documents/pymace/evaluation/change_nodes/N7_mobile_V.csv", "Impact of mobility"),
    # ("/home/mace/Documents/pymace/evaluation/fixed_far/N3_fixed_V.csv", "N=3 fixed V"),
    # ("/home/mace/Documents/pymace/evaluation/fixed_all_sight/N7_fixed_V.csv", "N=7 fixed all"),
    # ("/home/mace/Documents/pymace/evaluation/fixed_far/N7_fixed_V.csv", "System reponse with fixed nodes"),
    # ("/home/mace/Documents/pymace/evaluation/fixed_far/N3_fixed_IV.csv", "N=3 fixed IV"),
    # ("/home/mace/Documents/pymace/evaluation/fixed_far/N7_fixed_IV.csv", "N=7 fixed IV"),
    # ("/home/mace/Documents/pymace/evaluation/change_nodes/N3_mobile_V.csv", "N=3 mobile"),
    # ("/home/mace/Documents/pymace/evaluation/change_nodes/N7_mobile_V.csv", "N=7 mobile"),
]

for file_path, title in files_with_titles:
    plot_median_vs_throughput_sorted_by_concurrency(file_path, title)

plt.show()
