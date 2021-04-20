import torch
import math
import random
import logging
import time
import numpy as np
from fault_localization import Crosstab
from scipy.stats import rankdata

# device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
device = 'cpu'

class MSeer:
    def __init__(self, only_suspicious=False):
        self.only_suspicious = only_suspicious

    def fit(self, X, y, distance_matrix=None):
        """
        X: (num_tests, num_components)
        y: (num_tests)
        """
        X = X.to(device)
        y = y.to(device)

        failing_tests = torch.where(y == 0)[0].tolist()
        Nf = len(failing_tests)

        if distance_matrix is None:
            dist_calc_start_time = time.time()
            if self.only_suspicious:
                X = X[:, X[y == 0].type(torch.bool).any(axis=0)]
            else:
                X = X[:, X.type(torch.bool).any(axis=0)]

            crosstab = Crosstab()
            rankings = []
            for ft in failing_tests:
                valid_tests = (y == 1)
                valid_tests[ft] = 1
                scores = crosstab.fit_predict(X[valid_tests], y[valid_tests])
                scores = scores.to('cpu')
                ranking = rankdata(scores*-1, method='ordinal')
                rankings.append(torch.tensor(ranking, dtype=torch.int64).to(device))

            self.distance_matrix = torch.zeros((Nf, Nf))
            for i, ft1 in enumerate(failing_tests):
                for j, ft2 in enumerate(failing_tests):
                    if not ft1 < ft2:
                        continue
                    start_time = time.time()
                    dist = self.revised_kendall_tau(rankings[i], rankings[j])
                    logging.debug('time for distance calc: {}'.format(time.time() - start_time))
                    # print(ft1, ft2, dist)
                    self.distance_matrix[i, j], self.distance_matrix[j, i] = dist, dist
            
            dist_calc_duration = time.time() - dist_calc_start_time
            logging.debug('total elapsed time: {}'.format(dist_calc_duration))
            self.dist_calculation_cost = dist_calc_duration
            self.distance_matrix = self.distance_matrix.to(device)
        else:
            self.distance_matrix = distance_matrix
            self.dist_calculation_cost = 0

        # print(self.distance_matrix)
        psi = self.winsorized_mean(
                self.distance_matrix[torch.triu_indices(Nf, 1)],
                percentage=0.05
        )/2
        #print("psi", psi)
        if psi != 0:
            alpha = 4/(psi**2)
            #print("alpha", alpha)
            potential_values = torch.zeros((Nf)).to(device)
            for i in range(Nf):
                for j in range(Nf):
                    potential_values[i] += torch.exp(-alpha * self.distance_matrix[i, j].pow(2))
                # print(i, potential_values[i])
            theta = 0
            # print(f"theta: {theta}", potential_values)

            M = []
            R = []
            medoids = set()
            while True:
                M.append(potential_values.max())
                R.append(random.choice(
                    torch.where(potential_values == M[theta])[0].tolist()))
                # print(M[theta], R[theta])
                if theta == 0:
                    # print(f"Accept {R[theta]}")
                    medoids.add(R[theta])
                else:
                    # stopping criterion
                    if M[theta] > 0.5 * M[0]:
                        #print(f"Accept {R[theta]}")
                        medoids.add(R[theta])
                    elif M[theta] < 0.15 * M[0]:
                        # print(f"Reject {R[theta]}")
                        break
                    else:
                        Dmin = self.distance_matrix[R[theta]][list(medoids)].min()
                        if Dmin/psi + M[theta]/M[0] >= 1:
                            # print(f"Accept {R[theta]}")
                            medoids.add(R[theta])
                        else:
                            potential_values[potential_values == M[theta]] = 0
                            M = M[:-1]
                            R = R[:-1]
                            # print(f"Repeat {theta}")
                            continue
                zeta = 1.5 * psi
                beta = 4/zeta**2
                for i in range(Nf):
                    potential_values[i] = potential_values[i] - M[theta] \
                        * torch.exp(-beta * self.distance_matrix[i, R[theta]]**2)
                theta += 1
                # print(f"theta: {theta}", potential_values)
        else:
            medoids = set([random.choice(range(0, Nf))])

        # print(medoids)
        # Start K-medoids
        K = len(medoids)

        while True:
            medoids = list(sorted(list(medoids)))

            # cluster assignment
            labels = torch.zeros(Nf, dtype=torch.uint8).to(device)
            for c, m in enumerate(medoids):
                labels[m] = c
            for i in range(Nf):
                if i not in medoids:
                    # print(i, medoids)
                    min_dist = torch.min(self.distance_matrix[i][medoids])
                    cluster = random.choice(
                        torch.where(self.distance_matrix[i][medoids] == min_dist)[0].tolist())
                    labels[i] = cluster

            # medoid update
            new_medoids = []
            for c, m in enumerate(medoids):
                medoid_dist = self.distance_matrix[m][labels==c].sum()
                new_medoid, min_dist = m, medoid_dist
                for i in torch.where(labels==c)[0]:
                    if i == c:
                        continue
                    dist = self.distance_matrix[i][labels==c].sum()
                    if dist < min_dist:
                        min_dist = dist
                        new_medoid = int(i)
                new_medoids.append(new_medoid)
            if new_medoids == medoids:
                break
            medoids = new_medoids
        self.labels_ = labels.tolist()

    def fit_predict(self, X, y, distance_matrix=None):
        self.fit(X, y, distance_matrix)
        return self.labels_

    def winsorized_mean(self, x, percentage=0.05):
        n = x.shape[0]
        x, _ = torch.sort(x)
        trim_length = math.ceil(n * percentage)
        if trim_length * 2 < len(x):
            x[:trim_length] = x[trim_length]
            x[-trim_length:] = x[-trim_length-1]
        return torch.mean(x)

    def revised_kendall_tau(self, x, y):
        """
        MSeer uses a revised Kendall tau distance to
        measure the distance between two failing test cases (i.e., two
        suspiciousness rankings). The revised version gives greater
        weight to more suspicious statements and smaller weight to
        less suspicious statements. (From MSeer paper)
        """
        assert x.shape[0] == y.shape[0]
        N = x.shape[0]

        if device == 'cpu':
            x_diff = (x.reshape(N, -1) - x.reshape(-1, N)) > 0
            y_diff = (y.reshape(N, -1) - y.reshape(-1, N)) > 0

        else:
            x_diff = torch.zeros((N, N), dtype=torch.bool).to(device)
            for i in range(N):
                x_diff[i] = x[i] - x > 0

            y_diff = torch.zeros((N, N), dtype=torch.bool).to(device)
            for i in range(N):
                y_diff[i] = y[i] - y > 0

        i, j = torch.where(torch.triu(x_diff ^ y_diff, 1))
        inversed = 1/x + 1/y
        dist = inversed[i].sum() + inversed[j].sum()
        logging.debug(dist)
        return dist

# At the same time, since CBT only employs the coverage information of each statement,
# it does not require any additional instrumentation of code to evaluate predicate outcomes

if __name__ == "__main__":
    X = torch.tensor([
        [1, 1, 0, 0, 1, 1],
        [1, 1, 0, 0, 1, 1],
        [0, 1, 1, 0, 1, 0],
        [1, 0, 0, 1, 0, 0],
        [0, 1, 0, 1, 1, 0],
    ], dtype=torch.float32)
    y = torch.tensor([1, 1, 0, 0, 0], dtype=torch.bool)
    clustering = MSeer()
    clustering.fit(X, y)
    print(clustering.distance_matrix)
    print(clustering.labels_)
