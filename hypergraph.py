import torch
from functools import cached_property

class Vertice:
    def __init__(self, HG, index):
        self.HG = HG
        self.index = index
        self.deg = (self.HG.A[self.index, :] * self.HG.f).sum()

    @cached_property
    def neighbors(self):
        _neighbors = self.HG.A.matmul(self.HG.A[self.index].T) > 0
        _neighbors[self.index] = False
        return _neighbors

class HyperEdge:
    def __init__(self, HG, index):
        self.HG = HG
        self.index = index
        self.deg = self.HG.A[:, self.index].sum()

class HyperGraph:
    def __init__(self, A, f):
        """
        - A is an N*M incidence matrix, where N is the number of vertices,
          and M is the number of hyperedges.
        - f is an M dimensional vector of nonnegative weights for the hyperedges.
        """
        assert A.shape[1] == f.shape[0]
        assert (f >= 0).all()
        assert ((A == 0)|(A == 1)).all()

        self.N, self.M = A.shape
        self.A, self.f = A.type(torch.float32), f

        # Initialize vertices and hyperedges
        self.vertices = [Vertice(self, i) for i in range(self.N)]
        self.hyperedges = [HyperEdge(self, j) for j in range(self.M)]

    def Dv(self, pow=1):
        """
        Degree diagonal matrix for vertices
        """
        _Dv = torch.zeros((self.N, self.N))
        for i in range(self.N):
            _Dv[i,i] = self.vertices[i].deg.pow(pow)
        return _Dv

    def De(self, pow=1):
        """
        Degree diagonal matrix for hyperedges
        """
        _De = torch.zeros((self.M, self.M))
        for j in range(self.M):
            _De[j,j] = self.hyperedges[j].deg.pow(pow)
        return _De
    
    def F(self, pow=1):
        """
        Weight diagonal matrix for hyperedges
        """
        _F = torch.zeros((self.M, self.M))
        for j in range(self.M):
            _F[j,j] = self.f[j].pow(pow)
        return _F

    """
    Properties and functions used in hypergraph clustering
    """
    @cached_property
    def Ahat(self):
        """
        Ahat[i,j] is the sum of f(e)/deg(e) for all e that connects i and j
        """
        _Ahat = self.A.matmul(self.F()).matmul(self.De(pow=-1)).matmul(self.A.T)
        return _Ahat

    @cached_property
    def K(self):
        """
        K is the kernel matrix used by the weighted kernel k-means algo.
        """
        inversed_Dv = self.Dv(pow=-1)
        _K = inversed_Dv.matmul(self.Ahat).matmul(inversed_Dv)
        return _K

    @cached_property
    def P(self):
        """
        P is the pairwise hncut (matrix form)
        """
        inversed_Dv = self.Dv(pow=-1)
        _P = inversed_Dv.matmul(self.Ahat) + self.Ahat.matmul(inversed_Dv)
        return _P

    @cached_property
    def L(self):
        """
        L is the normalized linkage matrix
        """
        _N = (self.Ahat * torch.eye(self.N))
        for i in range(self.N):
            _N[i,i] = 1/_N[i,i]
        _L = _N.matmul(self.Ahat) + self.Ahat.matmul(_N)
        return _L/2

    def vol(self, in_cluster):
        return sum([self.vertices[i].deg for i in in_cluster])

    def hcut(self, in_cluster, mask):
        ic_mask = torch.tensor([i in in_cluster for i in range(self.N)], dtype=torch.bool)
        hcut = self.Ahat[ic_mask,:][:,~ic_mask&mask].sum()
        return hcut

    def hncut_from_clusters(self, clusters, mask=None):
        hncut = 0
        if mask is None:
            mask = torch.zeros(self.N, dtype=bool)
            for cluster in clusters:
                for i in cluster:
                    mask[i] = 1   
        for in_cluster in clusters:
            value = self.hcut(in_cluster, mask)/self.vol(in_cluster)
            hncut += value
        return hncut

    def hncut(self, label):
        """
        label is the cluster assignments
        """
        if isinstance(label, list):
            label = torch.tensor(label)
        assert label.shape[0] == self.N
        clusters = label.unique()
        return self.hncut_from_clusters([torch.where(label == c)[0] for c in clusters],
                                        mask=torch.ones(self.N, dtype=bool))

    def pairwise_hncut(self, i, j):
        return self.P[i,j]

    def dist_to_cluster_func(self, in_cluster):
        intra = self.Ahat[in_cluster, :][:, in_cluster].sum()/(self.vol(in_cluster) ** 2)
        def dist_func(i):
            inter = (2*self.Ahat[i, in_cluster].sum())/(self.vertices[i].deg * self.vol(in_cluster))
            return intra - inter
        return dist_func

    def merge_vertices(self, merging_sets):
        new_A = self.A.type(torch.bool)
        # mark vertices that should be deleted
        deleted = torch.zeros(self.N, dtype=torch.bool)
        for merging_set in merging_sets:
            # merge vertices into the vertex that has the minimum index
            i = min(merging_set)
            for j in merging_set:
                if i == j:
                    continue
                # the supernode
                new_A[i] = new_A[i] | new_A[j]
                deleted[j] = True
        # delete the merged nodes
        new_A = new_A[~deleted,:]
        # create new hypergraph with merged vertices
        return self.__class__(new_A.type(torch.float32), self.f)

if __name__ == "__main__":
    A = torch.tensor([
        [1, 1, 0, 0, 1, 1],
        [1, 1, 0, 0, 1, 1],
        [0, 1, 1, 0, 1, 0],
        [1, 0, 0, 1, 0, 0],
        [0, 1, 0, 1, 1, 0],
    ], dtype=torch.float32)
    f = torch.tensor([1,1,1,1,1,1], dtype=torch.float32)

    HG = HyperGraph(A, f)
    for v in HG.vertices:
        print(v.index, v.deg)
    for j in range(HG.M):
        e = HG.hyperedges[j]
        #print(e.index, e.deg)
        print(e.index, HG.f[j]/e.deg)

    print(HG.Ahat)
    print(HG.L)
    print(HG.P)

    A = torch.tensor([
        [0, 1, 1, 0, 1],
        [1, 0, 0, 1, 0],
        [0, 1, 0, 1, 1],
    ], dtype=torch.float32)
    f = torch.tensor([1,1,1,1,1], dtype=torch.float32)

    HG = HyperGraph(A, f)
    for v in HG.vertices:
        print(v.index, v.deg)
    for j in range(HG.M):
        e = HG.hyperedges[j]
        #print(e.index, e.deg)
        print(e.index, HG.f[j]/e.deg)

    print(HG.Ahat)
    print(HG.L)
    print(HG.P)


    A = torch.tensor([
        [0, 1, 1, 0, 1],
        [1, 0, 0, 1, 0],
        [0, 1, 0, 1, 1],
    ], dtype=torch.float32)
    f = torch.tensor([1/3,2/4,1,1,2/4], dtype=torch.float32)

    HG = HyperGraph(A, f)
    for v in HG.vertices:
        print(v.index, v.deg)
    for j in range(HG.M):
        e = HG.hyperedges[j]
        print(e.index, HG.f[j]/e.deg)

    print(HG.Ahat)
    print(HG.L)
    print(HG.P)
