# Article Quality Checker

Quality assessment based on 6 dimensions.

## Quality Dimensions

| Dimension | Criteria | Points |
|-----------|----------|--------|
| 1. One-sentence definition | Clear definition at the beginning | 1-5 |
| 2. Detailed explanation | What/Why/How covered | 1-5 |
| 3. Important details | ≥3 technical points/data/cases | 1-5 |
| 4. Architecture/Flow | Diagrams if applicable | 1-5 |
| 5. Action recommendations | ≥2 actionable items | 1-5 |
| 6. Related knowledge | [[wikilink]] format links | 1-5 |

## Scoring

- **5**: Excellent, complete with depth
- **4**: Good, mostly complete
- **3**: Passable, main structure present
- **2**: Unqualified, major gaps
- **1**: Poor, almost unusable

## Pass Threshold

**Total ≥ 18** (average 3+ per dimension)

## Usage

```bash
# Check single file
python3 60-Logs/scripts/batch_quality_checker.py --file article.md

# Check all areas
python3 60-Logs/scripts/batch_quality_checker.py --all
```
