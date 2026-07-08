# CDS 2025-2026 SQL Query Registry for Auto-Filling the Attached PDF

This file is designed to be fed to an LLM/agent that can run SQL in Databricks, collect the result rows, and write the returned values back into the fillable Common Data Set PDF.



## CSULB Playbook context for the fill agent

This section summarizes the CSULB IR&A CDS 2025-2026 Playbook and should be treated as the agent's operating policy before any SQL is run or any PDF field is filled.

### Scope and submission workflow

- Institution: California State University, Long Beach (CSULB), Institutional Research & Analytics.
- Active cycle: **CDS 2025-2026**.
- Default reporting point: **Fall 2025 enrollment**, unless the CDS item explicitly names a different period.
- CDS is completed in the **2025-2026 Excel template**, converted to PDF, and submitted separately to the publishers. It is not submitted through one universal CDS portal.
- Completed CDS is posted publicly on CSULB IR&A's Common Data Set web page after publisher submissions.
- For U.S. News, CDS-aligned fields in the portal may auto-populate; target internal deadline is approximately **May 20**, subject to analyst confirmation.
- **Analyst sign-off is required for every section.** Do not finalize, submit, or publish generated values without analyst approval.

### Data-source rules

Use the playbook tags below to decide whether the agent should run SQL, roll forward a value, request an external value, or leave the item blank.

| Tag | Meaning | Agent behavior |
|---|---|---|
| `[SQL]` | Pull from SQL/ERS in Databricks | Run only against approved ERS/Databricks tables. Confirm exact table names, term codes, census filters, and section-specific filters before final use. |
| `[EXT]` | External department owns value | Do not guess or derive from ERS. Output a request item and leave PDF field blank until the department-provided value is available. |
| `[NEW]` | New or changed for 2025-2026 | Do not roll over. Require extra validation and analyst review. |
| `[ROLL]` | Roll over from prior-year CDS | Copy from prior cycle only after confirming the policy/contact has not changed. |
| `[N/A]` | Not applicable to CSULB | Leave blank in the template unless the PDF explicitly requires `N/A`. Never write `0` as a placeholder. |

### Approved primary sources by section

| CDS section | Source decision | Notes for agent |
|---|---|---|
| A0-A5 | `[ROLL]` | Pre-fill from prior year; verify respondent/contact/institution details with analyst. A0 office is Institutional Research and Analytics, address 1250 Bellflower Blvd, BH133, Long Beach, CA 90840, phone 562-985-7800. |
| A6 | `[NEW]` | Campus Belonging webpage URL. This replaced/renamed prior DEI wording. Confirm current URL with analyst before filling. |
| B1 | `[SQL]` | Pull from `production.silver.erss`; Fall 2025 census/Oct. 15, 2025. Gender columns are **Male / Female / Unknown** only. Do not create/use `Another Gender`. |
| B2 | `[SQL]` | Pull race/ethnicity from `production.silver.erss`. Do not use dashboards or IPEDS export; use ERS directly. |
| B3 | `[SQL]` | Pull degrees awarded from `production.silver.ersd` for **Jul. 1, 2024-Jun. 30, 2025**. |
| B4-B11 | `[SQL]` + `[EXT]` | IR pulls cohort/student list from ERS, then Financial Aid appends Pell and Stafford groups. Validate each column: `(D+E+F) / C * 100 = H`. |
| B22 | `[NEW]` | Retention for Fall 2024 cohort to Fall 2025. Report numerator, denominator, and rate separately. |
| C1 | `[SQL]` | Pull applications/admits/enrolls from `production.silver.ersa`; use `production.silver.erss` for enrolled-student census details if needed. Fall 2025 term. |
| C2 | `[N/A]` | CSULB does not have a waitlist. Leave waitlist fields blank. |
| C3-C8 | `[ROLL]` | Roll HS requirements and test policy after confirming no change. CSULB is test-optional and SAT/ACT are not used in decisions. |
| C11-C12 | `[SQL]` | High-school GPA distribution and average; report **unweighted GPA only**. |
| C13-C22 | `[ROLL]` | Roll forward application fee/deadlines/reply policy, then validate. App fee baseline $70; closing date Dec. 1; reply date May 1. |
| D2 | `[SQL]` | Pull transfer applications/admits/enrolls from admissions data. |
| D3-D22 | `[ROLL]` | Roll transfer policies after confirming no changes; baseline includes min 60 credits and 30 credits to complete at CSULB. |
| E1, E3 | `[ROLL]` | Roll special study options and GE requirements after confirming no changes with Academic Affairs. |
| F1 | `[EXT]` | Housing/commuter/age percentages are owned/requested from Housing Department unless an approved SQL source is confirmed. |
| G1 | `[EXT]` | 2026-2027 tuition, fees, room, board, books, transportation: request from Financial Aid. Do not guess. |
| H1-H15 | `[EXT]` | All financial-aid values are owned by Financial Aid. Request full H-section dataset early. |
| I-1 | `[SQL]` | Faculty counts from `production.silver.ira_faculty`; census date Nov. 1, 2025. |
| I-2 | `[SQL]` | Student-to-faculty ratio formula: `(FT students + 1/3 PT students) / (FT faculty + 1/3 PT faculty)`. Confirm output with analyst. |
| I-3 | `[SQL]` | Class-size section counts by bucket: 2-9, 10-19, 20-29, 30-39, 40-49, 50-99, 100+. Exclude DL, independent study, dissertation, and internships. |
| J1 | `[SQL]` | Bachelor's degree percentages by CIP from `production.silver.ersd` plus CIP/HEGIS crosswalk in Oracle IRA Master. Must sum to exactly 100%. |

### Current-cycle constants and filters

The agent should use these values as defaults, while still allowing analyst overrides if local term coding differs.

```yaml
cycle: "2025-2026"
primary_enrollment_census_date: "2025-10-15"
faculty_census_date: "2025-11-01"
degree_award_window:
  start: "2024-07-01"
  end: "2025-06-30"
retention_cohort: "Fall 2024 first-time full-time bachelor's/equivalent degree-seeking undergraduates"
retention_observation: "Fall 2025 official enrollment/census date"
admissions_term: "Fall 2025"
gender_columns: ["Male", "Female", "Unknown"]
forbidden_placeholder_values: [0, "0", "TBD", "unknown unless this is a real Unknown category"]
```

### Prior-year baselines for reasonableness checks only

These 2024-2025 values are **not** values to fill for the 2025-2026 CDS. Use them only to flag changes greater than plus/minus 5% for analyst review.

| Data point | 2024-2025 baseline | CDS section |
|---|---:|---|
| Grand total all students | 40,057 | B1: UG 36,056; Grad 4,001 |
| Total full-time students | 32,370 | B1: Males 13,077; Females 18,750; Unknown 543 |
| Total part-time students | 7,687 | B1: Males 3,337; Females 4,123; Unknown 227 |
| Degree-seeking UG enrollment | 36,703 | B2 total |
| Bachelor's degrees awarded | 8,902 | B3; Master's 2,068; Doctoral 65 |
| Six-year grad rate, Fall 2019 cohort | 70% | B4-B11; Pell 68%, Stafford 71%, Neither 74% |
| First-year retention rate | 88.11% | B22; retained 5,522 / cohort 6,267 |
| FTFY applicants | 88,224 | C1; admitted 40,105; enrolled 5,884 |
| Average freshman HS GPA | 3.66 | C12; unweighted; 99.44% submitted GPA |
| Transfer applicants | 26,319 | D2; admitted 13,204; enrolled 4,407 |
| In-state tuition + fees | $8,748 | G1; tuition $6,838 + fees $1,910 |
| Out-of-state tuition + fees | $20,994 | G1 |
| On-campus room & board | $18,554 | G1 |
| Total instructional faculty | 2,519 | I-1; FT 1,050; PT 1,469 |
| Student-to-faculty ratio | 27 to 1 | I-2 |
| Total UG class sections | 5,405.9 | I-3; subsections 2,058 |
| Largest bachelor's discipline | Business 17% | J1; Visual/Performing Arts 9%; Psychology 8% |
| Total need-based grants awarded | $354.8M | H1 |
| Average financial-aid package, FT first-year | $15,994 | H2 |
| Graduates who borrowed any loan | 44% | H5; average $17,947 |

