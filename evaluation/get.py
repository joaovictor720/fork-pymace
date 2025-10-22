import re

def extract_values(log_file, output_file):
    # Define a dictionary to hold the extracted values
    extracted_data = {
        "Concurrency": None,
        "Throughput": None,
        "median": None,
        "Write Ratio": None,
        "Number of Keys": None,
        "Benchmark Time": None,
        "size": None,
        "mean": None,
        "min": None,
        "max": None
    }

    # Read the log file and extract the required values
    with open(log_file, 'r') as file:
        log_content = file.read()

    # Define regex patterns for the required values
    patterns = {
        "Concurrency": r"Concurrency\s*=\s*(\d+)",
        "Throughput": r"Throughput\s*=\s*([\d.]+)",
        "median": r"median\s*=\s*([\d.]+)",
        "Write Ratio": r"Write Ratio\s*=\s*([\d.]+)",
        "Number of Keys": r"Number of Keys\s*=\s*(\d+)",
        "Benchmark Time": r"Benchmark Time\s*=\s*([\w.]+)",
        "size": r"size\s*=\s*(\d+)",
        "mean": r"mean\s*=\s*([\d.]+)",
        "min": r"min\s*=\s*([\d.]+)",
        "max": r"max\s*=\s*([\d.]+)"
    }

    # Extract values using regex
    for key, pattern in patterns.items():
        match = re.search(pattern, log_content)
        if match:
            extracted_data[key] = match.group(1)

    # Prepare a single line of output
    output_line = " | ".join(f"{key}: {value}" for key, value in extracted_data.items() if value is not None)

    # Append the output line to the provided output file
    with open(output_file, 'a') as file:
        file.write(output_line + "\n")
    

# Example usage
log_file = "temp/node0/result.txt"  # Replace with the actual log file path
output_file = "/home/mace/Documents/pymace/evaluation/constant_density/N7_m14_IV.csv"  # Replace with the actual output file path
extract_values(log_file, output_file)
print(f"Done :{output_file}")
