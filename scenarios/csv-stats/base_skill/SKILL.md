---
name: csv-stats
description: Compute simple statistics over a column in a CSV file.
---
# CSV statistics

To compute a statistic over a CSV column:
1. Read the file line by line.
2. Split each line on commas to get the fields.
3. Use the first line as the header to find the column index.
4. Convert the values in that column to numbers and compute the requested
   statistic (sum, mean, count, min, or max).
5. Print the result.