### Required QA behavior

The fill agent must create a QA exception log with these columns: `cds_ref`, `pdf_field`, `query_id`, `value`, `baseline_value_if_any`, `percent_change_if_any`, `status`, `issue`, `recommended_next_step`.

Flag, but do not overwrite, any current value differing from the prior-year baseline by more than plus/minus 5%. Required checks:

1. Reproduce last year's historical data with the same process where possible before using current-year results.
2. B1: FT + PT totals and UG + Grad totals must reconcile to grand total.
3. B2: race/ethnicity row totals must reconcile to the section total and should be directionally consistent with B1/B2 definitions.
4. B3: degrees must reconcile to completion reporting for Jul. 1, 2024-Jun. 30, 2025.
5. B4-B11: Pell + Stafford-only + neither must equal total for each line; `(D+E+F)/C*100 = H` for every column.
6. B22: retained / adjusted cohort * 100 must equal the reported retention rate.
7. C1 and D2: applicant/admit/enrolled totals must reconcile to gender and residency details where provided.
8. C11-C12: GPA must be unweighted only.
9. I-2: student-to-faculty ratio must use the CDS formula, not a dashboard ratio.
10. I-3: excluded sections must be documented.
11. J1: percentages must sum to exactly 100% after final rounding adjustment.
12. Never fill a field with `0` just because data is missing. Leave blank and log the exception.

### Better agent workflow

1. Load this registry and the CDS PDF field list.
2. Build the run plan from the section/source table above.
3. Ask for or confirm analyst-approved term codes, census dates, and production table names.
4. Run only `[SQL]` queries with confirmed parameters.
5. For `[ROLL]`, use prior-year CDS values only after a policy/contact confirmation step.
6. For `[EXT]`, output a department request list instead of filling guessed values.
7. For `[N/A]`, leave blank unless the specific field requires a literal `N/A`.
8. Fill only fields with a mapped value and a passed QA check.
9. Extract the filled PDF fields after writing and compare against the query result table.
10. Produce a final QA report for analyst sign-off.

## Assumptions and parameters to confirm before running

The attached PDF is the **Common Data Set 2025-2026** template. It asks for Fall 2025 enrollment values, July 1 2024-June 30 2025 degree completions, Fall 2019/2018 graduation-rate cohorts, Fall 2024-to-Fall 2025 retention, Fall 2025 admissions, and 2024-2025 financial-aid/dollar values where applicable.

Use these runtime parameters. The CSULB playbook says to confirm exact table names, filters, census dates, and term codes with the analyst before running final queries:

```yaml
params:
  fall_term: "20254"                    # Fall 2025 census term; verify PeopleSoft/IR term code
  prior_fall_term: "20244"              # Fall 2024 census term
  degree_award_terms: ["20243", "20244", "20251", "20252"]  # Jul 1 2024-Jun 30 2025; verify summer/fall/winter/spring mapping
  admission_fall_term: "20254"          # Fall 2025 first-year and transfer applicants
  grad_rate_cohort_current: "20194"     # Fall 2019 first-time full-time bachelor's cohort
  grad_rate_cohort_prior: "20184"       # Fall 2018 fallback cohort
  full_time_credit_threshold: 120        # Existing notebooks use >=120 as full-time
  ug_level_codes: ["1", "2", "3", "4"]
  grad_level_codes: ["5", "6", "7", "8", "9"]
```

> Important: Existing notebooks had older hard-coded terms such as `20234`, `20224`, `20214`, `20213-20222`, and `20154`. The agent should replace those with the parameters above before execution.

## Agent output contract

Each query should return either:

1. `pdf_field`, `value` rows ready to fill, or
2. dimensional rows plus an explicit mapping table in this file.

Preferred universal fill output:

```sql
SELECT 'PDF_FIELD_NAME' AS pdf_field, CAST(value AS STRING) AS value
```

The PDF fields below are real field names extracted from the attached form. For radio/check boxes, return the visible answer text (`Yes`, `No`, `Public`, etc.) only if your fill routine knows the export values; otherwise leave those for manual QA.

---

# Query registry

## Q-B1: Institutional Enrollment - Men and Women

**CDS section:** B1, page 5.  
**Purpose:** Fill full-time/part-time enrollment by level, degree status, first-time status, and sex.

**PDF fields filled:**

```text
EN_FRSH_FT_MEN_N, EN_FRSH_FT_WMN_N, CDS_EN_FRSH_FT_UNK_N,
EN_FRSH_PT_MEN_N, EN_FRSH_PT_WMN_N, CDS_EN_FRSH_PT_UNK_N,
EN_OTH_1ST_FT_MEN_N, EN_OTH_1ST_FT_WMN_N, CDS_EN_OTH_1ST_FT_UNK_N,
EN_OTH_1ST_PT_MEN_N, EN_OTH_1ST_PT_WMN_N, CDS_EN_OTH_1ST_PT_UNK_N,
EN_DEG_FT_MEN_N, EN_DEG_FT_WMN_N, CDS_EN_DEG_FT_UNK_N,
EN_DEG_PT_MEN_N, EN_DEG_PT_WMN_N, CDS_EN_DEG_PT_UNK_N,
EN_TOT_DEG_FT_MEN_N, EN_TOT_DEG_FT_WMN_N, CDS_EN_TOT_DEG_FT_UNK_N,
EN_TOT_DEG_PT_MEN_N, EN_TOT_DEG_PT_WMN_N, CDS_EN_TOT_DEG_PT_UNK_N,
EN_CRDT_FT_MEN_N, EN_CRDT_FT_WMN_N, CDS_EN_CRDT_FT_UNK_N,
EN_CRDT_PT_MEN_N, EN_CRDT_PT_WMN_N, CDS_EN_CRDT_PT_UNK_N,
EN_UG_FT_MEN_N, EN_UG_FT_WMN_N, CDS_EN_UG_FT_UNK_N,
EN_UG_PT_MEN_N, EN_UG_PT_WMN_N, CDS_EN_UG_PT_UNK_N,
EN_GRAD_DEG_FT_MEN_N, EN_GRAD_DEG_FT_WMN_N, CDS_EN_GRAD_DEG_FT_UNK_N,
EN_GRAD_DEG_PT_MEN_N, EN_GRAD_DEG_PT_WMN_N, CDS_EN_GRAD_DEG_PT_UNK_N,
EN_GRAD_OTH_FT_MEN_N, EN_GRAD_OTH_FT_WMN_N, CDS_EN_GRAD_OTH_FT_UNK_N,
EN_GRAD_OTH_PT_MEN_N, EN_GRAD_OTH_PT_WMN_N, CDS_EN_GRAD_OTH_PT_UNK_N,
EN_GRAD_CRDT_FT_MEN_N, EN_GRAD_CRDT_FT_WMN_N, CDS_EN_GRAD_CRDT_FT_UNK_N,
EN_GRAD_CRDT_PT_MEN_N, EN_GRAD_CRDT_PT_WMN_N, CDS_EN_GRAD_CRDT_PT_UNK_N,
EN_GRAD_FT_MEN_N, EN_GRAD_FT_WMN_N, CDS_EN_GRAD_FT_UNK_N,
EN_GRAD_PT_MEN_N, EN_GRAD_PT_WMN_N, CDS_EN_GRAD_PT_UNK_N,
EN_TOT_FT_MEN_N, EN_TOT_FT_WMN_N, CDS_EN_TOT_FT_UNK_N,
EN_TOT_PT_MEN_N, EN_TOT_PT_WMN_N, CDS_EN_TOT_PT_UNK_N,
EN_TOT_UG_N, EN_TOT_GRAD_N, EN_TOT _N
```

