# Faculty demo — script (3–5 minutes) and likely questions

## Before the session (10 minutes earlier)

1. Close heavy programs (browsers with many tabs, IDEs). Start Docker Desktop.
2. `.\start-demo.ps1` → wait for "CostWise is running" (1–2 min if the cluster exists,
   longer the very first time). The dashboard opens at http://127.0.0.1:8000.
3. Check the six status pills. *Forecast model* says "Attention / warming up" until
   there is 30–60 s of traffic — that is expected.
4. Optional: press **START DEMO** once, wait a minute, then **RESET DEMO**, so
   everything (images, Prometheus) is warm.
5. Press **P** for presentation mode (large fonts). **F** = full screen.

## The script

**0:00 — The problem (20 s).**
"Online stores get sudden rushes — a flash sale. Kubernetes' standard autoscaler
reacts *after* the CPU is already high, and new pods take time to start, so customers
see slow pages exactly when it matters. Keeping many pods running all the time avoids
that but wastes money. CostWise predicts the rush and scales *before* it, while
watching a budget."

**0:20 — What is on the screen (30 s).**
"This is a real online store, COSTWISE MARKETPLACE, running on Kubernetes" — optionally
open http://127.0.0.1:8080 in a second tab and click a product. "Simulated shoppers
send real HTTP requests to it. Prometheus measures every pod; CostWise reads those
measurements every 5 seconds." Point at the pills: "every part is live and healthy."

**0:50 — START DEMO (40 s).**
Press **START DEMO**. "About 170 shoppers, around 7 requests per second. One pod is
enough; it costs ₹5 per hour of our ₹35 budget." Point at the blue line (measured)
and the orange dashed line (what the LSTM predicted 30 s earlier). "The two lines lie on
top of each other — the model forecasts normal traffic well."

**1:30 — FLASH SALE (90 s).** Press **FLASH SALE**.
* "The sale is announced; shoppers start arriving." Wait until the dotted orange
  forecast jumps up while the blue line has only begun to rise.
* "The LSTM now predicts ~60 requests per second within 30 seconds — about three
  times today's load." Read the **Why did the system scale?** card aloud: current load,
  predicted peak, *one pod handles ~14 req/s at 70 %*, so *N pods are needed*, *cost
  ₹X within the ₹35 budget*, *CostWise asked Kubernetes for N pods*.
* Point at the pod tiles: yellow *Starting* → green *Running*. "These are real pods
  being created by Kubernetes."
* Point at the green capacity line: "capacity arrives before the traffic does."
* Point at **Reactive vs predictive**: "this scale-up came N seconds before the load
  exceeded what the old pods could handle — that is the head start a reactive
  autoscaler does not have. Pods took about M seconds to start; that is how long
  shoppers would have waited."

**3:00 — Cost (30 s).** Point at the cost card. "We never exceed the budget. If the
budget were too small, CostWise shows *Budget limit* and serves as many shoppers as the
budget allows instead of overspending." (Optional live proof: Technical details →
Settings → budget ₹15 → Apply; the next decisions are capped at 3 pods. Set it back to 35.)

**3:30 — STOP TRAFFIC (40 s).** Press **STOP TRAFFIC**. "The traffic is gone. CostWise
waits one minute — so it does not flap if the traffic comes back — then removes the
extra pods; the cost drops back to ₹5 per hour." Point at the timeline: every step is
written in plain English.

**4:10 — Wrap-up (30 s).** "Measure → predict → plan → optimise cost → apply safely,
every 5 seconds, with least-privilege access to Kubernetes. The Technical details page
shows the model's accuracy, all options the optimiser compared, and the security
settings." Press **RESET DEMO** for the next group.

### If something goes wrong during the demo

| You see | Say / do |
|---|---|
| A pill turns red | Hover it: the tooltip says what failed and how to fix it. The controller holds the current pods; nothing unsafe happens. |
| "Safe mode" | "The forecast is not trusted right now, so CostWise falls back to the measured load and never removes pods." This is a feature. |
| Traffic generator offline | `.\start-demo.ps1` again (safe to re-run). |
| Laptop slow | `.\stop-demo.ps1 -StopCluster` after the session frees the memory. |

## Questions you are likely to get

**Is the data real?** Yes. Shoppers are simulated, but every request is a real HTTP
request handled by a real pod; all numbers on the dashboard come from Prometheus, the
Kubernetes API, the LSTM and the controller. Only the prices are simulated (₹ per pod-hour).

**What does the LSTM learn from? Is it learning now?** It was trained on synthetic
"bootstrap" traffic (labelled as such) built from the same shopper patterns. It does not
learn continuously. RETRAIN MODEL retrains it on bootstrap data plus the traffic
Prometheus measured during your demos, and the Technical page shows how many measured
samples were used.

**How accurate is it?** On held-out data: MAE 2.29 req/s, RMSE 4.12 req/s, MAPE 13.8 %,
about half the error of the naive "traffic stays the same" forecast (RMSE 8.44). Error
grows with the horizon (0.94 RMSE at 5 s, 6.57 at 30 s). The live error during the demo
is on the Technical page.

**What if the prediction is wrong?** Three protections: a safety margin and the
model's uncertainty are added to the plan; the live trust score switches to safe mode
when the forecast is worse than simply assuming traffic stays the same; and safe mode
never removes pods.

**Why not just use the HPA?** The HPA reacts to CPU that is already high; then pods
need time to start. The experiments (`python -m experiments.run_experiment`) compare
fixed pods, HPA, predictive and CostWise on identical traffic: SLA violations, latency,
cost, pods and scaling events.

**How do you avoid flapping?** Cooldowns (10 s up, 20 s down), a 60 s scale-down
stabilisation window, step limits (+4/−3), a minimum change and a small penalty in the
optimiser for every pod changed.

**How is the cost computed?** pods × ₹5 per pod-hour (configurable; a resource-based
model using CPU/memory prices is also included). It is an estimate to make the trade-off
visible — not a cloud bill.

**Is it secure?** The controller only has a namespace-scoped Role: read Deployments,
pods and events, and change `deployments/scale`. No cluster-admin, no Secrets, a 12-hour
token. The browser can only press the fixed demo buttons; it never talks to Kubernetes.

**Why does it run on the laptop and not in the cluster?** To fit in 4 GB of RAM. An
in-cluster deployment is provided in `kubernetes/optional/control-plane.yaml`.

**What are the limitations?** A burst with no warning cannot be predicted (it is
handled reactively); the cost is simulated; one Minikube node; orders are kept in pod
memory. All are documented in the README.
