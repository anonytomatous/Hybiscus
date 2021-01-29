import networkx as nx                 # pip install networkx
import community as community_louvain # pip install python-louvain
import torch

class SimilarityGraph:
    def __init__(self, sim_matrix, weight_attr='weight'):
        if isinstance(sim_matrix, list):
            sim_matrix = torch.tensor(sim_matrix)

        assert sim_matrix.shape[0] == sim_matrix.shape[1]

        N = sim_matrix.shape[0] # the number of nodes
        G = nx.Graph()
        G.add_nodes_from(range(0, N))
        G.add_edges_from([
            (i, j, {weight_attr: sim_matrix[i,j].item()})
            for i in range(N) for j in range(N) if i < j
            if sim_matrix[i,j].item() > 0
        ])
        self.N = N
        self.G = G
        self.weight_attr = weight_attr

    def labels_to_community(self, labels):
        # ex)
        # labels:      [0, 1, 1]
        # uniq_labels: [0, 1]
        # communities: [{0}, {1, 2}]
        assert len(labels) == self.N
        uniq_labels = list(sorted(set(labels)))
        communities = {l: set() for l in uniq_labels}
        for i, label in enumerate(labels):
            communities[label].add(i)
        return list(communities.values())

    def labels_to_partition(self, labels):
        # ex)
        # labels:      [0, 1, 1]
        # partition: {0: 0, 1: 1, 1: 1}
        assert len(labels) == self.N
        return {
            i: label
            for i, label in enumerate(labels)
        }

    def modularity(self, labels):
        try:
            return community_louvain.modularity(
                self.labels_to_partition(labels),
                self.G, weight=self.weight_attr
            )
        except ValueError:
            return float('nan')

    def run_louvain(self):
        partition = community_louvain.best_partition(self.G, weight=self.weight_attr)
        labels = [partition[i] for i in range(self.N)]
        return labels

if __name__ == "__main__":
    G = SimilarityGraph(torch.tensor([
        [1.5000, 0.2500, 0.2500],
        [0.2500, 0.7500, 0.5000],
        [0.2500, 0.5000, 0.7500]]))
    print(G.modularity([0, 1, 1]))

    best_partition = G.run_louvain()

    print(G.run_louvain())