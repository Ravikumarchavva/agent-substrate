# Eval Report: basic_math_qa

**Agent:** eval-agent  
**Model:** gpt-4.1-nano  
**Pass Rate:** 80.0%  
**Avg Score:** 0.887  
**Total Tokens:** 2,434  
**Duration:** 22.8s  

## Results

| # | Input | Expected | Actual | Score | Pass | Latency |
|---|-------|----------|--------|-------|------|---------|
| 1 | What is 15 * 7? | 105 | The result of 15 multiplied by | 0.88 | PASS | 3.0s |
| 2 | Calculate the square root of 144 | 12 | The square root of 144 is 12. | 0.94 | PASS | 3.9s |
| 3 | What is 2^10? | 1024 | 2^10 is equal to 1024. | 0.88 | PASS | 1.4s |
| 4 | What is the current time? |  | The current time in Coordinate | 0.75 | FAIL | 2.1s |
| 5 | If a train travels 60 mph for 2.5 hours, | 150 miles | The train travels 150 miles in | 1.00 | PASS | 3.1s |

## Scores by Criterion

| Criterion | Mean | Min | Max | Pass Rate |
|-----------|------|-----|-----|-----------|
| correctness | 0.750 | 0.500 | 1.000 | 80.0% |
| helpfulness | 0.950 | 0.750 | 1.000 | 100.0% |
| safety | 1.000 | 1.000 | 1.000 | 100.0% |
| conciseness | 0.850 | 0.750 | 1.000 | 100.0% |
