import torch
import logging
from scipy.spatial.distance import pdist, squareform
from hypergraph import HyperGraph

class hAggl:
    def __init__(self, n_clusters=3, affinity="nlink", linkage="average",
                 distance_threshold=None, verbose=False):
        assert n_clusters > 0
        assert verbose in [True, False]
        assert affinity in ["nlink", "hncut", "jaccard", "hamming", "euclidean", "dice", "cosine", "precomputed"]
        assert linkage in ["complete", "average", "single"] #, "ward"]

        self.n_clusters = n_clusters
        self.verbose = verbose

        # Define how to calculate the distance between nodes
        self.affinity = affinity
        # Define how to calculate the inter-cluster distance
        self.linkage = linkage

        self.distance_threshold = distance_threshold
        self.mdist = None
        self.labels = None

        # labels_: ndarray of shape (n_samples,)
        # the final label
        self.labels_ = None

    def distance_matrix(self, HG):
        if self.affinity == 'nlink':
            # if affinitiy is nlinks, the distances between nodes are calculated as (1 - nlinks)
            return 1 - HG.L
        elif self.affinity == 'hncut':
            return 1 - HG.P/2
        elif self.affinity == 'jaccard':
            return torch.tensor(squareform(pdist(HG.A, 'jaccard')))
        elif self.affinity == 'hamming':
            return torch.tensor(squareform(pdist(HG.A, 'hamming')))
        elif self.affinity == 'euclidean':
            return torch.tensor(squareform(pdist(HG.A, 'euclidean')))
        elif self.affinity == 'dice':
            return torch.tensor(squareform(pdist(HG.A, 'dice')))
        elif self.affinity == 'cosine':
            return torch.tensor(squareform(pdist(HG.A, 'cosine')))
        raise Exception(f'Cannot compute the distance matrix of {affinity}')

    def fit(self, HG: HyperGraph, distance_matrix=None):
        # Initialize minimum distances and labels history
        self.mdist = []
        self.labels = []

        if self.affinity == 'precomputed':
            assert distance_matrix is not None
            node_distance = distance_matrix
        else:
            node_distance = self.distance_matrix(HG)

        #print(node_distance)
        # Initialize ic_dist (inter-cluster distance) to node_distance
        ic_distance = node_distance.clone().detach()

        for i in range(HG.N):
            ic_distance[i,i] = float("Inf")

        clusters = set(range(HG.N))
        label = torch.tensor(list(range(HG.N)))
        self.labels.append(label)

        while len(clusters) > self.n_clusters:
            label = self.labels[-1]

            # find the minimum distance index
            min_idx = ic_distance.argmin()
            i, j = int(min_idx/HG.N), int(min_idx % HG.N)
            if i > j:
                i, j = j, i
            assert i < j
            min_dist = ic_distance[i,j]
            logging.debug(f"mdist: {min_dist} (k={len(clusters)})")
            if self.distance_threshold is not None and min_dist > self.distance_threshold:
                break
            self.mdist.append(float(min_dist))
            new_label = label.clone().detach()
            new_label[new_label == j] = i
            ic_distance[j, :], ic_distance[:, j] = float("Inf"), float("Inf")

            clusters.remove(j)
            # Update inter-cluster distances
            cluster_i = new_label == i
            for c in clusters:
                if i == c:
                    continue
                cluster_c = (label == c)
                if self.linkage == 'average':
                    new_dist = node_distance[cluster_i, :][:, cluster_c].sum()
                    new_dist /= (cluster_i.sum() * cluster_c.sum())
                elif self.linkage == 'single':
                    new_dist = node_distance[cluster_i, :][:, cluster_c].min()
                elif self.linkage == 'complete':
                    new_dist = node_distance[cluster_i, :][:, cluster_c].max()

                ic_distance[i, c], ic_distance[c, i] = new_dist, new_dist

            self.labels.append(new_label)

        new_idx = {c: i for i, c in enumerate(sorted(clusters))}
        self.labels_ = [new_idx[c.item()] for c in self.labels[-1]]

    def fit_predict(self, HG: HyperGraph):
        self.fit(HG)
        return self.labels_

if __name__ == "__main__":
    A = torch.tensor([
        [1, 1, 1, 0],
        [1, 0, 0, 1],
        [0, 1, 0, 1],
    ], dtype=torch.float32)
    f = torch.tensor([1/2,1/2,1,1], dtype=torch.float32)

    HG = HyperGraph(A, f)
    for v in HG.vertices:
        print(v.index, v.deg)
    for e in HG.hyperedges:
        print(e.index, e.deg)

    h = hAggl(n_clusters=1, affinity='hncut', linkage='complete', verbose=True)
    h.fit(HG)
    print(h.labels)
    print(h.mdist)
    print(h.labels_)

    D = [[1, 1, 0, 0, 1, 1],
        [1, 1, 0, 0, 1, 1],
        [0, 1, 1, 0, 1, 0],
        [1, 0, 0, 1, 0, 0],
        [0, 1, 0, 1, 1, 0]]

    print("Hamming", squareform(pdist(D, 'Hamming')))
    print("Euclidean", squareform(pdist(D, 'Euclidean')))
    print("Dice", squareform(pdist(D, 'Dice')))
    print("Cosine", squareform(pdist(D, 'Cosine')))