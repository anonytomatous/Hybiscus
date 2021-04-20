import logging
import torch
import argparse
import json
import time
import pandas as pd
import numpy as np
from fault_localization import Ochiai, Crosstab, get_ranks
from hypergraph import HyperGraph
from hAggl import hAggl


def run_Hybiscus(cdf, failing_tests, k=None, linkage='average', mdist_threshold=None,
                 fl_formula=None, output=None):
    logging.info("Processing input data....")

    # Setup Coverage Matrix from DataFrame (coverage_df)
    # - row: test case
    # - column: program components
    tests = cdf.columns.values
    components = cdf.index.values

    for test in failing_tests:
        assert test in tests

    X = torch.tensor(cdf.values.T != 0, dtype=torch.int8) # binarize + consider overflow
    y = torch.tensor([
        t not in failing_tests for t in tests
    ], dtype=torch.bool)

    # filter out test cases that do not cover any program components
    is_valid_test = (X > 0).any(dim=1)
    X, y = X[is_valid_test], y[is_valid_test]
    tests = tests[is_valid_test]

    # filter out program components that are not covered by any tests
    is_valid_component = (X > 0).any(dim=0)
    X = X[:, is_valid_component]
    components = components[is_valid_component]

    # Construct Hypergraph
    start_time = time.time()
    original_degrees = X.sum(axis=0)
    H = X[~y] # filter out passing_tests
    reduced_degrees = H.sum(axis=0)

    is_valid_component = reduced_degrees >= 1
    H = H[:,is_valid_component] # filter out unnecessary program elements
    f = reduced_degrees[is_valid_component]/original_degrees[is_valid_component]

    HG = HyperGraph(H, f)
    logging.info(f"Hypergraph modeling takes {round(time.time() - start_time, 3)} seconds")

    aggl = hAggl(n_clusters=1 if k is None else k, verbose=True, affinity='nlink', linkage=linkage) # nlink: hdist
    aggl.fit(HG)

    if k is not None:
        logging.info(f"The number of clusters (k) is provided: {k}")
        final_k = k
    elif mdist_threshold is not None:
        logging.info(f"The mdist threshold is provided: {mdist_threshold}")
        for k in range(HG.N, 0, -1):
            mdist = aggl.mdist[HG.N - k] if k > 1 else 1
            logging.info(f"k={k}: mdist is {mdist}")
            if mdist >= mdist_threshold:
                logging.info(f"{mdist} >= {mdist_threshold}! Stop at {k}")
                break
        final_k = k
    else:
        logging.info("Calculating k value.... elbow point of mdist curve")
        # our stopping criterion
        mdist_diff = np.diff([0] + aggl.mdist + [1], 1) # first derivative
        mdist_elbow = mdist_diff.argmax()
        final_k = HG.N - mdist_elbow

    clusters = []
    logging.info("======================= Hybiscus =======================")
    logging.info(f"k: {final_k}")
    labels = aggl.labels[HG.N - final_k]
    for i, c in enumerate(labels.unique()):
        logging.info(f"cluster {i+1}: {tests[~y][labels == c]}")
        clusters.append(tests[~y][labels == c].tolist())
    logging.info("========================================================")

    output_data = {'clusters': clusters}
    if fl_formula is not None:
        output_data['FL_result'] = []
        localizer = fl_formula()
        for failure_cluster in clusters:
            passings = tests[y == 1]
            used_tests = torch.tensor(
                [t in passings or t in failure_cluster for t in tests], dtype=bool)
            localizer.fit(X[used_tests], y[used_tests])
            suspiciousness = list(zip(components, localizer.scores_.tolist()))
            print(localizer.scores_, get_ranks([(c, ) for c in components], localizer.scores_))
            output_data['FL_result'].append(suspiciousness)

    if output is not None:
        with open(output, "w") as json_file:
            json.dump(output_data, json_file, indent=4)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("coverage", type=str)
    parser.add_argument("failing", type=str)
    parser.add_argument("--linkage", "-L", type=str, default="average")
    parser.add_argument("--k", type=int, default=None)
    parser.add_argument("--threshold", "-T", type=float, default=None)
    parser.add_argument("--fl-formula", type=str, default=None)
    parser.add_argument("--output", "-o", type=str, default="output.json")
    args = parser.parse_args()

    if args.threshold is not None:
        assert 0 <= args.threshold <= 1
    if args.fl_formula is not None:
        assert args.fl_formula in ["Ochiai", "Crosstab"]
        fl_formula = eval(args.fl_formula)
    else:
        fl_formula = None

    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)-8s %(message)s', handlers=[
        logging.FileHandler(f'logs/Hybiscus.log', mode='a'),
        logging.StreamHandler()
    ])

    coverage_path = args.coverage
    logging.info(f"Loading coverage data: {coverage_path}")
    cdf = pd.read_pickle(coverage_path) # coverage dataframe

    failing_tests_path = args.failing
    logging.info(f"Loading failing tests data: {failing_tests_path}")
    with open(failing_tests_path, 'r') as f:
        failing_tests = [l.strip() for l in f]

    run_Hybiscus(cdf, failing_tests, k=args.k, linkage=args.linkage,
                 mdist_threshold=args.threshold, fl_formula=fl_formula, output=args.output)