"""Resolve generated paths at the JSON/shell boundaries, including spaces/quotes."""
import argparse
import json
from pathlib import Path
import shlex


def inject(config, values):
    for node in config["nodes"]:
        commands = []
        for command in node["function"]:
            argv = shlex.split(command)
            if len(argv) != 3 or argv[:2] != ["/bin/bash", "-c"]:
                raise ValueError("expected generated /bin/bash -c command")
            script = argv[2]
            for token, value in values.items():
                script = script.replace(token, shlex.quote(value))
            commands.append(shlex.join(["/bin/bash", "-c", script]))
        node["function"] = commands

    def resolve(value):
        if isinstance(value, dict):
            return {k: resolve(v) for k, v in value.items()}
        if isinstance(value, list):
            return [resolve(v) for v in value]
        if isinstance(value, str) and value in values:
            return values[value]
        return value

    return resolve(config)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("assignments", nargs="+")
    args = parser.parse_args()
    values = dict(item.split("=", 1) for item in args.assignments)
    config = inject(json.loads(args.path.read_text()), values)
    text = json.dumps(config, indent=2)
    if any(token in text for token in ("__CRDT_", "__SPATIAL_", "__EXPERIMENT_CLOCK__")):
        raise ValueError("unresolved scenario token")
    args.path.write_text(text + "\n")


if __name__ == "__main__":
    main()
