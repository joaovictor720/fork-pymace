import re
from collections import defaultdict
import matplotlib.pyplot as plt
import statistics

def process_file(file_path):
    """Extract and process throughput and median grouped by concurrency."""
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

    # Compute averages and sort by concurrency
    averages = []
    for concurrency, values in data_by_concurrency.items():
        th = [v[0] for v in values]
        me = [v[1] for v in values]
        th.sort()
        me.sort()
        # avg_throughput = statistics.median(th)
        # avg_median = statistics.median(me)
        avg_throughput = sum(v[0] for v in values) / len(values)
        avg_median =  sum(v[1] for v in values) / len(values)
        averages.append((concurrency, avg_throughput, avg_median))

    # Sort by concurrency
    return sorted(averages, key=lambda x: x[0])

def plot_averages(files_with_labels):
    """
    Plot average median vs throughput sorted by concurrency for multiple datasets.
    
    Args:
        files_with_labels (list of tuples): List of (file_path, label) pairs.
    """
    plt.figure(figsize=(12, 8))
    # plt.title('Version IV vs V with mobile nodes',fontsize = 22)
    # plt.title('Impact of Mobility',fontsize = 22)
    # plt.title('Impact of Routing Updates',fontsize = 22)
    plt.xlabel('Throughput (req/s)', fontsize = 18)
    plt.ylabel('Latency (ms)', fontsize = 18)
    plt.grid(True)

    for file_path, label, color,style in files_with_labels:
        sorted_averages = process_file(file_path)
        if sorted_averages:
            avg_throughputs = [x[1] for x in sorted_averages]
            avg_medians = [x[2] for x in sorted_averages]
            if color is None :
                plt.plot(avg_throughputs, avg_medians, marker='o', linestyle='-', label=f"{label} ")
            else :
                plt.plot(avg_throughputs, avg_medians, marker='o', linestyle=style, label=f"{label} ",color = color)

    plt.xticks(fontsize=18)  # X-axis tick labels
    plt.yticks(fontsize=18)  # Y-axis tick labels
    plt.legend(loc='upper left',fontsize=18)
    plt.tight_layout()
    plt.show()

# Example usage
files_with_labels = [
    #CHanging Nodes
    # ("/home/mace/Documents/pymace/evaluation/change_nodes/N7_mobile_V.csv", "N=7 V", "green",None),
    # ("/home/mace/Documents/pymace/evaluation/change_nodes/N7_mobile_IV.csv", "N=7 IV", "green","--"),
    # ("/home/mace/Documents/pymace/evaluation/change_nodes/N5_mobile_V.csv", "N=5 V", "blue",None),
    # ("/home/mace/Documents/pymace/evaluation/change_nodes/N5_mobile_IV.csv", "N=5 IV", "blue","--"),
    # ("/home/mace/Documents/pymace/evaluation/change_nodes/N3_mobile_V.csv", "N=3 V", "red",None),
    # ("/home/mace/Documents/pymace/evaluation/change_nodes/N3_mobile_IV.csv", "N=3 IV", "red","--"),

    # Routing Updates
    ("/home/mace/Documents/pymace/evaluation/constant_density/N7_m14_IV.csv", "M=14 IV","red","--"),
    ("/home/mace/Documents/pymace/evaluation/constant_density/N7_m12_IV.csv", "M=12 IV","green","--"),
    ("/home/mace/Documents/pymace/evaluation/constant_density/N7_m10_IV.csv", "M=10 IV","purple","--"),
    ("/home/mace/Documents/pymace/evaluation/constant_density/N7_m9_IV.csv", "M=9 IV","blue","--"),
    ("/home/mace/Documents/pymace/evaluation/change_nodes/N7_mobile_IV.csv", "M=7 IV","black","--"),
    ("/home/mace/Documents/pymace/evaluation/constant_density/N7_m14.csv", "M=14 V","red","-"),
    ("/home/mace/Documents/pymace/evaluation/constant_density/N7_m12.csv", "M=12 V","green","-"),
    ("/home/mace/Documents/pymace/evaluation/constant_density/N7_m10.csv", "M=10 V","purple","-"),
    ("/home/mace/Documents/pymace/evaluation/constant_density/N7_m9.csv", "M=9 V","blue","-"),
    ("/home/mace/Documents/pymace/evaluation/change_nodes/N7_mobile_V.csv", "M=7 V","black","-"),

    # ("/home/mace/Documents/pymace/evaluation/constant_density/full_IV.csv", "IV","red"),
    # ("/home/mace/Documents/pymace/evaluation/constant_density/full_V.csv", "V","blue"),
    
    # Fixed
    # ("/home/mace/Documents/pymace/evaluation/fixed_all_sight/N3_fixed_IV.csv", "M=3 IV B","red",":"),
    # ("/home/mace/Documents/pymace/evaluation/fixed_far/N3_fixed_IV.csv", "M=3 IV A","red","-"),
    # ("/home/mace/Documents/pymace/evaluation/fixed_all_sight/N3_fixed_V.csv", "M=3 V B","red",":"),
    # ("/home/mace/Documents/pymace/evaluation/fixed_far/N3_fixed_V.csv", "M=3 V A","red","-"),
    # ("/home/mace/Documents/pymace/evaluation/fixed_all_sight/N7_fixed_IV.csv", "M=7 IV B","blue",":"),
    # ("/home/mace/Documents/pymace/evaluation/fixed_far/N7_fixed_IV.csv", "M=7 IV A","blue","-"),
    # ("/home/mace/Documents/pymace/evaluation/fixed_all_sight/N7_fixed_V.csv", "M=7 V B","blue",":"),
    # ("/home/mace/Documents/pymace/evaluation/fixed_far/N7_fixed_V.csv", "M=7 V A","blue","-"),
]

plot_averages(files_with_labels)