**SQL:**

```sql
WITH base AS (
  SELECT DISTINCT
      EMPLID,
      CASE
        WHEN CAST(YEARS AS INT) * 10 + CAST(TERM AS INT) >= 20254 THEN
          CASE
            WHEN GENDER_IDENTITY_CODE IN ('10') THEN 'MEN'
            WHEN GENDER_IDENTITY_CODE IN ('11') THEN 'WMN'
            ELSE 'UNK'
          END
        ELSE
          CASE
            WHEN SEX_CODE IN ('M','1') THEN 'MEN'
            WHEN SEX_CODE IN ('F','2') THEN 'WMN'
            ELSE 'UNK'
          END
      END AS sex_bucket,
      CASE
        WHEN COALESCE(TUA_LOWER_DIVISION, 0) + COALESCE(TUA_UPPER_DIVISION, 0)
             + COALESCE(TUA_GRADUATE, 0) + COALESCE(TUA_PRE_COLLEGIATE, 0)
             >= CASE WHEN STUDENT_LEVEL_CODE = '5' THEN 9 ELSE 12 END
          THEN 'FT'
        ELSE 'PT'
      END AS load_bucket,
      CASE
        WHEN STUDENT_LEVEL_CODE IN ('1','2','3','4') AND ENROLLMENT_STATUS = '5' THEN 'FRSH'
        WHEN STUDENT_LEVEL_CODE = '1' THEN 'OTH_1ST'
        WHEN STUDENT_LEVEL_CODE IN ('2','3','4') THEN 'DEG'
        WHEN STUDENT_LEVEL_CODE = '5' THEN 'GRAD'
        ELSE 'OTHER'
      END AS row_bucket
  FROM production.silver.erss
  WHERE YEARS || TERM = '${fall_term}'
)
SELECT
    row_bucket,
    load_bucket,
    sex_bucket,
    CAST(COUNT(DISTINCT EMPLID) AS STRING) AS value
FROM base
WHERE row_bucket <> 'OTHER'
GROUP BY row_bucket, load_bucket, sex_bucket;
```

**Post-processing:** Subtotal and total PDF fields are derived in the registry mapper from the dimensional row counts returned above. Do not trust row totals unless they exactly match the PDF row definitions.

---

## Q-B2: Enrollment by Racial/Ethnic Category

**CDS section:** B2, page 6.  
**Purpose:** Fill degree-seeking first-time first-year, all degree-seeking undergraduates, and total undergraduates by race/ethnicity.

**PDF field mapping:**

| Race/ethnicity result label | First-time first-year field | Degree-seeking UG field | Total UG field |
|---|---|---|---|
| Nonresidents | EN_1ST_NONRES_ALIEN_1ST_N | EN_NONRES_ALIEN_N | EN_TOT_NONRES_ALIEN_TOT_N |
| Hispanic/Latino | EN_1ST_HISPANIC_ETHNICITY_N | EN_HISPANIC_ETHNICITY_N | EN_TOT_HISPANIC_ETHNICITY_N |
| Black or African American | EN_1ST_BLACK_NONHISPANIC_N | EN_BLACK_NONHISPANIC_N | EN_TOT_BLACK_NONHISPANIC_N |
| White | EN_1ST_WHITE_NONHISPANIC_N | EN_WHITE_NONHISPANIC_N | EN_TOT_WHITE_NONHISPANIC_N |
| American Indian or Alaska Native | EN_1ST_NATIVE_NONHISPANIC_N | EN_NATIVE_NONHISPANIC_N | EN_TOT_NATIVE_NONHISPANIC_N |
| Asian | EN_1ST_ASIAN_NONHISPANIC_N | EN_ASIAN_NONHISPANIC_N | EN_TOT_ASIAN_NONHISPANIC_N |
| Native Hawaiian or Other Pacific Islander | EN_1ST_ISLANDER_NONHISPANIC_N | EN_ISLANDER_NONHISPANIC_N | EN_TOT_ISLANDER_NONHISPANIC_N |
| Two or More Races | EN_1ST_MULTIRACE_NONHISPANIC_N | EN_MULTIRACE_NONHISPANIC_N | EN_TOT_MULTIRACE_NONHISPANIC_N |
| Unknown | EN_1ST_RACE_ETHNICITY_UNKNOWN_N | EN_RACE_ETHNICITY_UNKNOWN_N | EN_TOT_RACE_ETHNICITY_UNKNOWN_N |
| Total | EN_1ST_RACE_ETHNICITY_TOT_N | EN_RACE_ETHNICITY_TOT_N | EN_TOT_RACE_ETHNICITY_TOT_N |

**SQL:**

```sql
WITH base AS (
  SELECT DISTINCT
      EMPLID,
      production.functions.ira_ethnicity(CITIZENSHIP_CODE, IPEDS_RACE_ETHNICITY_CATEGORY, ETHNIC_CODE_OLD, CAST(YEARS AS INT) * 10 + CAST(TERM AS INT)) AS race_ethnicity,
      CASE WHEN ENROLLMENT_STATUS = 5 THEN 1 ELSE 0 END AS is_first_time_first_year,
      CASE WHEN STUDENT_LEVEL_CODE IN ('1','2','3','4') THEN 1 ELSE 0 END AS is_degree_seeking_ug,
      1 AS is_total_ug
  FROM production.silver.erss
  WHERE YEARS || TERM = '${fall_term}'
    AND STUDENT_LEVEL_CODE IN ('1','2','3','4')
), agg AS (
  SELECT
      race_ethnicity,
      COUNT(DISTINCT CASE WHEN is_first_time_first_year=1 THEN EMPLID END) AS first_time_first_year_n,
      COUNT(DISTINCT CASE WHEN is_degree_seeking_ug=1 THEN EMPLID END) AS degree_seeking_ug_n,
      COUNT(DISTINCT EMPLID) AS total_ug_n
  FROM base
  GROUP BY race_ethnicity
)
SELECT * FROM agg
UNION ALL
SELECT 'Total', SUM(first_time_first_year_n), SUM(degree_seeking_ug_n), SUM(total_ug_n)
FROM agg
ORDER BY race_ethnicity;
```

---

## Q-B3: Degrees Awarded by Award Type

**CDS section:** B3, page 7.  
**Purpose:** Fill number of degrees awarded July 1, 2024-June 30, 2025.

**PDF fields filled:** `CERTIF_DIPLOMA_N`, `DEG_ASSOC_N`, `DEG_BACH_N`, `CERTIF_POST_BACH_N`, `DEG_MASTER_N`, `CERTIF_POST_MASTER_N`, `DEG_DOCTOR_RESEARCH_N`, `DEG_DOCTOR_PROF_N`, `DEG_DOCTOR_OTH_N`.

