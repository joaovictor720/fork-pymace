CXX = g++
CXXFLAGS = -std=c++17 -O2 -pthread

SRC = crdt-replica.cpp /home/mace/git/delta-enabled-crdts/delta-crdts.cc
OUT = crdt-replica

all: $(OUT)

$(OUT): $(SRC)
	$(CXX) $(CXXFLAGS) $(SRC) -o $(OUT)

clean:
	rm -f $(OUT)
