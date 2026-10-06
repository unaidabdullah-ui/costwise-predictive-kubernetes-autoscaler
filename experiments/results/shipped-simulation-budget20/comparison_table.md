
### Scenario: constant

| Metric | A: Fixed pods (peak) | B: Reactive HPA | C: Predictive (LSTM) | D: Predictive + cost-aware |
|---|---|---|---|---|
| Total requests | 17,976 | 17,976 | 17,976 | 17,976 |
| Avg latency (ms) | 50.2 | 50.2 | 50.2 | 50.2 |
| p95 latency (ms) | 54 | 54 | 54 | 54 |
| Peak latency (ms) | 59 | 59 | 59 | 59 |
| SLA violations (% req) | 0.00 | 0.00 | 0.00 | 0.00 |
| Avg pods | 3.00 | 3.00 | 3.00 | 3.00 |
| Peak pods | 3 | 3 | 3 | 3 |
| Scaling events | 0.0 | 0.0 | 0.0 | 0.0 |
| Under-provisioned episode (s) | 0.0 | 0.0 | 0.0 | 0.0 |
| Est. cost (currency) | 2.5000 | 2.5000 | 2.5000 | 2.5000 |
| Peak budget use | 0.75 | 0.75 | 0.75 | 0.75 |
| Forecast MAE (req/s) | - | - | 0.17 | 0.17 |
| Forecast RMSE (req/s) | - | - | 0.20 | 0.20 |

### Scenario: spike

| Metric | A: Fixed pods (peak) | B: Reactive HPA | C: Predictive (LSTM) | D: Predictive + cost-aware |
|---|---|---|---|---|
| Total requests | 15,760 | 15,760 | 15,760 | 15,760 |
| Avg latency (ms) | 41.1 | 936.7 | 486.3 | 514.4 |
| p95 latency (ms) | 54 | 5500 | 4262 | 4262 |
| Peak latency (ms) | 58 | 5500 | 5500 | 5500 |
| SLA violations (% req) | 0.00 | 20.24 | 12.98 | 13.78 |
| Avg pods | 6.00 | 3.67 | 3.35 | 2.77 |
| Peak pods | 6 | 5 | 6 | 4 |
| Scaling events | 0.0 | 2.0 | 6.0 | 3.0 |
| Under-provisioned episode (s) | 0.0 | 42.0 | 15.5 | 10.5 |
| Est. cost (currency) | 5.0000 | 3.0625 | 2.7917 | 2.3056 |
| Peak budget use | 1.50 | 1.25 | 1.50 | 1.00 |
| Forecast MAE (req/s) | - | - | 0.54 | 0.54 |
| Forecast RMSE (req/s) | - | - | 1.19 | 1.19 |

### Scenario: ramp_up

| Metric | A: Fixed pods (peak) | B: Reactive HPA | C: Predictive (LSTM) | D: Predictive + cost-aware |
|---|---|---|---|---|
| Total requests | 24,380 | 24,380 | 24,380 | 24,380 |
| Avg latency (ms) | 41.6 | 96.7 | 55.3 | 72.5 |
| p95 latency (ms) | 52 | 162 | 68 | 114 |
| Peak latency (ms) | 57 | 500 | 77 | 162 |
| SLA violations (% req) | 0.00 | 1.30 | 0.00 | 0.00 |
| Avg pods | 6.00 | 2.52 | 3.28 | 2.95 |
| Peak pods | 6 | 4 | 5 | 4 |
| Scaling events | 0.0 | 3.0 | 4.0 | 3.0 |
| Under-provisioned episode (s) | 0.0 | 7.2 | 0.0 | 4.8 |
| Est. cost (currency) | 6.0000 | 2.5208 | 3.2847 | 2.9514 |
| Peak budget use | 1.50 | 1.00 | 1.25 | 1.00 |
| Forecast MAE (req/s) | - | - | 0.24 | 0.24 |
| Forecast RMSE (req/s) | - | - | 0.32 | 0.32 |