**SQL:**

```sql
WITH degrees AS (
  SELECT DISTINCT
      T1.EMPLID,
      T1.ACAD_PLAN,
      T1.DEGREE_LEVEL_CODE,
      CASE WHEN T2.MAJOR_CODE1 IS NOT NULL THEN 2 ELSE 1 END AS num_degrees
  FROM production.silver.ersd T1
  LEFT JOIN production.silver.ersd_supplemental T2
    ON T1.EMPLID = T2.EMPLID
   AND T1.YEARS || T1.TERM = T2.YEARS || T2.TERM
  WHERE T1.YEARS || T1.TERM IN (${degree_award_terms_sql_list})
), typed AS (
  SELECT
    CASE
      WHEN DEGREE_LEVEL_CODE IN ('0','1') THEN 'CERTIF_DIPLOMA_N'
      WHEN DEGREE_LEVEL_CODE = '2' THEN 'DEG_ASSOC_N'
      WHEN DEGREE_LEVEL_CODE IN ('3','4') THEN 'DEG_BACH_N'
      WHEN DEGREE_LEVEL_CODE = '5' THEN 'CERTIF_POST_BACH_N'
      WHEN DEGREE_LEVEL_CODE IN ('6','7') THEN 'DEG_MASTER_N'
      WHEN DEGREE_LEVEL_CODE = '8' AND SUBSTR(ACAD_PLAN,1,6) = 'EDADPH' THEN 'DEG_DOCTOR_OTH_N'
      WHEN DEGREE_LEVEL_CODE = '8' THEN 'DEG_DOCTOR_PROF_N'
      WHEN DEGREE_LEVEL_CODE = '9' THEN 'DEG_DOCTOR_RESEARCH_N'
      ELSE NULL
    END AS pdf_field,
    num_degrees
  FROM degrees
)
SELECT pdf_field, CAST(SUM(num_degrees) AS STRING) AS value
FROM typed
WHERE pdf_field IS NOT NULL
GROUP BY pdf_field;
```

**QA note:** The uploaded notebook maps `DEGREE_LEVEL_CODE = '8'` to doctoral degrees and then splits `EDADPH` as Doctoral - Other vs Professional Practice. Confirm whether research/scholarship doctoral degrees use a separate level/plan code at CSULB.

---

## Q-B4-B11: Graduation Rates by Pell/Stafford/No Aid Status

**CDS section:** B4-B11, pages 8-10.  
**Purpose:** Fill Fall 2019 and Fall 2018 graduation-rate grids. The PDF requires Pell, subsidized Stafford without Pell, no Pell/no Stafford, and total columns.

**PDF fields filled for current cohort:**

```text
GRS_BACH_INIT_PELL_N, GRS_BACH_INIT_STAFFORD_N, GRS_BACH_INIT_NO_AID_N, GRS_BACH_INIT_N,
GRS_BACH_EXCLUDE_PELL_N, GRS_BACH_EXCLUDE_STAFFORD_N, GRS_BACH_EXCLUDE_NO_AID_N, GRS_BACH_EXCLUDE_N,
GRS_BACH_ADJUST_PELL_N, GRS_BACH_ADJUST_STAFFORD_N, GRS_BACH_ADJUST_NO_AID_N, GRS_BACH_ADJUST_N,
GRS_4YR_PELL_N, GRS_4YR_STAFFORD_N, GRS_4YR_NO_AID_N, GRS_4YR_N,
GRS_5YR_PELL_N, GRS_5YR_STAFFORD_N, GRS_5YR_NO_AID_N, GRS_5YR_N,
GRS_6YR_PELL_N, GRS_6YR_STAFFORD_N, GRS_6YR_NO_AID_N, GRS_6YR_N,
GRS_BACH_PELL_N, GRS_BACH_STAFFORD_N, GRS_BACH_TOT_NO_AID_N, GRS_BACH_TOT_N,
GRS_BACH_PELL_P, GRS_BACH_STAFFORD_P, GRS_BACH_TOT_NO_AID_P, GRS_BACH_TOT_P
```

**PDF fields for prior cohort:** same names with `GRS_LY_` prefix.

**SQL:**

```sql
-- Run once with cohort_term='${grad_rate_cohort_current}' and prefix='GRS'.
-- Run again with cohort_term='${grad_rate_cohort_prior}' and prefix='GRS_LY'.
-- Requires table/view cds_fin_aid_status with: EMPLID, PELL, STAFFORD, NEITHER.

WITH degrees AS (
  SELECT
      ROW_NUMBER() OVER (PARTITION BY EMPLID ORDER BY YEARS || TERM ASC) AS rn,
      EMPLID,
      YEARS || TERM AS degree_term
  FROM production.silver.ersd
  WHERE DEGREE_LEVEL_CODE IN ('2','3','4')
), cohort AS (
  SELECT DISTINCT
      T1.EMPLID,
      T1.YEARS || T1.TERM AS cohort_term,
      CASE
        WHEN A.PELL = 'Yes' THEN 'PELL'
        WHEN A.STAFFORD = 'Yes' AND COALESCE(A.PELL,'No') <> 'Yes' THEN 'STAFFORD'
        ELSE 'NO_AID'
      END AS aid_bucket,
      1 AS init_n,
      0 AS exclude_n,
      1 AS adjust_n,
      CASE WHEN D.degree_term BETWEEN (T1.YEARS || T1.TERM)+8  AND (T1.YEARS || T1.TERM)+39 THEN 1 ELSE 0 END AS grad_4yr_n,
      CASE WHEN D.degree_term BETWEEN (T1.YEARS || T1.TERM)+40 AND (T1.YEARS || T1.TERM)+49 THEN 1 ELSE 0 END AS grad_5yr_n,
      CASE WHEN D.degree_term BETWEEN (T1.YEARS || T1.TERM)+50 AND (T1.YEARS || T1.TERM)+59 THEN 1 ELSE 0 END AS grad_6yr_n,
      CASE WHEN D.degree_term BETWEEN (T1.YEARS || T1.TERM)+8  AND (T1.YEARS || T1.TERM)+59 THEN 1 ELSE 0 END AS grad_total_n
  FROM production.silver.erss T1
  LEFT JOIN degrees D
    ON T1.EMPLID = D.EMPLID
   AND D.rn = 1
   AND D.degree_term BETWEEN (T1.YEARS || T1.TERM)+8 AND (T1.YEARS || T1.TERM)+59
  LEFT JOIN cds_fin_aid_status A
    ON T1.EMPLID = A.EMPLID
  WHERE T1.STUDENT_LEVEL_CODE <= 4
    AND T1.ENROLLMENT_STATUS = 5
    AND (T1.TUA_LOWER_DIVISION + T1.TUA_UPPER_DIVISION + T1.TUA_GRADUATE + T1.TUA_PRE_COLLEGIATE) >= ${full_time_credit_threshold}
    AND T1.YEARS || T1.TERM = '${cohort_term}'
), agg AS (
  SELECT aid_bucket,
         SUM(init_n) AS init_n,
         SUM(exclude_n) AS exclude_n,
         SUM(adjust_n) AS adjust_n,
         SUM(grad_4yr_n) AS grad_4yr_n,
         SUM(grad_5yr_n) AS grad_5yr_n,
         SUM(grad_6yr_n) AS grad_6yr_n,
         SUM(grad_total_n) AS grad_total_n,
         ROUND(SUM(grad_total_n) / NULLIF(SUM(adjust_n),0) * 100, 2) AS grad_rate_p
  FROM cohort
  GROUP BY aid_bucket
), total AS (
  SELECT 'TOTAL' AS aid_bucket,
         SUM(init_n), SUM(exclude_n), SUM(adjust_n), SUM(grad_4yr_n), SUM(grad_5yr_n), SUM(grad_6yr_n), SUM(grad_total_n),
         ROUND(SUM(grad_total_n) / NULLIF(SUM(adjust_n),0) * 100, 2)
  FROM agg
)
SELECT * FROM agg
UNION ALL
SELECT * FROM total;
```

