#!/usr/bin/env python3
""" 
Report scripts is part of a thesis work about distributed systems 
"""
__author__ = "Bruno Chianca Ferreira"
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Bruno Chianca Ferreira"
__email__ = "brunobcf@gmail.com"

# TODO #
# Remove / from the end of indir

libnames = ['pandas']
import sys
for libname in libnames:
    try:
        lib = __import__(libname)
    except:
        print (sys.exc_info())
    else:
        globals()[libname] = lib

import os, shutil, sys, traceback, time, argparse, statistics, subprocess
from matplotlib import markers, pyplot as plt
from matplotlib import ticker as ticker
import numpy as np
pd = pandas

class MACOverhead():
    def __init__(self, args) -> None:
        self.report_folder = '/home/mace/temp/node0/' #TODO: remove
        self.destination = args.outdir
        self.original_destination = args.outdir
        if self.destination[-1:] != "/":
            self.destination += "/"

        if self.original_destination[-1:] != "/":
            self.original_destination += "/"
        
        self.scenario_file = 'mac_test.json'
        
        #self.run(args.repeat)
        self.start_plot("number of hops", "Throughput (MB/s)")
        data = self.report()
        self.plot_composed(data)
        #self.plot_scatter(data)


    def start_plot(self, xlab, ylab, y2lab="not used"):
        plt.style.use('default')
        plt.rcParams['text.usetex'] = False
        plt.rcParams['axes.linewidth'] = 0.8
        plt.rcParams['font.size'] = 15
        plt.rcParams['xtick.direction'] = 'in'
        plt.rcParams['ytick.direction'] = 'in'
        plt.rcParams['xtick.major.size'] = 5.0
        plt.rcParams['xtick.minor.size'] = 3.0
        plt.rcParams['ytick.major.size'] = 5.0
        plt.rcParams['ytick.minor.size'] = 3.0
        plt.ylabel(ylab, fontsize=20)
        plt.xlabel(xlab, fontsize=20)
        plt.yticks(fontsize=17)
        plt.xticks(fontsize=17)
        self.fig, self.ax1 = plt.subplots(figsize=(6, 6), dpi=150)
        self.ax1.yaxis.set_ticks_position('both')
        self.ax1.xaxis.set_ticks_position('both')
        self.ax1.yaxis.set_major_formatter(ticker.StrMethodFormatter('{x:.2f}'))
        self.ax1.set_ylim([0,25])
        self.ax1.set_xlabel(xlab, fontsize=21)
        self.ax1.set_ylabel(ylab, fontsize=21)
        self.ax1.grid(color = '#555555', linestyle = '--', linewidth = 0.4, which='major', alpha=0.8)
        #ax.xaxis.set_minor_locator(MultipleLocator(5))
        #ax.yaxis.set_minor_locator(MultipleLocator(25))
        self.fig.tight_layout()

    def plot_scatter(self, data):
        markers = ["+", "^", "*", "o"]
        current_marker = 0
        for exp in data.keys():
            lat = []
            mean = []
            tp = []
            for rep in data[exp].keys():
                _tp = []
                _lat = []
                _mean = []
                for clients, _data in sorted(data[exp][rep].items()):
                    _tp.append(_data['throughput'])
                    _lat.append(_data['median_latency'])
                    _mean.append(_data['mean_latency'])

                tp.append(_tp)
                lat.append(_lat)
                mean.append(_mean)
            try:
                average_tp = list(map(statistics.median, zip(*tp)))
                average_lat = list(map(statistics.median, zip(*lat)))
                
                self.ax1.scatter(average_tp, average_lat, marker=markers[current_marker], color="black",label = exp)

                if current_marker < len(markers)-1:
                    current_marker += 1
                else:
                    current_marker = 0
                #self.ax1.plot(average_tp, average_mean, label = exp,linewidth=2.0)
            except:
                print(tp)
                average_tp = tp[0]
                average_lat = lat[0]

                self.ax1.scatter(average_tp, average_lat, marker=markers[current_marker], color="black",label = exp)

                if current_marker < len(markers)-1:
                    current_marker += 1
                else:
                    current_marker = 0
        plt.legend()
        #plt.show()
        self.fig.savefig("plot.png")

    def plot_composed(self, data):
        markers = ["*", "o", "+", "."]
        current_marker = 0
        for exp in data.keys():
            tp = []
            tps= []
            for rep in data[exp].keys():
                _tp = []
                _tps = []
                for clients, _data in sorted(data[exp][rep].items()):
                    pass 
                    _tp.append(_data['throughput'])
                    _tps.append(_data['throughputs'])

                tp.append(_tp)
                tps.append(_tps)

            #averate_tp = statistics.mean(tp)
            #averate_tp = statistics.mean(lat)
            try:
                average_tp = list(map(statistics.mean, zip(*tp)))
                std_tp = list(map(statistics.mean, zip(*tps)))
                #self.ax1.plot(average_tp, average_lat, label = exp,linewidth=2.0)
                self.ax1.errorbar([1,2,3,4,5], average_tp, yerr=std_tp,label = exp, linewidth=1.0, marker=markers[current_marker], color="black")
                if current_marker < len(markers)-1:
                    current_marker += 1
                else:
                    current_marker = 0
                #self.ax1.plot(average_tp, average_mean, label = exp,linewidth=2.0)
            except:
                average_tp = tp[0]
                std_tp = tp[0]
                self.ax1.errorbar(average_tp, [1,2,3,4,5], yerr=std_tp,label = exp, linewidth=1.0, marker=markers[current_marker], color="black")
                if current_marker < len(markers)-1:
                    current_marker += 1
                else:
                    current_marker = 0

        #plt.legend()
        #plt.show()
        self.fig.savefig("mac_overhead.png")

    def report(self):
        """_summary_
        """
        experiment_types = []
        experiments = {}
        experiments_data = {}

        #get all experiments
        for (dirpath, dirnames, filenames) in os.walk(self.original_destination):
            experiment_types.extend(dirnames)
            break

        #get all repetitions per experiment
        for exp in experiment_types:
            repetitions = []
            for (dirpath, dirnames, filenames) in os.walk(self.original_destination + exp):
                repetitions.extend(dirnames)
                break
            experiments[exp] = repetitions

        for experiment in experiments.keys():
            experiments_data[experiment] = {}
            for rep in experiments[experiment]:
                #experiments_data[experiment][rep] = []
                try:
                    data = self.get_data(experiment,rep)
                    experiments_data[experiment][rep] = (data)
                except:
                    traceback.print_exc()
                    print("Error getting data, maybe this report file is malformed: " + experiment + ", " + str(rep))
        return(experiments_data)

    def get_data(self, experiment, rep):
        """_summary_

        Args:
            experiment (_type_): _description_
            rep (_type_): _description_
        """
        files = []
        data = {}
        for (dirpath, dirnames, filenames) in os.walk(self.original_destination + experiment + "/" + rep):
            files.extend(filenames)
            break
        #print(files)
        for file in files:
            #print(file)
            number_clients = int(file.split(".")[0].split("_")[-1:][0])
            _data_file = open(self.original_destination + experiment + "/" + rep + "/" + file, "r").readlines()
            if (len(_data_file) > 0 ):
                #process
                data[number_clients] = {}
                data[number_clients]["throughput"] = 0
                data[number_clients]["throughputs"] = 0
                tp = []
                for line in _data_file:
                    dataline = line.split("Mbits/sec")
                    try:
                        if dataline[0].split(" ")[5] != "0.00-20.00":
                            try: 
                                tp.append(float(dataline[0].split(" ")[:-1][-1:][0]))
                            except:
                                pass
                    except:
                        pass
                data[number_clients]["throughput"] = statistics.median(tp)
                data[number_clients]["throughputs"] = statistics.stdev(tp)
            else:
                data[number_clients] = {}
                data[number_clients]["throughput"] = []

        return (data)
    
    def run(self, reps):
        try:
            os.mkdir(self.destination)
        except FileExistsError:
            pass
        except:
            print("Error creating report folder. Is it a valid folder?")
        for i in range(0,reps):
            try:
                os.mkdir(self.destination + str(i))
            except FileExistsError:
                pass
            except:
                print("Error creating report folder. Is it a valid folder?")

            temp = []
            r = self.test()
            #print("testing " + str(_test) + " ,rep: " + str(i))
            if r == 0:
                for (dirpath, dirnames, filenames) in os.walk(self.report_folder):
                    temp.extend(filenames)
                    break
                for file in temp:
                    if file.split(".")[0].split("_")[0] == 'results':
                        print("copying to: " + self.destination)
                        shutil.copy(self.report_folder + file, self.destination + str(i))

    def test(self):
        scenario = 'scenarios/'
        scenario += self.scenario_file
        try:
            test_file = open(scenario, "r") #just to check if exists
            proc = subprocess.call(['sudo','./pymace.py', '-s' + scenario])
        except FileNotFoundError:
            return(-1)
        return(0)

if __name__ == '__main__':  #for main run the main function. This is only run when this main python file is called, not when imported as a class
    print("Reporter - Test MAC Overhead on pyMACE and create figure for report")
    print()
    folders = []
    sorted_folders = []
    parser = argparse.ArgumentParser(description='Options as below')
    parser.add_argument('outdir', type=str, help='Input dir where reports are located')
    parser.add_argument('-r','--repeat', help='how many times to repeate each test', type=int, default=1)
    arguments = parser.parse_args()

    MACOverhead(arguments)
    sys.exit()
