# Complete write results

Seconds are median [minimum-maximum] over three repetitions. All rows use 1,000 records.

| Input | Shape | Target bytes | Python write s | COPY write s | Speedup |
| --- | --- | ---: | --- | --- | ---: |
| pandas | larger | 65,536 | 0.0321 [0.0297-0.0577] | 0.1592 [0.1427-0.1604] | 0.20x |
| pandas | smaller | 65,536 | 0.0166 [0.0148-0.0176] | 0.0506 [0.0444-0.0652] | 0.33x |
| recordbatch | larger | 65,536 | 0.0296 [0.0265-0.0320] | 0.0265 [0.0225-0.0288] | 1.12x |
| recordbatch | smaller | 65,536 | 0.0122 [0.0109-0.0127] | 0.0191 [0.0164-0.0194] | 0.64x |
| table | larger | 65,536 | 0.0321 [0.0277-0.0367] | 0.0230 [0.0225-0.0270] | 1.39x |
| table | smaller | 65,536 | 0.0125 [0.0100-0.0131] | 0.0224 [0.0204-0.0256] | 0.56x |

## Actual fragments

File count and removed empties are per-trial ranges. Size statistics pool all three repetitions.

| Input | Shape | Target | Path | Total bytes | Files | File bytes min / median / max | Empty removed |
| --- | --- | ---: | --- | ---: | --- | --- | --- |
| pandas | larger | 65,536 | direct | 1,971,455 | 30-30 | 27,615 / 67,079 / 67,112 | 0-0 |
| pandas | larger | 65,536 | copy | 1,971,455 | 1-1 | 1,971,455 / 1,971,455 / 1,971,455 | 1-1 |
| pandas | smaller | 65,536 | direct | 398,225 | 7-7 | 3,213 / 65,852 / 65,896 | 0-0 |
| pandas | smaller | 65,536 | copy | 398,225 | 1-1 | 398,225 / 398,225 / 398,225 | 1-1 |
| recordbatch | larger | 65,536 | direct | 1,971,455 | 30-30 | 27,615 / 67,079 / 67,112 | 0-0 |
| recordbatch | larger | 65,536 | copy | 1,971,455 | 1-1 | 1,971,455 / 1,971,455 / 1,971,455 | 1-1 |
| recordbatch | smaller | 65,536 | direct | 398,225 | 7-7 | 3,213 / 65,852 / 65,896 | 0-0 |
| recordbatch | smaller | 65,536 | copy | 398,225 | 1-1 | 398,225 / 398,225 / 398,225 | 1-1 |
| table | larger | 65,536 | direct | 1,971,455 | 30-30 | 27,615 / 67,079 / 67,112 | 0-0 |
| table | larger | 65,536 | copy | 1,971,455 | 1-1 | 1,971,455 / 1,971,455 / 1,971,455 | 1-1 |
| table | smaller | 65,536 | direct | 398,225 | 7-7 | 3,213 / 65,852 / 65,896 | 0-0 |
| table | smaller | 65,536 | copy | 398,225 | 1-1 | 398,225 / 398,225 / 398,225 | 1-1 |
