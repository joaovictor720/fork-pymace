#!/usr/bin/env python3

import os, json, copy

uasjson = open("uas.json","r").read()
scenfile = open("scen.json","w")

uas = json.loads(uasjson)

scen = []
for i in range(50):
    new = copy.deepcopy(uas)
    new["name"] = "uas" + str(i+1)
    new["settings"]["_id"] = i
    new["function"] = []
    new["function"].append("/home/mace/Documents/utm/uas_client.py -t " + "uas" + str(i+1))
    scen.append(new)

scenfile.write(json.dumps(scen, indent=2))
scenfile.flush()
scenfile.close()
