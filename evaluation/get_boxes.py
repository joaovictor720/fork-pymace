import re
from collections import defaultdict
import matplotlib.pyplot as plt
import itertools

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

    # Compute medians for each concurrency value
    medians_by_concurrency = []
    throughputs_by_concurrency = []

    for concurrency, values in data_by_concurrency.items():
        throughputs = [v[0] for v in values]
        medians = [v[1] for v in values]

        # For each concurrency, calculate the average of the medians
        avg_median = round(sum(medians) / len(medians),1)
        medians_by_concurrency.append(avg_median)
        throughputs_by_concurrency.append(throughputs)

    # Sort by concurrency for the plotting order
    return sorted(zip(medians_by_concurrency, throughputs_by_concurrency), key=lambda x: x[0])

def plot_averages(files_with_labels):
    """
    Plot horizontal box plots of throughput for each concurrency sorted by median.
    
    Args:
        files_with_labels (list of tuples): List of (file_path, label) pairs.
    """
    # List of colors to use for different files
    colors = itertools.cycle(['b', 'g', 'r', 'c', 'm', 'y', 'k', 'orange', 'purple', 'brown', 'pink', 'gray', 'olive', 'teal', 'lime'])

    plt.figure(figsize=(12, 8))
    plt.title('Throughput (req/s) vs Median (ms) for Each Concurrency')
    plt.xlabel('Throughput (req/s)')
    plt.ylabel('Median (ms)')
    
    # Store median values for y-axis
    all_medians = []
    all_throughputs = []

    for file_path, label in files_with_labels:
        sorted_data = process_file(file_path)
        if sorted_data:
            medians, throughputs = zip(*sorted_data)
            all_medians.extend(medians)
            all_throughputs.extend(throughputs)
            
            # Pick a unique color for each file
            color = next(colors)

            # Plot horizontal box plot for throughputs at each median value
            for i, throughput in enumerate(throughputs):
                plt.boxplot(throughput, vert=False, positions=[medians[i]], widths=10, patch_artist=True, 
                            boxprops=dict(facecolor=color, color=color), 
                            flierprops=dict(markerfacecolor=color, marker='o', markersize=6),
                            whiskerprops=dict(color=color), 
                            capprops=dict(color=color))

    # Add a y-axis showing the median values and corresponding box plots
    plt.xticks(rotation=45)
    plt.grid(True)
    plt.tight_layout()
    plt.show()

# Example usage
files_with_labels = [
    ("/home/mace/Documents/pymace/evaluation/N7_mobile_IV.csv", "M=7 IV"),
]

plot_averages(files_with_labels)
