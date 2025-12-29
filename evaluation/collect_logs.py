import sys
import shutil
import pathlib

result_dir = pathlib.Path(sys.argv[1])
report_dir = pathlib.Path("/home/mace/git/fork-pymace/reports")

for log in report_dir.glob("node_*.log*"):
    shutil.move(str(log), result_dir / log.name)
