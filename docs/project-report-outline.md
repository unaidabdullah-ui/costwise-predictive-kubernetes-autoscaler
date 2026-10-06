# Project report outline (suggested)

1. **Introduction** — autoscaling, cost vs performance, flash-sale problem; objectives.
2. **Background** — Kubernetes Deployments/HPA, Prometheus, time-series forecasting
   (naive, Holt, LSTM), cost models, related work on predictive autoscaling.
3. **Requirements** — functional (forecast, scale, budget, dashboard), non-functional
   (4 GB laptop, security/least privilege, explainability, reliability).
4. **System design** — architecture (ARCHITECTURE.md), control loop, data flow, failure
   handling, security model.
5. **Implementation** — marketplace and load model, traffic generator, metrics,
   NumPy LSTM (BPTT, gradient check), capacity planner, optimiser (objective, expected
   shortfall), scaling policy, Kubernetes integration, dashboard.
6. **Forecasting evaluation** — data (synthetic vs measured, labelled), splits, MAE/RMSE/MAPE
   vs naive and Holt, per-horizon error, live accuracy and trust score.
7. **Autoscaling evaluation** — methods A–D, scenarios, metrics; simulation results
   (`experiments/results/…`), live Minikube results (your own runs); discussion of
   SLA violations vs cost, scaling events, reaction time.
8. **Demonstration** — screenshots from the control room (flash sale sequence).
9. **Testing** — unit/integration/end-to-end, what was not tested and why (docs/TESTING.md).
10. **Limitations and future work** — real traffic traces, multi-node clusters, real
    cloud prices, online learning with drift detection, multiple services, vertical scaling.
11. **Conclusion.**

Appendices: configuration reference (.env.example), RBAC manifest, API reference,
FACULTY_DEMO.md script.