**Mapping rule:** For each returned `aid_bucket`, map to the suffix: `PELL`, `STAFFORD`, `NO_AID`, or no suffix for total. For example, current cohort `aid_bucket='PELL'` maps `init_n` to `GRS_BACH_INIT_PELL_N`, `grad_4yr_n` to `GRS_4YR_PELL_N`, and `grad_rate_p` to `GRS_BACH_PELL_P`.

---

## Q-B22: Retention Rate

**CDS section:** B22, page 12.  
**Purpose:** Fill Fall 2024 cohort, retained in Fall 2025.

**PDF fields filled:** `RETENTION_FRSH_N`, `RETENTION_ENROLL_N`, `RETENTION_FRSH_P`.

**SQL:**

```sql
WITH returned AS (
  SELECT DISTINCT EMPLID AS returned_emplid
  FROM production.silver.erss
  WHERE YEARS || TERM = '${fall_term}'
    AND STUDENT_LEVEL_CODE < 5
), cohort AS (
  SELECT DISTINCT EMPLID
  FROM production.silver.erss
  WHERE YEARS || TERM = '${prior_fall_term}'
    AND STUDENT_LEVEL_CODE < 5
    AND ENROLLMENT_STATUS = 5
    AND (TUA_LOWER_DIVISION + TUA_UPPER_DIVISION + TUA_GRADUATE + TUA_PRE_COLLEGIATE) >= ${full_time_credit_threshold}
)
SELECT 'RETENTION_FRSH_N' AS pdf_field, CAST(COUNT(DISTINCT C.EMPLID) AS STRING) AS value FROM cohort C
UNION ALL
SELECT 'RETENTION_ENROLL_N', CAST(COUNT(DISTINCT R.returned_emplid) AS STRING)
FROM cohort C LEFT JOIN returned R ON C.EMPLID = R.returned_emplid
UNION ALL
SELECT 'RETENTION_FRSH_P', CAST(ROUND(COUNT(DISTINCT R.returned_emplid) / NULLIF(COUNT(DISTINCT C.EMPLID),0) * 100, 2) AS STRING)
FROM cohort C LEFT JOIN returned R ON C.EMPLID = R.returned_emplid;
```

---

## Q-C1-C2: First-Time, First-Year Applications, Admits, and Enrollees

**CDS section:** C1-C2, pages 13-14.  
**Purpose:** Fill first-time first-year applicants/admitted/enrolled by sex, load, and residency.

**PDF fields filled:**

```text
AP_RECD_1ST_MEN_N, AP_RECD_1ST_WMN_N, AP_RECD_1ST_UNK_N,
AP_ADMT_1ST_MEN_N, AP_ADMT_1ST_WMN_N, AP_ADMT_1ST_UNK_N,
EN_TOT_1ST_MEN_N, EN_TOT_1ST_WMN_N, EN_TOT_1ST_UNK_N,
EN_TOT_1ST_FT_MEN_N, EN_TOT_1ST_PT_MEN_N,
EN_TOT_1ST_FT_WMN_N, EN_TOT_1ST_PT_WMN_N,
EN_TOT_1ST_FT_UNK_N, EN_TOT_1ST_PT_UNK_N,
AP_RECD_STATE_1ST_N, AP_RECD_NRES_1ST_N, AP_RECD_INTL_1ST_N, AP_RECD_UNK_1ST_N, AP_RECD_1ST_N,
AP_ADMT_STATE_1ST_N, AP_ADMT_NRES_1ST_N, AP_ADMT_INTL_1ST_N, AP_ADMT_UNK_1ST_N, AP_ADMT_1ST_N,
EN_TOT_STATE_1ST_N, EN_TOT_NRES_1ST_N, EN_TOT_INTL_1ST_N, EN_TOT_UNK_1ST_N, EN_TOT_1ST_N
```

**SQL:**

```sql
WITH enrolled AS (
  SELECT DISTINCT
      EMPLID,
      CAST(YEARS AS INT) * 10 + CAST(TERM AS INT) AS year_term,
      CASE
        WHEN (COALESCE(TUA_PRE_COLLEGIATE,0) + COALESCE(TUA_LOWER_DIVISION,0) + COALESCE(TUA_UPPER_DIVISION,0) + COALESCE(TUA_GRADUATE,0)) >= ${full_time_credit_threshold}
          THEN 'FT' ELSE 'PT'
      END AS load_bucket
  FROM production.silver.erss
  WHERE STUDENT_LEVEL_CODE < 5
    AND ENROLLMENT_STATUS = 5
    AND CAST(YEARS AS INT) * 10 + CAST(TERM AS INT) = ${admission_fall_term}
), apps AS (
  SELECT DISTINCT
      A.EMPLID,
      CASE WHEN TYLERN.GENDER(A.GENDER_IDENTITY_CODE, A.SEX_CODE, ${admission_fall_year}, 4) = 'Man' THEN 'MEN'
           WHEN TYLERN.GENDER(A.GENDER_IDENTITY_CODE, A.SEX_CODE, ${admission_fall_year}, 4) = 'Woman' THEN 'WMN'
           ELSE 'UNK' END AS sex_bucket,
      CASE WHEN A.RESIDENCE_CODE IS NULL THEN 'UNK'
           WHEN A.RESIDENCE_CODE < 60 THEN 'STATE'
           WHEN A.RESIDENCE_CODE > 60 AND A.RESIDENCE_CODE < 7000 THEN 'NRES'
           WHEN A.RESIDENCE_CODE >= 7000 THEN 'INTL'
           ELSE 'UNK' END AS residency_bucket,
      1 AS applied,
      CASE WHEN A.ADMISSION_STATUS IN ('A','C','F','H','N','P') THEN 1 ELSE 0 END AS admitted,
      CASE WHEN E.EMPLID IS NOT NULL THEN 1 ELSE 0 END AS enrolled,
      E.load_bucket
  FROM production.silver.ersa A
  LEFT JOIN enrolled E
    ON A.EMPLID = E.EMPLID
   AND CAST(A.YEARS AS INT) * 10 + CAST(A.TERM AS INT) = E.year_term
  WHERE A.STUDENT_LEVEL_CODE < 5
    AND A.ENROLLMENT_STATUS = 5
    AND A.ACCOMMODATION_STATUS IN ('A','B','R')
    AND CAST(A.YEARS AS INT) * 10 + CAST(A.TERM AS INT) = ${admission_fall_term}
)
SELECT 'sex' AS dimension, sex_bucket AS bucket,
       SUM(applied) AS applied_n, SUM(admitted) AS admitted_n, SUM(enrolled) AS enrolled_n,
       SUM(CASE WHEN load_bucket='FT' THEN enrolled ELSE 0 END) AS enrolled_ft_n,
       SUM(CASE WHEN load_bucket='PT' THEN enrolled ELSE 0 END) AS enrolled_pt_n
FROM apps
GROUP BY sex_bucket
UNION ALL
SELECT 'residency', residency_bucket,
       SUM(applied), SUM(admitted), SUM(enrolled), NULL, NULL
FROM apps
GROUP BY residency_bucket
UNION ALL
SELECT 'total', 'TOTAL', SUM(applied), SUM(admitted), SUM(enrolled), NULL, NULL
FROM apps;
```

