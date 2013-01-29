#!/usr/local/bin/python2.7

from __future__ import print_function

try:
    import networkx as nx
except ImportError as IE:
    raise IE

def create_installItem_graph(item_map):
    retVal = nx.DiGraph()
    for item in item_map:
        for dependant in item_map[item].depends:
            retVal.add_edge(item_map[item].guid, dependant)
    return retVal

def find_cycles(item_graph):
    retVal = nx.simple_cycles(item_graph)
    return retVal

def find_leafs(item_graph):
    retVal = list()
    for node in sorted(item_graph):
        neig = item_graph.neighbors(node)
        if not neig:
            retVal.append(node)
    return retVal
