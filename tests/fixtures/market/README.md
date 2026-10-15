# Market-data fixtures

Small files in the FRED CSV download layout (`observation_date,<SERIES>`; older downloads use
`DATE`). A missing value is `.` or empty. The values are illustrative test data, not a copy of
official FRED series; tests only rely on the arithmetic between the rows in these files.
