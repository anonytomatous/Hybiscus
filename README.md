# 🌺 Hybiscus: Hypergraph-based Failure Clustering Framework

This repository implements a failure clustering technique called *Hybiscus* accompanying the paper:

**Improving Test Distance for Failure Clustering with Hypergraph Modelling (Under Review)**
<!-- Add PDF link -->

- [Supplementary results](results.md)
- Dataset Reconstruction
  - You can reproduce the Java and C multi-faults dataset from the following two repositories:
    - [Java Dataset](https://github.com/anonytomatous/docker-D4J-multifault)
    - [C Dataset](https://github.com/anonytomatous/docker-SIR-multifault)
- Experiement Replication
  - The experiment procedure presented in the paper can be replicated using the evaluation script (`evaluation.py`).
  - Follow the instructions [here](#experiment).
- Using Hybiscus as tool
  - You can use Hybiscus for your own program to cluster the failing tests by their root causes.
  - All you need are a coverage matrix and a list of failing test cases!
  - Check the instructions [here](#tool).


--- 
## ✅ Prerequisite

- Python >= 3.8
- Install packages by typing
    ```shell
    pip install -r requirements.txt
    ```

---
## 📊 Failure Clustering Experiment Results
- ‼️ **See the experiment results [here](results.md)**. ‼️

- Raw results are also available in [resources/result/](./resources/result/).

    | File (Pickled Pandas DataFrame)                      | Description                             |
    |:-----------------------------------------------------|:----------------------------------------|
    |`[project]-[num_faults]-faults_meta.pkl`              | basic information about each subject    |
    |`[project]-[num_faults]-faults_result.pkl`            | failure clustering results              |
    |`[project]-[num_faults]-faults_fl_result.pkl`         | fault localisation results              |
    |`[project]-[num_faults]-faults_dist_code_result.pkl`  | cost measurement results                |

- You can reproduce the figures and tables in the result page using [the analysis script](./Analysis.ipynb).
In the first cell, set the variable `Lang` to `Java` or `C` to analyse the Java and C results, respectively.

--- 
## <a name="experiment"></a> 🔬 Evaluate the Failure Clustering & Fault Localisation Performance

### A. Prepare needed information
To evaluate and compare the failure clustering performance of various clustering methods (Hybiscus, MSeer, ...), you need following data:

1. A coverage data file (`.pkl`): A pickled pandas dataframe whose columns are test case IDs, and the indices are the program component IDs.
    ```python
    # Initialise data of coverage. 
    data = {
        'T1':[1, 1, 0, 0, 1, 1],
        'T2':[1, 1, 0, 0, 1, 1], 
        'T3':[0, 1, 1, 0, 1, 0], 
        'T4':[1, 0, 0, 1, 0, 0], 
        'T5':[0, 1, 0, 1, 1, 0], 
    } 
    
    # Create and save pandas DataFrame.
    df = pd.DataFrame(data, index =['C1', 'C2', 'C3', 'C4', 'C5', 'C6']) 
    df.to_pickle("resources/example/my_coverage.pkl") # See resources/example/
    print(df)
    """
        T1  T2  T3  T4  T5
    C1   1   1   0   1   0
    C2   1   1   1   0   1
    C3   0   0   1   0   0
    C4   0   0   0   1   1
    C5   1   1   1   0   1
    C6   1   1   0   0   0
    """
    ```
2. (*ground-truth*) Files containing failing test cases for each fault (failing test cases should not be ovelapped.)
   - ex) [./resources/example/fault-1-failing-tests](./resources/example/fault-1-failing-tests):
        ```
        T3
        ```

   - ex) [./resources/example/fault-2-failing-tests](./resources/example/fault-2-failing-tests):
        ```
        T4
        T5
        ```

3. (*ground-truth*, optional) Files containing faulty components for each faults (to evaluate FL performance)
   - ex) [./resources/example/fault-1-faulty-components](./resources/example/fault-1-faulty-components):
        ```
        C3
        ```
   - ex) [./resources/example/fault-2-faulty-components](./resources/example/fault-2-faulty-components):
        ```
        C4
        ```

### B. Generate a Evaluation Dataset
- ex) [./resources/example/evaluation_example.json](./resources/example/evaluation_example.json)
    ```json
    {
        ...,
        "my-program-v1": {
            "coverage": "./my_coverage.pkl",
            "failing_tests": {
                "fault-1": "./fault-1-failing-tests",
                "fault-2": "./fault-2-failing-tests"
            },
            "faulty_components": {
                "fault-1": "./fault-1-faulty-components",
                "fault-2": "./fault-2-faulty-components"
            }
        },
        ...
    }
    ```

- **If you want to construct a D4J multi-fault dataset, refer to https://github.com/anonytomatous/docker-D4J-multifault**
- **If you want to construct a SIR multi-fault dataset, refer to https://github.com/anonytomatous/docker-SIR-multifault**

