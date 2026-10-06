
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
| Peak budget use | 0.43 | 0.43 | 0.43 | 0.43 |
| Forecast MAE (req/s) | - | - | 0.17 | 0.17 |
| Forecast RMSE (req/s) | - | - | 0.20 | 0.20 |

### Scenario: spike

| Metric | A: Fixed pods (peak) | B: Reactive HPA | C: Predictive (LSTM) | D: Predictive + cost-aware |
|---|---|---|---|---|
| Total requests | 15,760 | 15,760 | 15,760 | 15,760 |
| Avg latency (ms) | 41.1 | 936.7 | 486.3 | 486.0 |
| p95 latency (ms) | 54 | 5500 | 4262 | 4262 |
| Peak latency (ms) | 58 | 5500 | 5500 | 5500 |
| SLA violations (% req) | 0.00 | 20.24 | 12.98 | 12.98 |
| Avg pods | 6.00 | 3.67 | 3.35 | 3.36 |
| Peak pods | 6 | 5 | 6 | 6 |
| Scaling events | 0.0 | 2.0 | 6.0 | 6.0 |
| Under-provisioned episode (s) | 0.0 | 42.0 | 15.5 | 15.5 |
| Est. cost (currency) | 5.0000 | 3.0625 | 2.7917 | 2.7986 |
| Peak budget use | 0.86 | 0.71 | 0.86 | 0.86 |
| Forecast MAE (req/s) | - | - | 0.54 | 0.54 |
| Forecast RMSE (req/s) | - | - | 1.19 | 1.19 |

### Scenario: ramp_up

| Metric | A: Fixed pods (peak) | B: Reactive HPA | C: Predictive (LSTM) | D: Predictive + cost-aware |
|---|---|---|---|---|
| Total requests | 24,380 | 24,380 | 24,380 | 24,380 |
| Avg latency (ms) | 41.6 | 96.7 | 55.3 | 55.3 |
| p95 latency (ms) | 52 | 162 | 68 | 68 |
| Peak latency (ms) | 57 | 500 | 77 | 77 |
| SLA violations (% req) | 0.00 | 1.30 | 0.00 | 0.00 |
| Avg pods | 6.00 | 2.52 | 3.28 | 3.28 |
| Peak pods | 6 | 4 | 5 | 5 |
| Scaling events | 0.0 | 3.0 | 4.0 | 4.0 |
| Under-provisioned episode (s) | 0.0 | 7.2 | 0.0 | 0.0 |
| Est. cost (currency) | 6.0000 | 2.5208 | 3.2847 | 3.2847 |
| Peak budget use | 0.86 | 0.57 | 0.71 | 0.71 |
| Forecast MAE (req/s) | - | - | 0.24 | 0.24 |
| Forecast RMSE (req/s) | - | - | 0.32 | 0.32 |

### Scenario: periodic

| Metric | A: Fixed pods (peak) | B: Reactive HPA | C: Predictive (LSTM) | D: Predictive + cost-aware |
|---|---|---|---|---|
| Total requests | 31,442 | 31,442 | 31,442 | 31,442 |
| Avg latency (ms) | 41.0 | 485.6 | 54.2 | 54.2 |
| p95 latency (ms) | 52 | 4323 | 77 | 77 |
| Peak latency (ms) | 58 | 5500 | 106 | 106 |
| SLA violations (% req) | 0.00 | 12.50 | 0.00 | 0.00 |
| Avg pods | 6.00 | 3.70 | 4.21 | 4.21 |
| Peak pods | 6 | 4 | 5 | 5 |
| Scaling events | 0.0 | 3.0 | 17.0 | 17.0 |
| Under-provisioned episode (s) | 0.0 | 7.4 | 1.8 | 1.8 |
| Est. cost (currency) | 7.5000 | 4.6250 | 5.2569 | 5.2569 |
| Peak budget use | 0.86 | 0.57 | 0.71 | 0.71 |
| Forecast MAE (req/s) | - | - | 0.45 | 0.45 |
| Forecast RMSE (req/s) | - | - | 0.57 | 0.57 |

### Scenario: bursty

| Metric | A: Fixed pods (peak) | B: Reactive HPA | C: Predictive (LSTM) | D: Predictive + cost-aware |
|---|---|---|---|---|
| Total requests | 28,423 | 28,423 | 28,423 | 28,423 |
| Avg latency (ms) | 37.0 | 464.3 | 142.3 | 135.6 |
| p95 latency (ms) | 52 | 4241 | 518 | 278 |
| Peak latency (ms) | 61 | 5500 | 3272 | 3272 |
| SLA violations (% req) | 0.00 | 17.83 | 6.16 | 5.13 |
| Avg pods | 8.00 | 3.62 | 3.91 | 3.92 |
| Peak pods | 8 | 6 | 7 | 7 |
| Scaling events | 0.0 | 3.0 | 13.0 | 12.0 |
| Under-provisioned episode (s) | 0.0 | 8.2 | 6.1 | 4.8 |
| Est. cost (currency) | 10.0000 | 4.5208 | 4.8819 | 4.9028 |
| Peak budget use | 1.14 | 0.86 | 1.00 | 1.00 |
| Forecast MAE (req/s) | - | - | 0.78 | 0.78 |
| Forecast RMSE (req/s) | - | - | 1.34 | 1.34 |

### Scenario: flash_sale

| Metric | A: Fixed pods (peak) | B: Reactive HPA | C: Predictive (LSTM) | D: Predictive + cost-aware |
|---|---|---|---|---|
| Total requests | 13,447 | 13,447 | 13,447 | 13,447 |
| Avg latency (ms) | 45.6 | 1261.7 | 50.4 | 50.4 |
| p95 latency (ms) | 57 | 5500 | 71 | 71 |
| Peak latency (ms) | 61 | 5500 | 123 | 123 |
| SLA violations (% req) | 0.00 | 28.54 | 0.00 | 0.00 |
| Avg pods | 6.00 | 3.75 | 3.33 | 3.33 |
| Peak pods | 6 | 5 | 6 | 6 |
| Scaling events | 0.0 | 4.0 | 12.0 | 12.0 |
| Under-provisioned episode (s) | 0.0 | 23.7 | 2.3 | 2.3 |
| Est. cost (currency) | 5.0000 | 3.1250 | 2.7778 | 2.7778 |
| Peak budget use | 0.86 | 0.71 | 0.86 | 0.86 |
| Forecast MAE (req/s) | - | - | 0.28 | 0.28 |
| Forecast RMSE (req/s) | - | - | 0.43 | 0.43 |