---

## Q-C11-C12-C13: High School GPA Distribution and Average GPA

**CDS section:** C11-C12, page 21.  
**Purpose:** Fill GPA bands, percent submitting GPA, and average high-school GPA.

**PDF fields filled:** `FRSH_GPA_SUBMIT_1_P` through `FRSH_GPA_SUBMIT_9_P`, `TOT_FRSH_GPA_SUBMIT_P`, `EN_FRSH_GPA_1_P` through `EN_FRSH_GPA_9_P`, `TOT_EN_FRSH_GPA_P`, and average-GPA field if present in your local form version.

**SQL:**

```sql
WITH fr AS (
  SELECT DISTINCT
      EMPLID,
      HS_GPA,
      HS_GPA / 100 AS hs_gpa,
      CASE WHEN HS_GPA != 0 THEN 1 ELSE 0 END AS has_gpa
  FROM production.silver.erss
  WHERE STUDENT_LEVEL_CODE < 5
    AND ENROLLMENT_STATUS = 5
    AND CAST(YEARS AS INT) * 10 + CAST(TERM AS INT) = ${admission_fall_term}
), bins AS (
  SELECT
      EMPLID,
      hs_gpa,
      has_gpa,
      CASE
        WHEN HS_GPA >= 400 THEN '1'
        WHEN HS_GPA >= 375 AND HS_GPA < 400 THEN '2'
        WHEN HS_GPA >= 350 AND HS_GPA < 375 THEN '3'
        WHEN HS_GPA >= 325 AND HS_GPA < 350 THEN '4'
        WHEN HS_GPA >= 300 AND HS_GPA < 325 THEN '5'
        WHEN HS_GPA >= 250 AND HS_GPA < 300 THEN '6'
        WHEN HS_GPA >= 200 AND HS_GPA < 250 THEN '7'
        WHEN HS_GPA >= 100 AND HS_GPA < 200 THEN '8'
        WHEN HS_GPA < 100 AND HS_GPA IS NOT NULL THEN '9'
      END AS gpa_bin
  FROM fr
)
SELECT
    gpa_bin,
    COUNT(DISTINCT CASE WHEN has_gpa=1 THEN EMPLID END) AS submitted_count,
    ROUND(COUNT(DISTINCT CASE WHEN has_gpa=1 THEN EMPLID END) / NULLIF(SUM(COUNT(DISTINCT CASE WHEN has_gpa=1 THEN EMPLID END)) OVER (),0) * 100, 2) AS pct_of_submitters,
    ROUND(COUNT(DISTINCT EMPLID) / NULLIF((SELECT COUNT(DISTINCT EMPLID) FROM fr),0) * 100, 2) AS pct_of_all_enrolled
FROM bins
WHERE gpa_bin IS NOT NULL
GROUP BY gpa_bin
UNION ALL
SELECT 'TOTAL_SUBMITTED', COUNT(DISTINCT CASE WHEN has_gpa=1 THEN EMPLID END),
       ROUND(COUNT(DISTINCT CASE WHEN has_gpa=1 THEN EMPLID END) / NULLIF(COUNT(DISTINCT EMPLID),0) * 100, 2),
       100.00
FROM fr
UNION ALL
SELECT 'AVG_HSGPA', NULL, ROUND(AVG(CASE WHEN has_gpa=1 THEN hs_gpa END), 3), NULL
FROM fr;
```

---

## Q-D2: Transfer Applicants, Admits, and Enrollees

**CDS section:** D2, page 24.  
**Purpose:** Fill transfer applicants, admitted applicants, and enrolled applicants by sex and totals.

**PDF fields filled:** `AP_TFER_MEN_N`, `AP_TFER_WMN_N`, `AP_TFER_UNK_N`, `AP_TFER_N`, `AD_TFER_MEN_N`, `AD_TFER_WMN_N`, `AD_TFER_UNK_N`, `AD_TFER_N`, `EN_TFER_MEN_N`, `EN_TFER_WMN_N`, `EN_TFER_UNK_N`, `EN_TFER_N`.

**SQL:**

```sql
WITH enrolled AS (
  SELECT DISTINCT EMPLID AS erss_emplid, YEARS || TERM AS year_term
  FROM production.silver.erss
  WHERE STUDENT_LEVEL_CODE < 5
    AND ENROLLMENT_STATUS = 4
    AND YEARS || TERM = '${admission_fall_term}'
), apps AS (
  SELECT DISTINCT
      A.EMPLID,
      CASE WHEN A.SEX_CODE IN ('M','1','MALE') THEN 'MEN'
           WHEN A.SEX_CODE IN ('F','2','FEMALE') THEN 'WMN'
           ELSE 'UNK' END AS sex_bucket,
      1 AS applied,
      CASE WHEN A.ADMISSION_STATUS IN ('A','C','F','H','N','P') THEN 1 ELSE 0 END AS admitted,
      CASE WHEN E.erss_emplid IS NOT NULL THEN 1 ELSE 0 END AS enrolled
  FROM production.silver.ersa A
  LEFT JOIN enrolled E
    ON A.EMPLID = E.erss_emplid
   AND A.YEARS || A.TERM = E.year_term
  WHERE A.STUDENT_LEVEL_CODE < 5
    AND A.ENROLLMENT_STATUS = 4
    AND A.ACCOMMODATION_STATUS IN ('A','B','R')
    AND A.YEARS || A.TERM = '${admission_fall_term}'
)
SELECT sex_bucket, SUM(applied) AS applicants, SUM(admitted) AS admitted, SUM(enrolled) AS enrolled
FROM apps
GROUP BY sex_bucket
UNION ALL
SELECT 'TOTAL', SUM(applied), SUM(admitted), SUM(enrolled)
FROM apps;
```

---

## Q-F1: Student Life Percentages - Age and Residency

**CDS section:** F1, page 28.  
**Purpose:** Fill the available F1 numeric fields from existing notebooks: nonresident percentage, age 25+ percentage, and average ages.

**PDF fields filled:** `EN_1ST_NRES_P`, `EN_NRES_P`, `EN_1ST_OLD_P`, `EN_OLD_P`, `EN_1ST_OLD_FT`, `EN_OLD_FT`, `EN_1ST_OLD_ALL`, `EN_OLD_ALL`.

**SQL:**

