#!/usr/bin/env python3
""" 
Scripts is part of a thesis work about distributed systems 
"""
__author__ = "Bruno Chianca Ferreira"
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Bruno Chianca Ferreira"
__email__ = "brunobcf@gmail.com"

import sys, os, shutil, sys, traceback, time, argparse, subprocess

class MACOverhead():
    def __init__(self, args) -> None:
        self.destination = args.outdir
        if self.destination[-1:] != "/":
            self.destination += "/"
        
        self.servers = [2, 3, 4, 5, 6]
        self.network = "10.0.1."
        self.run()

    def run(self):
        try:
            os.mkdir(self.destination)
        except FileExistsError:
            pass
        except:
            print("Error creating report folder. Is it a valid folder?")
        for server in self.servers:
            r = self.test(server)
            #print("testing " + str(_test) + " ,rep: " + str(i))

    def test(self, server):
        print("running server: " + str(server))
        _server = self.network + str(server)
        command = ['iperf3','-c', _server, '-t 20', '--forceflush', '--logfile', self.destination + "results_" + str(server) + ".txt"]
        try:
            os.remove(self.destination + "results_" + str(server) + ".txt")
            proc = subprocess.call(command)
        except FileNotFoundError:
            proc = subprocess.call(command)
        return(0)

if __name__ == '__main__':  #for main run the main function. This is only run when this main python file is called, not when imported as a class
    print("Reporter - Test MAC Overhead on pyMACE and create figure for report")
    print()
    folders = []
    sorted_folders = []
    parser = argparse.ArgumentParser(description='Options as below')
    parser.add_argument('outdir', type=str, help='Input dir where reports are located')
    arguments = parser.parse_args()

    MACOverhead(arguments)
    sys.exit()
