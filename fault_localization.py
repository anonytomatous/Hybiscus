import torch
import numpy as np
import pandas as pd
from abc import ABC, abstractmethod
from scipy.stats import rankdata

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

def ranking(l, method='max'):
    return rankdata(-np.array(l), method=method)

def get_ranks(elements, scores, level=0, method='max'):
  """
  elements: list of tuples
  """
  if scores.is_cuda:
    scores = scores.to('cpu')
  
  if len(elements) > 0:
    max_level = len(elements[0])
    idx = pd.MultiIndex.from_arrays(
      [[t[l] for t in elements] for l in range(max_level)],
      names=[str(l) for l in range(max_level)])
    s = pd.Series(scores, name='scores', index=idx)
    if max_level - 1  == level:
      aggr = s
    else:
      aggr = s.max(level=level)
    return aggr.index.values, ranking(aggr.values, method=method)
  else:
    return []

def get_ranks_of_faulty_elements(elements, scores, faulty_elements, level=0, method='max'):
    elems, ranks = get_ranks(elements, scores, level=level, method=method)
    return {e: ranks[i] for i, e in enumerate(elems) if e in faulty_elements}

class FaultLocalizer(ABC):
    def __init__(self):
        self.scores_ = None

    @abstractmethod
    def fit(self, X, y):
        # need to compute self.scores_
        pass

    def fit_predict(self, X, y):
        self.fit(X, y)
        return self.scores_

class Crosstab(FaultLocalizer):
  # statistics-based fault localization

  def fit(self, X, y):
      # The null hypothesis is
      # “program execution result is independent of the coverage of statement ω.”

      # Under the null hypothesis,
      # the statistic χ2(ω) has an approximately chi-square distribution
      X = X.to(device)
      y = y.to(device)

      N = y.shape[0]
      Nf = (y == 0).sum()                 # the number of failing tests
      Ns = (y == 1).sum()                 # the number of passing tests
      Nc = (X == 1).sum(dim=0)            # the number of tests that cover each elements
      Ncf = (X == 1)[y == 0].sum(dim=0)   # the number of failing tests that cover each elements
      Ncs = (X == 1)[y == 1].sum(dim=0)   # the number of passing tests that cover each elements
      Nu = (X == 0).sum(dim=0)            # the number of tests that do not cover each elements
      Nuf = (X == 0)[y == 0].sum(dim=0)   # the number of failing tests that do not cover each elements
      Nus = (X == 0)[y == 1].sum(dim=0)   # the number of passing tests that do not cover each elements
      assert (Ncf + Nuf == Nf).all()
      assert (Ncs + Nus == Ns).all()
      assert (Ncs + Ncf == Nc).all()
      assert (Nus + Nuf == Nu).all()

      Ecf = Nc * Nf/N
      Ecs = Nc * Ns/N
      Euf = Nu * Nf/N
      Eus = Nu * Ns/N

      chi_sq = (Ncf - Ecf).pow(2)/Ecf
      chi_sq += (Ncs - Ecs).pow(2)/Ecs
      chi_sq += (Nuf - Euf).pow(2)/Euf
      chi_sq += (Nus - Eus).pow(2)/Eus

      M = chi_sq/N

      phi = (Ncf/Nf)/(Ncs/Ns)
      zeta = M
      zeta[phi < 1] *= -1
      zeta[phi == 1] = 0
      self.scores_ = zeta

class Ochiai(FaultLocalizer):
  # spectrum-based fault localization

  def fit(self, X, y):
      X = X.to(device)
      y = y.to(device)
      Ncf = (X == 1)[y == 0].sum(dim=0)   # the number of failing tests that cover each elements
      Ncs = (X == 1)[y == 1].sum(dim=0)   # the number of passing tests that cover each elements
      Nuf = (X == 0)[y == 0].sum(dim=0)   # the number of failing tests that do not cover each elements
      Nus = (X == 0)[y == 1].sum(dim=0)   # the number of passing tests that do not cover each elements
      self.scores_ = Ncf/torch.sqrt(((Ncf + Nuf) * (Ncf + Ncs)).type(torch.float32))


  
if __name__ == "__main__":
  # print(get_ranks([('method A', 1), ('method A', 2), ('method B', 3)], [0.1, 0.2, 0.3], level=0))
  # print(get_ranks_of_faulty_elements([('method A', 1), ('method A', 2), ('method B', 3)], [0.1, 0.2, 0.3], ['method A'], level=0))
  # print(get_ranks([('method A', 1), ('method A', 2), ('method B', 3)], [0.1, 0.2, 0.3], level=1))
  # print(get_ranks_of_faulty_elements([('method A', 1), ('method A', 2), ('method B', 3)], [0.1, 0.2, 0.3], [('method A', 2)], level=1))
  pass