```sql
WITH base AS (
  SELECT DISTINCT
      A.EMPLID,
      A.ENROLLMENT_STATUS,
      A.BIRTH_DATE,
      A.YEARS,
      CASE WHEN B.RESIDENCE_STATUS = 'N' THEN 1 ELSE 0 END AS domestic_nonresident,
      CASE WHEN B.RESIDENCE_STATUS = 'F' THEN 1 ELSE 0 END AS international_flag,
      ROUND(((TO_DATE(('15-' || 'SEP-' || A.YEARS), 'DD, MON, YYYY') - TO_DATE(A.BIRTH_DATE))/365), 2) AS age,
      CASE WHEN (A.TUA_LOWER_DIVISION + A.TUA_UPPER_DIVISION + A.TUA_GRADUATE + A.TUA_PRE_COLLEGIATE) >= ${full_time_credit_threshold}
           THEN 'FT' ELSE 'PT' END AS load_bucket
  FROM production.silver.erss A
  LEFT JOIN production.silver.ersa B
    ON A.EMPLID = B.EMPLID
   AND A.MATRICULATION_PERIOD = B.YEARS || B.TERM
  WHERE A.STUDENT_LEVEL_CODE IN ('1','2','3','4')
    AND A.YEARS || A.TERM = '${fall_term}'
)
SELECT 'EN_1ST_NRES_P' AS pdf_field,
       CAST(ROUND(SUM(CASE WHEN ENROLLMENT_STATUS=5 AND domestic_nonresident=1 THEN 1 ELSE 0 END) / NULLIF(SUM(CASE WHEN ENROLLMENT_STATUS=5 AND international_flag=0 THEN 1 ELSE 0 END),0) * 100, 2) AS STRING) AS value
FROM base
UNION ALL
SELECT 'EN_NRES_P', CAST(ROUND(SUM(CASE WHEN domestic_nonresident=1 THEN 1 ELSE 0 END) / NULLIF(SUM(CASE WHEN international_flag=0 THEN 1 ELSE 0 END),0) * 100, 2) AS STRING)
FROM base
UNION ALL
SELECT 'EN_1ST_OLD_P', CAST(ROUND(SUM(CASE WHEN ENROLLMENT_STATUS=5 AND age >= 25 THEN 1 ELSE 0 END) / NULLIF(SUM(CASE WHEN ENROLLMENT_STATUS=5 THEN 1 ELSE 0 END),0) * 100, 2) AS STRING)
FROM base
UNION ALL
SELECT 'EN_OLD_P', CAST(ROUND(SUM(CASE WHEN age >= 25 THEN 1 ELSE 0 END) / NULLIF(COUNT(*),0) * 100, 2) AS STRING)
FROM base
UNION ALL
SELECT 'EN_1ST_OLD_FT', CAST(ROUND(AVG(CASE WHEN ENROLLMENT_STATUS=5 AND load_bucket='FT' THEN age END), 2) AS STRING)
FROM base
UNION ALL
SELECT 'EN_OLD_FT', CAST(ROUND(AVG(CASE WHEN load_bucket='FT' THEN age END), 2) AS STRING)
FROM base
UNION ALL
SELECT 'EN_1ST_OLD_ALL', CAST(ROUND(AVG(CASE WHEN ENROLLMENT_STATUS=5 THEN age END), 2) AS STRING)
FROM base
UNION ALL
SELECT 'EN_OLD_ALL', CAST(ROUND(AVG(age), 2) AS STRING)
FROM base;
```

---

## Q-I1: Instructional Faculty Counts

**CDS section:** I-1, page 39.  
**Purpose:** Fill instructional faculty counts by full-time/part-time status for total faculty, minority faculty, women, men, nonresident faculty, degree groups, and graduate-only faculty.

**Analyst reference:** Derived from `Section I.sql`. Faculty population uses `production.silver.ira_faculty`, Fall 2025 faculty term `${faculty_fall_term}`, instructional job codes `2360,2361,2481,2482,2387,2388,2321,2358,2359`, and an analyst-provided `NRA_STATUS` / `TERMINAL_MASTERS` lookup table `${faculty_nra_tm_table}`.

**PDF fields filled:**

```text
FT_N, PT_N, TOT_N,
MIN_FT_N, MIN_PT_N, MIN_TOT_N,
FT_WMN_N, PT_WMN_N, TOT_WMN_N,
FT_MEN_N, PT_MEN_N, TOT_MEN_N,
NRES_FT_N, NRES_PT_N, NRES_TOT_N,
FT_DEG_TERM_N, PT_DEG_TERM_N, TOT_DEG_TERM_N,
MASTER_FT_N, MASTER_PT_N, MASTER_TOT_N,
BACH_FT_N, BACH_PT_N, BACH_TOT_N,
UNKNOWN_FT_N, UNKNOWN_PT_N, UNKNOWN_TOT_N,
GRAD_FT_N, GRAD_PT_N, GRAD_TOT_N
```

**SQL:**

```sql
WITH faculty_pop_with_dupes AS (
  SELECT DISTINCT
      TERM,
      EMPLID,
      CASE WHEN FTE >= 1 THEN 'Full Time' ELSE 'Part Time' END AS ft_pt_status,
      FTE,
      ETHNIC_GROUP_DESCR AS ethnicity,
      GENDER AS gender,
      HIGHEST_EDUC_LVL_DESCR AS education,
      DEPTID
  FROM production.silver.ira_faculty
  WHERE TERM = ${faculty_fall_term}
    AND JOBCODE IN (2360,2361,2481,2482,2387,2388,2321,2358,2359)
), faculty_pop AS (
  SELECT TERM, EMPLID, ft_pt_status, FTE, ethnicity, gender, education, DEPTID
  FROM (
    SELECT
        TERM, EMPLID, ft_pt_status, FTE, ethnicity, gender, education, DEPTID,
        ROW_NUMBER() OVER (PARTITION BY EMPLID ORDER BY FTE DESC) AS rn
    FROM faculty_pop_with_dupes
  )
  WHERE rn = 1
), nra_tm AS (
  SELECT DISTINCT EMPLID, NRA_STATUS, TERMINAL_MASTERS
  FROM ${faculty_nra_tm_table}
), metric_rows AS (
  SELECT 'TOTAL' AS metric, ft_pt_status, EMPLID
  FROM faculty_pop
  UNION ALL
  SELECT 'MINORITY', ft_pt_status, EMPLID
  FROM faculty_pop
  WHERE ethnicity IN ('Black/African American', 'American Indian/Alaska Native', 'Asian',
                      'Native Hawaiian/Oth Pac Island', 'Hispanic/Latino')
  UNION ALL
  SELECT 'WOMEN', ft_pt_status, EMPLID
  FROM faculty_pop
  WHERE gender = 'F'
  UNION ALL
  SELECT 'MEN', ft_pt_status, EMPLID
  FROM faculty_pop
  WHERE gender = 'M'
  UNION ALL
  SELECT 'NONRESIDENT', F.ft_pt_status, F.EMPLID
  FROM faculty_pop F
  INNER JOIN nra_tm N ON F.EMPLID = N.EMPLID
  WHERE N.NRA_STATUS = 'Yes'
  UNION ALL
  SELECT 'TERMINAL_DEGREE', F.ft_pt_status, F.EMPLID
  FROM faculty_pop F
  LEFT JOIN nra_tm N ON F.EMPLID = N.EMPLID
  WHERE F.education = 'Doctorate Level Degree'
     OR (F.education = 'Master''s Level Degree' AND N.TERMINAL_MASTERS = 'Yes')
  UNION ALL
  SELECT 'MASTERS_NON_TERMINAL', F.ft_pt_status, F.EMPLID
  FROM faculty_pop F
  LEFT JOIN nra_tm N ON F.EMPLID = N.EMPLID
  WHERE F.education = 'Master''s Level Degree'
    AND COALESCE(N.TERMINAL_MASTERS, 'No') != 'Yes'
  UNION ALL
  SELECT 'BACHELORS', ft_pt_status, EMPLID
  FROM faculty_pop
  WHERE education = 'Bachelor''s Level Degree'
  UNION ALL
  SELECT 'OTHER_UNKNOWN', ft_pt_status, EMPLID
  FROM faculty_pop
  WHERE education NOT IN ('Doctorate Level Degree', 'Master''s Level Degree', 'Bachelor''s Level Degree')
     OR education IS NULL
  UNION ALL
  SELECT 'GRAD_ONLY', ft_pt_status, EMPLID
  FROM faculty_pop
  WHERE DEPTID IN ('00421', '00158', '00263', '00138', '00698', '00414')
), counts AS (
  SELECT metric, ft_pt_status, COUNT(DISTINCT EMPLID) AS headcount
  FROM metric_rows
  GROUP BY metric, ft_pt_status
)
SELECT
    metric,
    SUM(CASE WHEN ft_pt_status = 'Full Time' THEN headcount ELSE 0 END) AS ft_n,
    SUM(CASE WHEN ft_pt_status = 'Part Time' THEN headcount ELSE 0 END) AS pt_n,
    SUM(headcount) AS total_n
FROM counts
GROUP BY metric;
```