### C. Run the Evaluation Script 

- Execute `evaluate.py` to run the experiment.
    ```shell
    python evaluate.py [-h] [--id ID] [--no-cache] [--no-fl-cache] [--fl] [--fl-only-knee] dataset
    # example
    python evaluate.py ./resources/example/evaluation_example.json
    python evaluate.py ./resources/example/evaluation_example.json --fl # to run the FL evaluation
    python evaluate.py ./resources/example/evaluation_example.json --fl --fl-only-knee # run FL for only the suggested "k" of AHC
    ```

- The results will be saved to `./resources/result/evaluation_example_*`.
- RKT distance will be stored in `resources/MSeer/my-program-v1.pt`.
- The distance calculation cost is measured only when either there are no existing result files or the `--no-cache` option is provided.
--- 
## <a name="tool"></a> 🛠 Wanna use Hybiscus as a tool?

This is very similar to the experiment running process, but you don't need any ground-truth.

To run **Hybiscus**, we need following data:
1. A coverage data (`.pkl`)
   - ex) [./resources/example/my_coverage.pkl](resources/example/my_coverage.pkl)
2. A list of failing test cases
   - ex) [./resources/example/failing-tests](./resources/example/failing-tests):
        ```
        T3
        T4
        T5
        ```

Based on the coverage data and failing test information, you can run Hybiscus as follows:
```shell
# usage: Hybiscus.py [-h] [--linkage LINKAGE] [--k K] [--threshold THRESHOLD] [--fl-formula FL_FORMULA] [--output OUTPUT] coverage failing

python Hybiscus.py ./resources/example/my_coverage.pkl ./resources/example/failing-tests --linkage complete

# 2021-01-25 00:41:50,229 INFO     Loading coverage data: ./resources/example/my_coverage.pkl
# 2021-01-25 00:41:50,230 INFO     Loading failing tests data: ./resources/example/failing-tests
# 2021-01-25 00:41:50,230 INFO     Processing input data....
# 2021-01-25 00:41:50,232 INFO     Hypergraph modeling takes 0.0 seconds
# 2021-01-25 00:41:50,233 DEBUG    mdist: 0.4500000476837158 (k=3)
# 2021-01-25 00:41:50,234 DEBUG    mdist: 1.0 (k=2)
# 2021-01-25 00:41:50,234 INFO     Calculating k value.... elbow point of mdist curve
# 2021-01-25 00:41:50,234 INFO     ======================= Hybiscus =======================
# 2021-01-25 00:41:50,234 INFO     k: 2
# 2021-01-25 00:41:50,235 INFO     cluster 1: ['T3']
# 2021-01-25 00:41:50,235 INFO     cluster 2: ['T4' 'T5']
# 2021-01-25 00:41:50,235 INFO     ========================================================
```
By default, Hybiscus suggests `k` (# clusters) using the distance-based stopping criterion.
```shell
python Hybiscus.py ./resources/example/my_coverage.pkl ./resources/example/failing-tests --linkage complete
```

Instead, you can specify `k` or the distance threshold:
```shell
python Hybiscus.py ./resources/example/my_coverage.pkl ./resources/example/failing-tests --linkage complete --k 2 
python Hybiscus.py ./resources/example/my_coverage.pkl ./resources/example/failing-tests --linkage complete --threshold 0.5
```

Note that `--k` has priority over `--threshold` if both are given.

Also, after the failure clustering, you can localise faults by applying a coverage-based fault localisation technique.
```shell
python Hybiscus.py ./resources/example/my_coverage.pkl ./resources/example/failing-tests --linkage complete --fl-formula Ochiai # or Crosstab
```

Check out `./output.json`. (use the `--output` option to change the path to output)
- `clusters`: the failure clustering results
- `FL_result`: the suspiciousness scores of program components when using only the failing tests in a cluster and all passing test cases
  - For example, using the first cluster `[T3]` (+ passing tests `[T1, T2]`), the most suspicious element is `C3` with 1.0 of suspiciousness score.
```json
{
    "clusters": [
        [
            "T3"
        ],
        [
            "T4",
            "T5"
        ]
    ],
    "FL_result": [
        [
            [
                "C1",
                0.0
            ],
            [
                "C2",
                0.5773502588272095
            ],
            [
                "C3",
                1.0
            ],
            [
                "C4",
                NaN
            ],
            [
                "C5",
                0.5773502588272095
            ],
            [
                "C6",
                0.0
            ]
        ],
        [
            [
                "C1",
                0.40824827551841736
            ],
            [
                "C2",
                0.40824827551841736
            ],
            [
                "C3",
                NaN
            ],
            [
                "C4",
                1.0
            ],
            [
                "C5",
                0.40824827551841736
            ],
            [
                "C6",
                0.0
            ]
        ]
    ]
}
```
