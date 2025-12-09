#include "stats.h"
#include <fstream>

void stats::dump_csv(const std::string &fname) {
    std::lock_guard<std::mutex> lg(mu);
    std::ofstream f(fname);
    f << "msgs_sent,msg_forwarded,msg_received,req_sent,req_received\n";
    f << msgs_sent << "," << msgs_forwarded << "," << msgs_received << "," << req_sent << "," << req_received << "\n";
    f.close();
}
