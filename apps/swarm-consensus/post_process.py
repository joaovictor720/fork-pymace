#!/usr/bin/env python3
import numpy as np
from matplotlib import pyplot as plt
from matplotlib.pyplot import figure
from matplotlib import ticker as ticker
from matplotlib.ticker import MultipleLocator

import argparse, statistics, warnings, traceback
warnings.filterwarnings("ignore")
import sys, os , operator

class PostProcess ():
    def __init__ (self, indir, type):
        print("Processing reports")
        self.mawindow = 1
        self.folder = indir
        self.report_folder = indir
        self.type = type
        #self.ticks = int(th / 10)
        self.target = ['consensus']
        files = []
        report_files = []

        for (dirpath, dirnames, filenames) in os.walk(self.folder):
            files.extend(filenames)
            break

        for file in files:
            try:
                file = file.split("_")
                if file[0] in self.target:
                    report_files.append(file)
            except:
                traceback.print_exc()

        self.consensus = {}
        
        for file in report_files:
            if file[0] == 'consensus':
                #print(file)
                #self.plot_queue(file, 'Time(s)' , 'Queue Level (MB)' )
                #self.plot_any(file, 'Time(s)' , 'Queue Level (MB)', 'Queue')
                self.plot_start('Time(s)' , 'Direction (radians)', 'Std. deviation ($\sigma$)')
                self.process_any(file, self.consensus)

        self.plot_composed(self.report_folder, 'Consensus', self.consensus, save=True)
        #print(self.consensus["node0"])
        #sys.exit()


    def process_any(self, file, _list):
        time = []
        measurements = []
        node = file[2].split(".")[0]
        file_to_open = '_'.join(file)
        print("Processing file: " + file_to_open)
        _file = open(self.folder + file_to_open,"r").readlines()
        header = _file[0].split(';')
        _file.pop(0)

        for step in range(len(_file)):
            time.append(float(_file[step].split(';')[0])/1000)
            measurements.append(float(_file[step].split(';')[1]))

        _list["time"] = time
        _list[node] = [time, measurements]

    def plot_start(self,xlab, ylab, y2lab):
        plt.style.use('default')
        #figure(figsize=(6, 6), dpi=150)
        #plt.rcParams['text.usetex'] = True
        plt.rcParams['axes.linewidth'] = 0.8
        plt.rcParams['font.size'] = 15
        plt.rcParams['xtick.direction'] = 'in'
        plt.rcParams['ytick.direction'] = 'in'
        plt.rcParams['xtick.major.size'] = 5.0
        plt.rcParams['xtick.minor.size'] = 3.0
        plt.rcParams['ytick.major.size'] = 5.0
        plt.rcParams['ytick.minor.size'] = 3.0
        #plt.ylabel(ylab, fontsize=20)
        #plt.xlabel(xlab, fontsize=20)
        plt.yticks(fontsize=17)
        plt.xticks(fontsize=17)
        #plt.grid(color = '#DDDDDD', linestyle = '--', linewidth = 0.5, which='major', alpha=1)
        self.fig, self.ax1 = plt.subplots(figsize=(6, 6), dpi=150)
        self.ax1.yaxis.set_ticks_position('both')
        self.ax1.xaxis.set_ticks_position('both')
        self.ax2 = self.ax1.twinx()
        self.ax1.yaxis.set_major_formatter(ticker.StrMethodFormatter('{x:.2f}'))
        self.ax1.set_xlabel(xlab, fontsize=21)
        self.ax1.set_ylabel(ylab, fontsize=21)
        self.ax2.set_ylabel(y2lab, fontsize=21)
        self.ax1.grid(color = '#555555', linestyle = '--', linewidth = 0.4, which='major', alpha=0.8)
        #self.ax2.grid(color = '#555555', linestyle = '--', linewidth = 0.4, which='major', alpha=0.8)
        #ax.xaxis.set_minor_locator(MultipleLocator(5))
        #ax.yaxis.set_minor_locator(MultipleLocator(25))
        self.fig.tight_layout()

    def plot_composed(self, exp, figfile, plot_data, title='Nice Tittle', save=False):

        time = plot_data["time"]
        measurements = []
        for key in sorted(plot_data):
            if key != "time":
                #print(value)
                #sys.exit()
                measurements.append(plot_data[key][1])
                self.ax1.plot(plot_data[key][0], plot_data[key][1], label = key,linewidth=2.0)
        average_nodes = list(map(statistics.mean, zip(*measurements)))
        std_dev_nodes = list(map(statistics.stdev, zip(*measurements)))
        time = time[:len(std_dev_nodes)]
        #cy = next(x for x in std_dev_nodes if x < 0.005)
        #cx = next((i for i, x in enumerate(std_dev_nodes) if x<0.005))
        #cx=time[cx]
        #self.ax2.annotate('$\sigma$<0.05 t=' + str(cx) + 's', xy = (cx,cy), ha="right", xytext=(40,0.1) ,arrowprops=dict(facecolor='black', shrink=0.05),bbox=dict(boxstyle="round4", fc="w"))
        self.ax2.plot(time, std_dev_nodes, label = "mean",linewidth=2.0, linestyle = "--", color='black')
        self.ax1.legend(loc=1)
        #self.ax2.legend()
        if save:
            self.fig.savefig(exp + "/" + figfile + '_flatten.' + self.type)
            plt.close()
            
     
    def movingaverage(self,interval, window_size):
        window = np.ones(int(window_size))/float(window_size)
        return np.convolve(interval, window, 'same')

if __name__ == '__main__':  #for main run the main function. This is only run when this main python file is called, not when imported as a class
    print("Postprocessor for swarm consensus")
    print()

    
    parser = argparse.ArgumentParser(description='Options as below')
    parser.add_argument('indir', type=str, help='Input dir where reports are located')
    parser.add_argument('-t','--type', type=str, help='type of figure', default="png", choices=['png', 'eps'])

    arguments = parser.parse_args()

    PostProcess(arguments.indir,
                arguments.type)

    sys.exit()