---

## Q-J: Disciplinary Areas of Degrees Conferred by CIP

**CDS section:** J, pages 41-42.  
**Purpose:** Fill percentages of certificates/diplomas, associate, and bachelor's degrees by CIP category.

**PDF fields filled:** pages 41-42 fields such as `CERTIF_P_AGR`, `ASSOC_AGR`, `BACH_AGR`, through `CERTIF_P_TOT_P`, `ASSOC_TOT_P`, `BACH_TOT_P`.

**SQL:**

```sql
WITH degrees AS (
  SELECT DISTINCT
      T1.SSN AS ersd_ssn,
      T1.EMPLID AS ersd_emplid,
      T1.DEGREE_LEVEL_CODE,
      T1.MAJOR_CODE AS first_major,
      T2.MAJOR_CODE1 AS second_major
  FROM production.silver.ersd T1
  LEFT JOIN production.silver.ersd_supplemental T2
    ON T1.SSN = T2.SSN
   AND CONCAT(TO_CHAR(T1.YEARS, '9999'), T1.TERM) = CONCAT(TO_CHAR(T2.YEARS, '9999'), T2.TERM)
  WHERE CONCAT(TO_CHAR(T1.YEARS, '9999'), T1.TERM) IN (${degree_award_terms_sql_list})
), stacked AS (
  SELECT ersd_ssn, ersd_emplid, degree_level_code, first_major AS degree_code FROM degrees
  UNION ALL
  SELECT ersd_ssn, ersd_emplid, degree_level_code, second_major AS degree_code FROM degrees WHERE second_major IS NOT NULL
), cip AS (
  SELECT * FROM iramaster.SS_HEGIS_CIP WHERE OBSOLETE_CODE = 0
), joined AS (
  SELECT
      S.degree_level_code,
      S.degree_code,
      C.CIP,
      CASE WHEN LENGTH(C.CIP) = 5 THEN CONCAT('0', SUBSTR(C.CIP,1,1)) ELSE SUBSTR(C.CIP,1,2) END AS short_cip
  FROM stacked S
  LEFT JOIN cip C ON S.degree_code = C.HEGIS_CODE
), grouped AS (
  SELECT
      CASE
        WHEN degree_level_code IN ('0','1') THEN 'CERTIF'
        WHEN degree_level_code = '2' THEN 'ASSOC'
        WHEN degree_level_code IN ('3','4') THEN 'BACH'
      END AS award_bucket,
      short_cip,
      COUNT(*) AS num_degrees
  FROM joined
  WHERE degree_level_code IN ('0','1','2','3','4')
  GROUP BY CASE
        WHEN degree_level_code IN ('0','1') THEN 'CERTIF'
        WHEN degree_level_code = '2' THEN 'ASSOC'
        WHEN degree_level_code IN ('3','4') THEN 'BACH'
      END, short_cip
)
SELECT award_bucket, short_cip,
       num_degrees,
       ROUND(num_degrees / SUM(num_degrees) OVER (PARTITION BY award_bucket) * 100, 2) AS percent
FROM grouped
ORDER BY award_bucket, short_cip;
```

**Mapping rule:** Map `short_cip` to the CDS J row abbreviation and award bucket to the field prefix. Examples: `BACH + 01 -> BACH_AGR`, `ASSOC + 11 -> ASSOC_CIS`, `CERTIF + 51 -> CERTIF_P_HEALTH`. Return totals as `CERTIF_P_TOT_P`, `ASSOC_TOT_P`, and `BACH_TOT_P = 100` after QA.

---

# Missing or manual sections

The uploaded notebooks cover useful portions of B, C, D, F, and J. They do **not** contain complete SQL for every field in the 50-page PDF. Treat these as manual, policy-owner, or separate-system sections unless you add new data sources:

- A0-A6: respondent, institutional contact, control, calendar, degrees offered, campus belonging URL.
- C3-C10 and C13-C22: admission policies, deadlines, fees, test policies, early decision/action, waitlist details unless admissions tables contain those policy values.
- E: academic offerings and graduation requirements.
- G: annual costs. Use budget/COA tables, not ERSS/ERSA.
- H: financial aid. Use financial-aid award tables, not the enrollment-only queries above.
- I: faculty and class size. Use HR/faculty workload and class-section enrollment tables.
- Some B1 categories such as non-degree credit-only students may require extra coding beyond the ERSS degree-seeking logic.

---

# Minimal fill-agent prompt

Use this prompt with an LLM/agent that has Databricks SQL execution and PDF form-fill tools:

```text
You are filling the CSULB Common Data Set 2025-2026 PDF. Use the SQL Query Registry in this markdown file. Replace parameters using the params block. Run each SQL query. For every result, map output columns to the named PDF fields exactly as listed. For percentage fields ending in _P, write percentages on the 0-100 scale with up to two decimals. For count fields ending in _N, write integer counts. For dollar fields ending in _D, write whole dollars unless the source says otherwise. Do not invent missing values. If a query returns no rows for a field, leave it blank and report it in a QA exception log. After filling, re-open the PDF, extract field values, and verify all mapped fields are populated and subtotal fields equal the sum of their components.
```

# QA checks before final PDF

1. B1 total all students equals total undergraduates plus total graduates.
2. B2 race/ethnicity totals equal row sums and B1 undergraduate total where definitions align.
3. B3 degrees should reconcile to IPEDS completions for July 1, 2024-June 30, 2025.
4. B4-B11 Pell + Stafford + No Aid should equal total for each line A-G.
5. B22 retention percent should equal retained / cohort * 100.
6. C1 total applicants/admits/enrollees should equal the sex-detail sums.
7. D2 total transfer applicants/admits/enrollees should equal the sex-detail sums.
8. J totals by award bucket should equal 100% after rounding; use a final rounding adjustment if required.

# Source coverage notes

- Existing Databricks notebook `CDS.html` contains SQL for B2 race/ethnicity, B3 degrees, B4-B11 graduation rates, C1 first-year admissions, D transfer admissions, F age/residency percentages, B22 retention, and historical J degree-by-CIP logic.
- Existing Databricks notebook `(Clone) CDS_J.html` contains a newer J-section workflow using `production.silver.ersd`, `production.silver.ersd_supplemental`, and a CIP/HEGIS crosswalk.
- The attached PDF contains the fillable field names and the CDS definitions/layout used for the mappings above.


# Playbook-specific instructions added in latest revision

The latest context file, `CDS_2025_2026_Playbook_v5.pdf`, clarifies that CSULB's official data source is ERS via Databricks SQL, not dashboards or direct IPEDS exports. It also clarifies that sections are a mix of `[SQL]`, `[ROLL]`, `[EXT]`, `[NEW]`, and `[N/A]`; therefore a fully automated PDF-fill agent must not try to SQL-fill every PDF field. The agent should produce three outputs: `(1)` a field-value table for SQL-fillable items, `(2)` a manual/rollover/external request list, and `(3)` a QA exception log for analyst sign-off.
