# Experiment results

Each run of `python -m experiments.run_experiment` writes a folder here:

```
<run-id>/
  run_metadata.json     what was run, "kind": simulation | live measurement on Minikube, config, versions
  summary.csv/.json     one row per (scenario, method, seed)
  aggregate.csv         mean over seeds
  comparison_table.md   the table for the report
  timeseries/           per-second (sim) or per-5-s (live) traces
  charts/               PNG charts
```

`shipped-simulation/` was produced by the simulation mode with the code in this package
(see its run_metadata.json). There are **no live Minikube results** in the package;
produce them with `--mode live` on your laptop.
