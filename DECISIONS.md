# DECISIONS.md

### 1. Indexes
I created a unique index on `employees.emp_code` to prevent duplicate employee codes and a compound index on `(department, emp_code)` for department filtering. For attendance, the unique `(emp_code, date)` index prevents duplicate daily records. The `(date, emp_code)` index supports date-based queries and sorting. I would consider an index on `(status, date)` but would add it only if query-plan measurements showed a benefit.

### 2. Punch-in race
If two identical punch-in requests arrive together, both may attempt to insert the same employee's attendance record. The unique `(emp_code, date)` index allows only one insertion. The other request receives a duplicate-key error, which the API should convert to HTTP 409 Conflict.

### 3. Ties
Employees with equal late-minute totals receive the same competition rank. If two employees tie at the cutoff, both are returned, even if the number of returned employees exceeds `limit`. The next rank is skipped, and tied employees are ordered by employee code.

### 4. Headcount
The department summary must count employees from the employee collection, not just attendance logs. This includes employees with no attendance records while excluding employees who joined after the reporting period ends.

### 5. Scaling
For 100 times the data, I would inspect MongoDB execution plans, benchmark aggregation pipelines, and optimize indexes based on actual query patterns.