import os
import json
import logging
import torch
import argparse
import sys
import time
import pandas as pd
import numpy as np
from fault_localization import Ochiai, Crosstab, get_ranks_of_faulty_elements
from functools import cached_property
from sklearn import metrics
from hypergraph import HyperGraph
from hAggl import hAggl
from MSeer import MSeer
from modularity import SimilarityGraph
from scipy.spatial.distance import pdist, squareform

def sort_clustering_labels(labels):
    map_to_new_index = {}
    for c in labels:
        if c in map_to_new_index:
            continue
        else:
            map_to_new_index[c] = len(map_to_new_index)
    return [map_to_new_index[c] for c in labels]

def find_min_dist_knee(y):
    y[-1] = 1.
    diff = np.diff(np.insert(y, 0, 0.), 1) # first derivative
    return diff.argmax()

class NoSetupCommandError(Exception):
    pass

class NoFailingTestCaseError(Exception):
    pass

class FaultData:
    def __init__(self, data_id, coverage_path, failing_tests, dataset_dir,
                 faulty_components=None, faulty_components_level=None,
                 setup_command=None, cleanup_command=None):
        self.data_id = data_id
        self.cleanup_command = cleanup_command

        if not os.path.isabs(coverage_path):
            coverage_path = os.path.join(dataset_dir, coverage_path)

        for fault, filepath in failing_tests.items():
            if not os.path.isabs(filepath):
                failing_tests[fault] = os.path.join(dataset_dir, filepath)
    
        self.overlapping_failing_test_cases = set() # this is for C subjects

        # Load faults & failing tests information
        self.faults = list(failing_tests.keys()) # the list of fault-ids
        self.test_fault_map = self.load_test_fault_map(failing_tests) # mapping from failing tests to fault indices
        if len(set(self.test_fault_map.values())) != len(self.faults):
            raise NoFailingTestCaseError('No failing TC for some faults')

        if faulty_components:
            for fault, filepath in faulty_components.items():
                if not os.path.isabs(filepath):
                    faulty_components[fault] = os.path.join(dataset_dir, filepath)
            self.cause_fault_map = self.load_cause_fault_map(faulty_components) # mapping from faulty methods to fault indices
        else:
            self.cause_fault_map = None
        self.faulty_components_level = faulty_components_level

        logging.info(f"Loading coverage data of {data_id}...")

        # Load coverage data
        if not os.path.exists(coverage_path):
            if setup_command is None:
                raise NoSetupCommandError()
            os.system(setup_command)
        cdf = pd.read_pickle(coverage_path) # coverage dataframe

        logging.info("Processing coverage data....")

        # Setup Coverage Matrix from DataFrame (coverage_df)
        # - row: test case
        # - column: program components
        tests = cdf.columns.values
        components = cdf.index.values

        X = torch.tensor(cdf.values.T != 0, dtype=torch.int8) # binarize + consider overflow
        y = torch.tensor([
            t not in self.test_fault_map for t in tests
        ], dtype=torch.bool)

        is_overlapping = torch.tensor([t in self.overlapping_failing_test_cases for t in tests], dtype=torch.bool)
        logging.debug(self.overlapping_failing_test_cases)
        logging.debug(is_overlapping)
        assert is_overlapping.sum() == len(self.overlapping_failing_test_cases)
        
        # filter out test cases that do not cover any program components
        # filter out the overlapping failing test cases (for C subjects)
        is_valid_test = (X > 0).any(dim=1) & ~is_overlapping
        X, y = X[is_valid_test], y[is_valid_test]
        tests = tests[is_valid_test]

        # filter out program components that are not covered by any tests
        is_valid_component = (X > 0).any(dim=0)
        X = X[:, is_valid_component]
        components = components[is_valid_component]

        self.X, self.y = X, y
        self.tests, self.components = tests, components

        self.distance_matrices = {}
        self.dist_calculation_cost = {}

    def __del__(self):
        if self.cleanup_command:
            os.system(self.cleanup_command)

    def load_test_fault_map(self, failing_tests):
        """
        return ftmap: dict {failing_test: fault_idx}
        ex)
        {
            'org.apache.commons.lang3.text.translate.LookupTranslatorTest.testLang882': 0,
            'org.apache.commons.lang3.LocaleUtilsTest.testLang865': 1
        }
        """
        ftmap = {}
        for i, fault in enumerate(self.faults):
            with open(failing_tests[fault], 'r') as f:
                for l in f:
                    failing_test = l.strip()
                    if failing_test in ftmap:
                        self.overlapping_failing_test_cases.add(failing_test)
                    ftmap[failing_test] = i
        for failing_test in self.overlapping_failing_test_cases:
            del ftmap[failing_test]
        return ftmap

    def load_cause_fault_map(self, faulty_components):
        """
        return fcmap: dict {faulty_components: fault_idx}
        ex)
        {
            'org.apache.commons.lang3.builder.HashCodeBuilder$register<Ljava/lang/Object;>': 0,
            'org.apache.commons.lang3.builder.HashCodeBuilder$unregister<Ljava/lang/Object;>': 0,
            'org.apache.commons.lang3.builder.HashCodeBuilder$getRegistry<>': 0,
            'org.apache.commons.lang3.builder.HashCodeBuilder$1$initialValue<>': 0,
            'org.apache.commons.lang3.builder.HashCodeBuilder$isRegistered<Ljava/lang/Object;>': 0,
            'org.apache.commons.lang3.ClassUtils$toClass<[Ljava/lang/Object;>': 1
        }
        """
        fcmap = {}
        for i, fault in enumerate(self.faults):
            with open(faulty_components[fault], 'r') as f:
                for l in f:
                    faulty_component = l.strip()
                    if len(faulty_component.split(',')) != 1:
                        faulty_component = tuple([e.strip() for e in faulty_component.split(',')])
                    if faulty_component not in fcmap:
                        fcmap[faulty_component] = list()
                    fcmap[faulty_component].append(i)
        return fcmap

    @cached_property
    def failing_tests(self):
        return self.tests[~self.y].tolist()

    @cached_property
    def faulty_components(self):
        return list(self.cause_fault_map.keys()) if (self.cause_fault_map is not None) else None

    @cached_property
    def root_cause_map(self):
        if self.cause_fault_map is None:
            return None
        # dict (key: fault_index, value: list(faulty_component_index))
        fault_to_root_cause = [[] for fault in self.faults]
        for i, faulty_component in enumerate(self.faulty_components):
            for fault_index in self.cause_fault_map[faulty_component]:
                fault_to_root_cause[fault_index].append(i)
        return fault_to_root_cause

    @cached_property
    def labels_true(self):
        labels_true = [self.test_fault_map[test] for test in self.failing_tests]
        return labels_true

    @cached_property
    def labels_TCN(self):
        labels = []
        test_classes = []
        for test in self.failing_tests:
            test_class = self.__class__.get_test_class(test)
            if test_class not in test_classes:
                test_classes.append(test_class)
            labels.append(test_classes.index(test_class))
        return labels

    def labels_louvain(self, similarity='link', similarity_matrix=None):
        if similarity == 'link':
            similarity_matrix = self.hypergraph.Ahat
        elif similarity == 'nlink':
            similarity_matrix = self.hypergraph.L
        G = SimilarityGraph(similarity_matrix)
        return G.run_louvain()

    def labels_MSeer(self, use_cache=True):
        cached_dist_path = f"resources/MSeer/{data_id}.pt"
        dist = torch.load(cached_dist_path) if use_cache and os.path.exists(cached_dist_path) else None
        mseer = MSeer(only_suspicious=False)
        labels = mseer.fit_predict(
            self.X, self.y,
            distance_matrix=dist
        )
        if dist is None:
            # Revised Kendall tau distance between ranked lists
            torch.save(mseer.distance_matrix, cached_dist_path)
            dist = mseer.distance_matrix
        # normalize
        if dist.max() != 0:
            dist /= dist.max()
        self.distance_matrices["RKT"] = dist
        self.dist_calculation_cost["RKT"] = mseer.dist_calculation_cost

        del mseer
        return labels

    @cached_property
    def hypergraph(self):
        start_time = time.time()
        original_degrees = self.X.sum(axis=0)
        reduce_start_time = time.time()
        H = self.X[~self.y] # filter out passing_tests
        reduced_degrees = H.sum(axis=0)

        is_valid_component = reduced_degrees >= 1
        H = H[:,is_valid_component] # filter out unnecessary program elements
        self.coverage_reduction_cost = time.time() - reduce_start_time
        f = reduced_degrees[is_valid_component]/original_degrees[is_valid_component]

        HG = HyperGraph(H, f)
        self.hypergraph_modeling_cost = time.time() - start_time

        return HG

    def get_distance_matrix(self, affinity='nlink'):
        HG = self.hypergraph
        if affinity == 'nlink':
            return 1 - self.hypergraph.L
        elif affinity == 'hncut':
            return 1 - self.hypergraph.P/2
        elif affinity == 'jaccard':
            return torch.tensor(squareform(pdist(self.hypergraph.A, 'jaccard')))
        elif affinity == 'cosine':
            return torch.tensor(squareform(pdist(self.hypergraph.A, 'cosine')))
        elif affinity == 'dice':
            return torch.tensor(squareform(pdist(self.hypergraph.A, 'dice')))
        elif affinity == 'euclidean':
            return torch.tensor(squareform(pdist(self.hypergraph.A, 'euclidean')))
        elif affinity == 'hamming':
            return torch.tensor(squareform(pdist(self.hypergraph.A, 'hamming')))
        elif affinity == 'RKT': # revised kendall tau
            if "RKT" not in self.distance_matrices:
                self.labels_MSeer() # compute the distance matrix
            return self.distance_matrices["RKT"]

    def localize_faults(self, labels=None, technique=Ochiai):
        localizer = technique()
        if labels is None:
            suspiciousness = localizer.fit_predict(self.X, self.y)
        else:
            # debugging in parallel
            failing_idx = torch.where(~self.y)[0]
            suspiciousness = {}
            for cluster in set(labels):
                used_tests = self.y == 1 # all passing tests
                for i, l in enumerate(labels):
                    if l == cluster:
                        used_tests[failing_idx[i]] = 1
                suspiciousness[cluster] = localizer.fit_predict(self.X[used_tests], self.y[used_tests])
        return suspiciousness

    @staticmethod
    def get_test_class(test_name):
        return '.'.join(test_name.split('.')[:-1])

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=str)
    parser.add_argument("--id", type=str, default=None)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--no-fl-cache", action="store_true")
    parser.add_argument("--fl", action="store_true")
    parser.add_argument("--fl-only-knee", action="store_true")
    args = parser.parse_args()

    if args.id is None:
        dataset_id = os.path.splitext(os.path.basename(args.dataset))[0]
    else:
        dataset_id = args.id

    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)-8s %(message)s', handlers=[
        logging.FileHandler(f'logs/evaluate_{dataset_id}.log', mode='w'),
        logging.StreamHandler()
    ])

    """
    Load dataset config file
    """
    logging.info(f'Started: {dataset_id}')

    with open(args.dataset, 'r') as f:
        dataset = json.load(f)
    dataset_dir = os.path.dirname(os.path.abspath(args.dataset))

    meta_path = f'resources/result/{dataset_id}_meta.pkl'
    result_path = f'resources/result/{dataset_id}_result.pkl'
    if args.fl:
        fl_result_path = f'resources/result/{dataset_id}_fl_result.pkl'
    if args.no_cache:
        dist_cost_path = f'resources/result/{dataset_id}_dist_cost_result.pkl'

    meta_df = pd.DataFrame(columns=[
        'data_id', 'faults', 'num_faults', 'labels_true', 'num_vertices',
        'num_hyperedges', 'num_total_tests', 'num_total_components',
        'failing_tests', 'faulty_components', 'root_cause_map',
        'hypergraph_modeling_cost', 'coverage_reduction_cost'
    ])

    """
    Configuration for Agglomerative Clustering
    """
    k = 1

    AFFINITY = ['nlink', 'hncut', 'jaccard', 'hamming', 'dice', 'cosine', 'euclidean', 'RKT']
    LINKAGE = ['average', 'single', 'complete']

    PREDIST_AFFINITY = ['RKT']

    """
    Configuration for clustering result evaluation
    """
    evaluation_metrics = [
            metrics.adjusted_rand_score,
            metrics.normalized_mutual_info_score,
            metrics.homogeneity_score,
            metrics.completeness_score,
            metrics.v_measure_score
    ]

    """
    Configuration for fault localization
    """
    FL_techniques = [Ochiai, Crosstab]

    """
    Load result files if exist
    """
    if not args.no_cache and os.path.exists(result_path):
        old_result_df = pd.read_pickle(result_path)
    else:
        old_result_df = None

    result_df = pd.DataFrame(
        columns=[
            'data_id', 'clustering', 'affinity', 'linkage', 'iteration', 'labels_pred', 'min_dist'
        ] + [ em.__name__ for em in evaluation_metrics]
    )

    if args.fl:
        if not args.no_cache and not args.no_fl_cache and os.path.exists(fl_result_path):
            fl_result_df = pd.read_pickle(fl_result_path)
        else:
            fl_result_df = pd.DataFrame(
                columns=[
                    'data_id', 'formula', 'labels_pred', 'cluster', 'faulty_component_index', 'rank', 'tie_breaker'
                ]
            )
    else:
        fl_result_df = None

    if args.no_cache:
        dist_cost_df = pd.DataFrame(
            columns=[
                'data_id', 'affinity', 'cost'
            ]   
        )
    else:
        dist_cost_df = None

    for data_id in dataset:
        config = dataset[data_id]
        try:
            fd = FaultData(
                data_id, config['coverage'], config['failing_tests'],
                dataset_dir=dataset_dir,
                faulty_components=config.get('faulty_components'),
                faulty_components_level=config.get('faulty_components_level'),
                setup_command=config.get('setup_command'), cleanup_command=config.get('cleanup_command')
            )
        except NoFailingTestCaseError as e:
            logging.debug(f'{data_id}: {e}')
            continue

        HG = fd.hypergraph

        logging.info(f"[{data_id}] nodes: {HG.N}, hyperedges: {HG.M}")
        logging.info(f"{fd.labels_true} - Ground Truth")

        baselines = {
            'TCN': fd.labels_TCN,
            'MSeer': fd.labels_MSeer(use_cache=not args.no_cache),
            'Louvain-link': fd.labels_louvain(similarity='link'),
            'Louvain-nlink': fd.labels_louvain(similarity='nlink'),
        }

        for baseline in baselines:
            logging.info(f"{baselines[baseline]} - {baseline} (baseline)")

        meta_df = meta_df.append({
                'data_id': data_id,
                'faults': fd.faults,
                'num_faults': len(fd.faults),
                'labels_true': fd.labels_true,
                'num_vertices': HG.N,
                'num_hyperedges': HG.M,
                'num_total_tests': len(fd.tests),
                'num_total_components': len(fd.components),
                'failing_tests': fd.failing_tests,
                'faulty_components': fd.faulty_components,
                'root_cause_map': fd.root_cause_map,
                'hypergraph_modeling_cost': fd.hypergraph_modeling_cost,
                'coverage_reduction_cost': fd.coverage_reduction_cost
            }, ignore_index=True)

        tmp_result_df = pd.DataFrame()

        # 1. Run agglomeratvie clustering
        method = 'Agglomerative'
        for affinity in AFFINITY:
            # 1-1. Initialize needed variables
            start_time = time.time()
            distance_matrix = fd.get_distance_matrix(affinity)
            end_time = time.time()
            if affinity == 'RKT':
                dist_calculation_cost = fd.dist_calculation_cost['RKT']
            else:
                dist_calculation_cost = end_time - start_time

            if dist_cost_df is not None:
                dist_cost_df = dist_cost_df.append(
                    {
                        'data_id': data_id,
                        'affinity': affinity,
                        'cost': dist_calculation_cost
                    }, ignore_index=True)

            affinity_ = "precomputed"

            # 1-2. Initialize similarity graph (affinity-dependent)
            G = None

            # 1-3. Run agglomerative clustering with affinity and linkage
            for linkage in LINKAGE:
                # 1-3-1. Initialize agglomerative clustering instance
                aggl = hAggl(n_clusters=k, verbose=False, affinity=affinity_, linkage=linkage)

                logging.info(f"Initialized Agglomerative Clustering: {affinity}, {linkage}")

                skip = False

                if old_result_df is not None:
                    old_results = old_result_df[(old_result_df.data_id == data_id)
                        & (old_result_df.clustering == method)
                        & (old_result_df.affinity == affinity) & (old_result_df.linkage == linkage)]

                    if old_results.shape[0] == HG.N:
                        # if the results of (affinity, linkage) already exist, skip
                        logging.debug(f"Skipping")
                        tmp_result_df = tmp_result_df.append(old_results, ignore_index=True)
                        skip = True

                # 1-3-2. Run agglomerative clustering if needed
                if not skip:
                    aggl.fit(fd.hypergraph, distance_matrix=distance_matrix)

                    for i, labels_pred in enumerate(aggl.labels):
                        min_dist = aggl.mdist[i] if i < len(aggl.mdist) else float("inf")
                        row = {
                            'data_id': data_id,
                            'clustering': 'Agglomerative',
                            'affinity': affinity,
                            'linkage': linkage,
                            'iteration': i,
                            'labels_pred': tuple(sort_clustering_labels(labels_pred.tolist())),
                            'min_dist': min_dist
                        }
                        for evaluation_metric in evaluation_metrics:
                            row[evaluation_metric.__name__] = evaluation_metric(fd.labels_true, labels_pred.tolist())
                        tmp_result_df = tmp_result_df.append(row, ignore_index=True)
                
                is_in_current_settings = (tmp_result_df.clustering == method)\
                    & (tmp_result_df.affinity == affinity)\
                    & (tmp_result_df.linkage == linkage)\

                min_dist_knee = find_min_dist_knee(tmp_result_df[is_in_current_settings].min_dist.values)

                tmp_result_df.loc[is_in_current_settings, 'is_min_dist_knee'] = False
                tmp_result_df.loc[is_in_current_settings & (tmp_result_df.iteration == min_dist_knee), 'is_min_dist_knee'] = True

                # 1-3-3. Define similarity matrix
                if G is None:
                    if distance_matrix is not None:
                        G = SimilarityGraph(1 - distance_matrix)
                    else:
                        G = SimilarityGraph(1 - aggl.distance_matrix(HG))

                del aggl

            # 1-4. Calculate affinity-dependent objective functions
            # - modularity
            unique_labels = tmp_result_df[
                (tmp_result_df.clustering == method)
                & (tmp_result_df.affinity == affinity)].labels_pred.unique()

            assert G is not None

            for labels_pred in unique_labels:
                assert G is not None
                modularity =  G.modularity(list(labels_pred))
                tmp_result_df.loc[
                    (tmp_result_df.clustering == method)
                    & (tmp_result_df.affinity == affinity)
                    & (tmp_result_df.labels_pred == labels_pred), 'modularity'] = modularity

        # 1-5. Calculate affinity-agnostic objective functions
        # - hncut
        unique_labels = tmp_result_df[tmp_result_df.clustering == method].labels_pred.unique()

        for labels_pred in unique_labels:
            hncut = HG.hncut(list(labels_pred))
            tmp_result_df.loc[
                (tmp_result_df.clustering == method)
                & (tmp_result_df.labels_pred == labels_pred), 'hNCut'] = float(hncut)

        # 2. Add Baseline results
        for method in baselines:
            row = {
                'data_id': data_id,
                'clustering': method,
                'labels_pred': tuple(baselines[method]),
                'iteration': 0,
                'min_dist': float("inf"),
            }
            for evaluation_metric in evaluation_metrics:
                row[evaluation_metric.__name__] = evaluation_metric(fd.labels_true, baselines[method])
            tmp_result_df = tmp_result_df.append(row, ignore_index=True)

        # 3. Add the all results of current data point to the final result dataframe
        result_df = result_df.append(tmp_result_df, ignore_index=True)

        # 4. Check whether fault localization can be performed
        if fl_result_df is None or fd.faulty_components is None:
            continue

        # 5. Run fault localization
        logging.info(f"Running fault localization on {data_id}")

        if args.fl_only_knee:
            unique_labels = tmp_result_df[(tmp_result_df.clustering != 'Agglomerative') | (tmp_result_df.is_min_dist_knee == True)].labels_pred.unique()
        else:
            unique_labels = tmp_result_df.labels_pred.unique()
        logging.info(f"{len(unique_labels)} unique predictions found")

        try:
            for labels_pred in unique_labels:
                for technique in FL_techniques:
                    technique_name = technique.__name__.lower()
                    if fl_result_df is not None:
                        fl_results = fl_result_df[
                            (fl_result_df.data_id == data_id)
                            & (fl_result_df.labels_pred == labels_pred)
                            & (fl_result_df.formula == technique_name)]
                        if fl_results.shape[0] > 0:
                            continue

                    suspiciousness = fd.localize_faults(labels=labels_pred, technique=technique)
                    for cluster in suspiciousness:
                        for tie_breaker in ['max', 'min', 'average', 'ordinal']:
                            ranks = get_ranks_of_faulty_elements(
                                fd.components, suspiciousness[cluster], fd.faulty_components,
                                level=fd.faulty_components_level, method=tie_breaker)
                            for faulty_component in ranks:
                                row = {
                                    'data_id': data_id,
                                    'formula': technique_name,
                                    'labels_pred': labels_pred,
                                    'cluster': cluster,
                                    'faulty_component_index': fd.faulty_components.index(faulty_component),
                                    'rank': ranks[faulty_component],
                                    'tie_breaker': tie_breaker
                                }
                                fl_result_df = fl_result_df.append(row, ignore_index=True)
                    logging.debug(f"{labels_pred} {technique_name}")
        except KeyboardInterrupt as e:
            fl_result_df.to_pickle(fl_result_path)
            logging.info(f"FL results are saved to: {fl_result_path}")
            raise e

        del fd

    meta_df.to_pickle(meta_path)
    print(meta_df)
    result_df.to_pickle(result_path)
    print(result_df)
    if fl_result_df is not None:
        fl_result_df.to_pickle(fl_result_path)
        print(fl_result_df)
    if dist_cost_df is not None:
        dist_cost_df.to_pickle(dist_cost_path)
        print(dist_cost_df)