### Scenario: periodic

| Metric | A: Fixed pods (peak) | B: Reactive HPA | C: Predictive (LSTM) | D: Predictive + cost-aware |
|---|---|---|---|---|
| Total requests | 31,442 | 31,442 | 31,442 | 31,442 |
| Avg latency (ms) | 41.0 | 485.6 | 54.2 | 68.4 |
| p95 latency (ms) | 52 | 4323 | 77 | 113 |
| Peak latency (ms) | 58 | 5500 | 106 | 166 |
| SLA violations (% req) | 0.00 | 12.50 | 0.00 | 0.00 |
| Avg pods | 6.00 | 3.70 | 4.21 | 3.67 |
| Peak pods | 6 | 4 | 5 | 4 |
| Scaling events | 0.0 | 3.0 | 17.0 | 11.0 |
| Under-provisioned episode (s) | 0.0 | 7.4 | 1.8 | 4.0 |
| Est. cost (currency) | 7.5000 | 4.6250 | 5.2569 | 4.5903 |
| Peak budget use | 1.50 | 1.00 | 1.25 | 1.00 |
| Forecast MAE (req/s) | - | - | 0.45 | 0.45 |
| Forecast RMSE (req/s) | - | - | 0.57 | 0.57 |

### Scenario: bursty

| Metric | A: Fixed pods (peak) | B: Reactive HPA | C: Predictive (LSTM) | D: Predictive + cost-aware |
|---|---|---|---|---|
| Total requests | 28,423 | 28,423 | 28,423 | 28,423 |
| Avg latency (ms) | 37.0 | 464.3 | 142.3 | 263.1 |
| p95 latency (ms) | 52 | 4241 | 518 | 836 |
| Peak latency (ms) | 61 | 5500 | 3272 | 3272 |
| SLA violations (% req) | 0.00 | 17.83 | 6.16 | 28.57 |
| Avg pods | 8.00 | 3.62 | 3.91 | 3.03 |
| Peak pods | 8 | 6 | 7 | 4 |
| Scaling events | 0.0 | 3.0 | 13.0 | 5.0 |
| Under-provisioned episode (s) | 0.0 | 8.2 | 6.1 | 11.4 |
| Est. cost (currency) | 10.0000 | 4.5208 | 4.8819 | 3.7847 |
| Peak budget use | 2.00 | 1.50 | 1.75 | 1.00 |
| Forecast MAE (req/s) | - | - | 0.78 | 0.78 |
| Forecast RMSE (req/s) | - | - | 1.34 | 1.34 |

### Scenario: flash_sale

| Metric | A: Fixed pods (peak) | B: Reactive HPA | C: Predictive (LSTM) | D: Predictive + cost-aware |
|---|---|---|---|---|
| Total requests | 13,447 | 13,447 | 13,447 | 13,447 |
| Avg latency (ms) | 45.6 | 1261.7 | 50.4 | 95.5 |
| p95 latency (ms) | 57 | 5500 | 71 | 161 |
| Peak latency (ms) | 61 | 5500 | 123 | 222 |
| SLA violations (% req) | 0.00 | 28.54 | 0.00 | 0.00 |
| Avg pods | 6.00 | 3.75 | 3.33 | 2.63 |
| Peak pods | 6 | 5 | 6 | 4 |
| Scaling events | 0.0 | 4.0 | 12.0 | 8.0 |
| Under-provisioned episode (s) | 0.0 | 23.7 | 2.3 | 33.5 |
| Est. cost (currency) | 5.0000 | 3.1250 | 2.7778 | 2.1944 |
| Peak budget use | 1.50 | 1.25 | 1.50 | 1.00 |
| Forecast MAE (req/s) | - | - | 0.28 | 0.28 |
| Forecast RMSE (req/s) | - | - | 0.43 | 0.43 |
