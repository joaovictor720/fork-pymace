def calculate_failure_percentage_with_labels(files_with_labels):
    """
    Calculate the percentage of failed executions and total executions for each labeled data file.

    Args:
        files_with_labels (list of tuples): List of (file_path, label) pairs.

    Returns:
        dict: A dictionary with labels as keys and a tuple (failure percentage, total executions) as values.
    """
    results = {}

    for file_path, label in files_with_labels:
        try:
            with open(file_path, 'r') as file:
                lines = file.readlines()
                total_executions = len(lines)
                failed_executions = sum(1 for line in lines if not line.strip())  # Count empty lines

                if total_executions > 0:
                    failure_percentage = (failed_executions / total_executions) * 100
                else:
                    failure_percentage = 0.0  # Handle edge case of an empty file

                results[label] = (failure_percentage, total_executions)

        except FileNotFoundError:
            print(f"File not found: {file_path}")
            results[label] = (None, 0)  # Mark as None and 0 if the file is missing

    return results


def print_failure_rates_with_labels(results):
    """
    Print the failure rates and number of executions with their labels in a readable format.

    Args:
        results (dict): Dictionary with labels as keys and tuples (failure percentage, total executions) as values.
    """
    print("Failure Rates and Total Executions:")
    for label, (failure_rate, total_executions) in results.items():
        if failure_rate is not None:
            print(f"{label}\t | {failure_rate:.2f}%\t | {total_executions}")
        else:
            print(f"Label: {label} | Status: File Not Found | Total Executions: {total_executions}")


# Example usage
files_with_labels = [
    ("/home/mace/Documents/pymace/evaluation/constant_density/full_IV.csv", "Full IV"),
    ("/home/mace/Documents/pymace/evaluation/constant_density/full_V.csv", "Full V"),

    ("/home/mace/Documents/pymace/evaluation/constant_density/N7_m14_IV.csv", "M=14 IV"),
    ("/home/mace/Documents/pymace/evaluation/constant_density/N7_m12_IV.csv", "M=12 IV"),
    ("/home/mace/Documents/pymace/evaluation/constant_density/N7_m10_IV.csv", "M=10 IV"),
    ("/home/mace/Documents/pymace/evaluation/constant_density/N7_m9_IV.csv", "M=9 IV"),
    ("/home/mace/Documents/pymace/evaluation/change_nodes/N7_mobile_IV.csv", "M=7 IV"),
    ("/home/mace/Documents/pymace/evaluation/constant_density/N7_m14.csv", "M=14 V"),
    ("/home/mace/Documents/pymace/evaluation/constant_density/N7_m12.csv", "M=12 V"),
    ("/home/mace/Documents/pymace/evaluation/constant_density/N7_m10.csv", "M=10 V"),
    ("/home/mace/Documents/pymace/evaluation/constant_density/N7_m9.csv", "M=9 V"),
    ("/home/mace/Documents/pymace/evaluation/change_nodes/N7_mobile_V.csv", "M=7 V"),

]

results = calculate_failure_percentage_with_labels(files_with_labels)
print_failure_rates_with_labels(results)
