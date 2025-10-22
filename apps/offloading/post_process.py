#!/usr/bin/env python3
import numpy as np
from matplotlib import pyplot as plt
from matplotlib.pyplot import figure
from matplotlib import ticker as ticker
from matplotlib.ticker import MultipleLocator

import argparse, statistics, warnings, traceback
warnings.filterwarnings("ignore")
import sys, os , operator

from mpl_toolkits.axes_grid1.inset_locator import zoomed_inset_axes
from mpl_toolkits.axes_grid1.inset_locator import mark_inset

class PostProcess ():
    def __init__ (self, indir, type, comp):
        print("Processing reports")
        self.mawindow = 1
        self.folder = indir
        self.report_folder = indir
        self.type = type
        #self.ticks = int(th / 10)
        self.target = ['offload']
        self.subtarget = ['parallel', 'serial']
        files = []
        report_files = []

        for (dirpath, dirnames, filenames) in os.walk(self.folder):
            files.extend(filenames)
            break

        for file in files:
            try:
                file = file.split("_")
                if file[0] in self.target:
                    #print(file[2].split(".")[0][:3])
                    if file[3].split(".")[0][:3] == 'uav':
                        report_files.append(file)
            except:
                traceback.print_exc()

        self.offload = {}
        #print(report_files)
        #sys.exit(1)
        
        for file in report_files:
            if file[0] == 'offload':
                #print(file)
                #self.plot_queue(file, 'Time(s)' , 'Queue Level (MB)' )
                #self.plot_any(file, 'Time(s)' , 'Queue Level (MB)', 'Queue')
                #self.plot_start('Time(s)' , 'Partition size')
                try:
                    if (comp):
                        self.process_composed(file, self.offload)
                    else:
                        self.process_any(file, self.offload)
                except:
                    traceback.print_exc()

        if (comp):
            #print(self.offload["random"]["parallel"][0][1.0])
            self.plot_composed(self.report_folder, 'Offload', self.offload, save=True)
            #sys.exit(1)
        else:
            self.plot_times(self.report_folder, 'Offload', self.offload, save=True)
        #print(self.consensus["node0"])
        #sys.exit()

    def process_composed(self, file, _list):
        partition = {}
        wait = {}
        trans = {}
        compute = {}
        total = {}
        betas = []
        reps = []

        #print(file)
        offloadtype = file[1]
        strategy = file[2]
        #print(offloadtype)
        #print(strategy)
        #print(node)
        #sys.exit(1)

        #node = file[2].split(".")[0]
        file_to_open = '_'.join(file)
        print("Processing file: " + file_to_open)
        _file = open(self.folder + file_to_open,"r").readlines()
        header = _file[0].split(';')
        _file.pop(0)

        old_beta = 0
        rep = 0
        reps.append(rep)
        partition[rep] = {}
        wait[rep] = {}
        trans[rep] = {}
        compute[rep] = {}
        total[rep] = {}

        for id, offload in enumerate(_file):
            #partition.append(offload.split(';')[0])
            beta = float(offload.split(';')[7])

            if beta != old_beta:

                if (beta > old_beta):
                    if rep == 0:
                        betas.append(beta)
                else:
                    rep +=1
                    partition[rep] = {}
                    wait[rep] = {}
                    trans[rep] = {}
                    compute[rep] = {}
                    total[rep] = {}
                    reps.append(rep)
                
                partition[rep][beta] = []
                wait[rep][beta] = []
                trans[rep][beta] = []
                compute[rep][beta] = []
                total[rep][beta] = []
                old_beta = beta


            partition[rep][beta].append(str(id))
            wait[rep][beta].append(float(offload.split(';')[3]))
            trans[rep][beta].append(float(offload.split(';')[4]))
            compute[rep][beta].append(float(offload.split(';')[5]))
            total[rep][beta].append(float(offload.split(';')[6]))

        for rep in reps:
            for beta in betas:
                zipped_lists = zip(partition[rep][beta], wait[rep][beta], trans[rep][beta], compute[rep][beta], total[rep][beta])
                sorted_pairs = sorted(zipped_lists, reverse=True)
                tuples = zip(*sorted_pairs)
                partition[rep][beta], wait[rep][beta], trans[rep][beta], compute[rep][beta], total[rep][beta] = [ list(tuple) for tuple in  tuples] #sorted together

        #print(partition[0].keys())
        #sys.exit(1)
        if strategy not in _list.keys():
            _list[strategy] = {}
        _list[strategy][offloadtype] = total

    def process_any(self, file, _list):
        partition = []
        wait = []
        trans = []
        compute = []
        #print(file)
        offloadtype = file[1]
        #print(offloadtype)
        #print(node)
        #sys.exit(1)

        node = file[2].split(".")[0]
        file_to_open = '_'.join(file)
        print("Processing file: " + file_to_open)
        _file = open(self.folder + file_to_open,"r").readlines()
        header = _file[0].split(';')
        _file.pop(0)

        for id, offload in enumerate(_file):
            #partition.append(offload.split(';')[0])
            partition.append(str(id))
            wait.append(float(offload.split(';')[3]))
            trans.append(float(offload.split(';')[4]))
            compute.append(float(offload.split(';')[5]))
        
        zipped_lists = zip(partition, wait, trans, compute)
        sorted_pairs = sorted(zipped_lists, reverse=True)
        tuples = zip(*sorted_pairs)
        partition, wait, trans, compute = [ list(tuple) for tuple in  tuples] #sorted together


        _list[offloadtype] = [partition, wait, trans, compute]

    def plot_start(self,xlab, ylab):
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
        self.fig = plt.figure(figsize=(6, 6), dpi=150)
        self.ax = self.fig.add_subplot(111)
        self.ax.yaxis.set_ticks_position('both')
        self.ax.xaxis.set_ticks_position('both')
        #self.ax2 = self.ax1.twinx()
        self.ax.yaxis.set_major_formatter(ticker.StrMethodFormatter('{x:.2f}'))
        self.ax.set_xlabel(xlab, fontsize=21)
        self.ax.set_ylabel(ylab, fontsize=21)
        #self.ax2.set_ylabel(y2lab, fontsize=21)
        self.ax.grid(color = '#555555', linestyle = '--', linewidth = 0.4, which='major', alpha=0.8)
        #self.ax2.grid(color = '#555555', linestyle = '--', linewidth = 0.4, which='major', alpha=0.8)
        #ax.xaxis.set_minor_locator(MultipleLocator(5))
        #ax.yaxis.set_minor_locator(MultipleLocator(25))
        self.fig.tight_layout()

    def plot_composed(self, exp, figfile, plot_data, title='Nice Tittle', save=False):
        fig, ax = plt.subplots(1, figsize=(7, 4))
        ax.set_xlabel('$\\beta$' + '(ops/byte)')
        ax.set_ylabel('Turn around time(s)')
        ax.grid(True)
        markers = ["^", "<", ">", "*", "o", "x"]
        m = 0

         # location for the zoomed portion 
        sub_axes = plt.axes([.2, .55, .25, .25])
        #sub_axes_2 = plt.axes([.62, .18, .25, .25]) 

        for strategy, sdata in sorted(plot_data.items()):
            #print(strategy)
            for off_type, odata in sorted(sdata.items()):
                #print(off_type)
                totals = []
                betas = []

                for rep, rdata in odata.items():
                    #totals[rep] = []
                    _totals = []

                    for beta, bdata in rdata.items():
                        if beta not in betas:
                            betas.append(beta)
                        _totals.append(max(bdata))
                    totals.append(_totals)
                #print(betas)
                #print(totals)
                mean_totals = list(map(statistics.mean, zip(*totals)))
                std_totals = list(map(statistics.stdev, zip(*totals)))
                X_detail = [1,2,5]
                X_detail_2 = [150]

                y_mean_detail = mean_totals[0:3]
                y_std_detail = std_totals[0:3]
                
                y_mean_detail_2 = mean_totals[-1:]
                y_std_detail_2 = std_totals[-1:]
                ax.errorbar(betas, mean_totals, yerr=std_totals,label = off_type.capitalize() +" & " + strategy.capitalize(), linewidth=1.0, marker=markers[m])
                sub_axes.errorbar(X_detail, y_mean_detail, yerr=y_std_detail,label = off_type.capitalize() +" & " + strategy.capitalize(), linewidth=1.0, marker=markers[m])
                #sub_axes_2.errorbar(X_detail_2, y_mean_detail_2, yerr=y_std_detail_2,label = off_type +" & " + strategy, linewidth=1.0, marker=markers[m])

                m+=1
        ax.legend(bbox_to_anchor=(1.1, 1.16), ncol=3, frameon=False)
        plt.savefig(exp + "/" + figfile + '_' + '.' + self.type)
        plt.close()

        #plt.show()
            #print(mean_totals)
                        
        sys.exit(1)
        for offloadtype, data in plot_data.items():
            fig, ax = plt.subplots(1, figsize=(7, 4))
            left = 0
            ax.barh(data[0], data[1], height=.75, color='#1D2F6F',label='Waiting time')
            ax.barh(data[0], data[2], height=.75, left=data[1], color='#8390FA',label='Transfer time')
            ax.barh(data[0], data[3], height=.75, left=[sum(x) for x in zip(data[1], data[2])], color='#0000BB',label='Compute time')
            ax.set_yticks(data[0]) 
            ax.set_xlabel('Runtime(s)')
            ax.set_ylabel('Task Partition')
            #ax.set_title(offloadtype + ' offload', loc='left')
            ax.grid(True)
            
            ax.legend(bbox_to_anchor=(1, 1.1), ncol=3, frameon=False)
            # remove spines
            #ax.spines['right'].set_visible(False)
            #ax.spines['left'].set_visible(False)
            #ax.spines['top'].set_visible(False)
            #ax.spines['bottom'].set_visible(False)
            plt.tight_layout()
            plt.savefig(exp + "/" + figfile + '_' + offloadtype + '.' + self.type)
            plt.close()

        #sys.exit(1)
        self.fig.savefig(exp + "/" + figfile + '_flatten.' + self.type)
        plt.close()

    def plot_times(self, exp, figfile, plot_data, title='Nice Tittle', save=False):

        for offloadtype, data in plot_data.items():
            fig, ax = plt.subplots(1, figsize=(7, 4))
            left = 0
            ax.barh(data[0], data[1], height=.75, color='#1D2F6F',label='Waiting time')
            ax.barh(data[0], data[2], height=.75, left=data[1], color='#8390FA',label='Transfer time')
            ax.barh(data[0], data[3], height=.75, left=[sum(x) for x in zip(data[1], data[2])], color='#0000BB',label='Compute time')
            ax.set_yticks(data[0]) 
            ax.set_xlabel('Runtime(s)')
            ax.set_ylabel('Task Partition')
            #ax.set_title(offloadtype + ' offload', loc='left')
            ax.grid(True)
            
            ax.legend(bbox_to_anchor=(1, 1.1), ncol=3, frameon=False)
            # remove spines
            #ax.spines['right'].set_visible(False)
            #ax.spines['left'].set_visible(False)
            #ax.spines['top'].set_visible(False)
            #ax.spines['bottom'].set_visible(False)
            plt.tight_layout()
            plt.savefig(exp + "/" + figfile + '_' + offloadtype + '.' + self.type)
            plt.close()

        #sys.exit(1)
        self.fig.savefig(exp + "/" + figfile + '_flatten.' + self.type)
        plt.close()
     
    def movingaverage(self,interval, window_size):
        window = np.ones(int(window_size))/float(window_size)
        return np.convolve(interval, window, 'same')

if __name__ == '__main__':  #for main run the main function. This is only run when this main python file is called, not when imported as a class
    print("Postprocessor for swarm offload")
    print()

    
    parser = argparse.ArgumentParser(description='Options as below')
    parser.add_argument('indir', type=str, help='Input dir where reports are located')
    parser.add_argument('-t','--type', type=str, help='type of figure', default="png", choices=['png', 'eps'])
    parser.add_argument('-c','--composed',help='Composed plot', dest='comp', action='store_true')
    arguments = parser.parse_args()

    PostProcess(arguments.indir,
                arguments.type,
                arguments.comp)

    sys.exit()