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
  admission_fall_year: 2025             # Calendar year for TYLERN.GENDER() call in C1
  faculty_fall_term: "2254"             # 4-digit faculty term for ira_faculty; 2254 = Fall 2025
  faculty_nra_tm_table: "TYLERN.CDS_FACULTY_NRA_TM_F25_TBL"  # Faculty Affairs NRA/TM lookup; update each year
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
EN_TOT_UG_N, EN_TOT_GRAD_N, EN_TOT_N
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
        WHEN STUDENT_LEVEL_CODE = '0' THEN 'CRDT'
        WHEN STUDENT_LEVEL_CODE = '5' THEN 'GRAD_DEG'
        WHEN STUDENT_LEVEL_CODE IN ('6','7','8','9') THEN 'GRAD_CRDT'
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
      CASE
        WHEN CAST(A.YEARS AS INT) * 10 + CAST(A.TERM AS INT) >= 20254 THEN
          CASE WHEN A.GENDER_IDENTITY_CODE IN ('10') THEN 'MEN'
               WHEN A.GENDER_IDENTITY_CODE IN ('11') THEN 'WMN'
               ELSE 'UNK' END
        ELSE
          CASE WHEN A.SEX_CODE IN ('M','1') THEN 'MEN'
               WHEN A.SEX_CODE IN ('F','2') THEN 'WMN'
               ELSE 'UNK' END
      END AS sex_bucket,
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
      CASE
        WHEN CAST(A.YEARS AS INT) * 10 + CAST(A.TERM AS INT) >= 20254 THEN
          CASE WHEN A.GENDER_IDENTITY_CODE IN ('10') THEN 'MEN'
               WHEN A.GENDER_IDENTITY_CODE IN ('11') THEN 'WMN'
               ELSE 'UNK' END
        ELSE
          CASE WHEN A.SEX_CODE IN ('M','1') THEN 'MEN'
               WHEN A.SEX_CODE IN ('F','2') THEN 'WMN'
               ELSE 'UNK' END
      END AS sex_bucket,
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

## Q-I2: Student-to-Faculty Ratio

**CDS section:** I-2, page 39.
**Purpose:** Fill the official student-to-faculty ratio using the CDS formula.

**PDF fields filled:** `STUFAC_RATIO_N` (integer ratio, e.g., `27` for 27:1).

**CDS formula:** `(FT UG students + 1/3 × PT UG students) / (FT instructional faculty + 1/3 × PT instructional faculty)`

**SQL:**

```sql
-- Numerator: FT + 1/3 PT undergraduate students (degree-seeking, Fall 2025)
WITH stu AS (
  SELECT
    COUNT(DISTINCT CASE
      WHEN (COALESCE(TUA_PRE_COLLEGIATE,0)+COALESCE(TUA_LOWER_DIVISION,0)+COALESCE(TUA_UPPER_DIVISION,0)+COALESCE(TUA_GRADUATE,0)) >= ${full_time_credit_threshold}
      THEN EMPLID END) AS ft_ug,
    COUNT(DISTINCT CASE
      WHEN (COALESCE(TUA_PRE_COLLEGIATE,0)+COALESCE(TUA_LOWER_DIVISION,0)+COALESCE(TUA_UPPER_DIVISION,0)+COALESCE(TUA_GRADUATE,0)) < ${full_time_credit_threshold}
      THEN EMPLID END) AS pt_ug
  FROM production.silver.erss
  WHERE CAST(YEARS AS INT) * 10 + CAST(TERM AS INT) = ${fall_term}
    AND STUDENT_LEVEL_CODE IN ('1','2','3','4')
    AND ENROLLMENT_STATUS = 5
),
-- Denominator: FT + 1/3 PT instructional faculty (faculty term, e.g., 2254 for Fall 2025)
fac AS (
  WITH fac_pop AS (
    SELECT EMPLID,
           CASE WHEN FTE >= 1 THEN 'FT' ELSE 'PT' END AS ft_pt
    FROM (
      SELECT EMPLID, FTE,
             ROW_NUMBER() OVER (PARTITION BY EMPLID ORDER BY FTE DESC) AS rn
      FROM production.silver.ira_faculty
      WHERE TERM = ${faculty_fall_term}
        AND JOBCODE IN (2360,2361,2481,2482,2387,2388,2321,2358,2359)
    ) x WHERE rn = 1
  )
  SELECT
    COUNT(DISTINCT CASE WHEN ft_pt='FT' THEN EMPLID END) AS ft_fac,
    COUNT(DISTINCT CASE WHEN ft_pt='PT' THEN EMPLID END) AS pt_fac
  FROM fac_pop
)
SELECT
  'STUFAC_RATIO_N' AS pdf_field,
  CAST(ROUND((s.ft_ug + s.pt_ug / 3.0) / NULLIF((f.ft_fac + f.pt_fac / 3.0), 0), 0) AS STRING) AS value
FROM stu s CROSS JOIN fac f;
```

**Params note:** Add `faculty_fall_term: "2254"` to the params block (4-digit faculty term; `2254` = Fall 2025). Confirm with analyst — faculty census is Nov. 1, 2025.

**QA:** Prior-year baseline was 27:1. Flag if result differs by more than ±2.

---

## Q-I3: Class Size Distribution

**CDS section:** I-3, page 40.
**Purpose:** Fill count of class sections by enrollment-size bucket.

**PDF fields filled:**

```text
CLASS_2_9_N, CLASS_10_19_N, CLASS_20_29_N, CLASS_30_39_N,
CLASS_40_49_N, CLASS_50_99_N, CLASS_100_N, CLASS_TOT_N,
CLASS_SUB_2_9_N, CLASS_SUB_10_19_N, CLASS_SUB_20_29_N, CLASS_SUB_30_39_N,
CLASS_SUB_40_49_N, CLASS_SUB_50_99_N, CLASS_SUB_100_N, CLASS_SUB_TOT_N
```

**Source:** `production.silver.erss` does not contain class-section data. This requires a class-section enrollment table such as `production.silver.class_section` or equivalent. Confirm exact table name with analyst before running.

```sql
-- IMPORTANT: Replace 'production.silver.class_section' with the analyst-confirmed
-- class-section enrollment table. Exclude: distance-learning (INSTRUCTION_MODE='P' or 'W'),
-- independent study, dissertation/thesis sections, and internship sections.
-- Prior-year total: 5,405.9 sections (2,058 subsections).

WITH sections AS (
  SELECT
    CLASS_NBR,
    ENRL_TOT AS enrollment,
    CASE WHEN CLASS_TYPE = 'E' THEN 'SUBSECTION' ELSE 'SECTION' END AS section_type
  FROM production.silver.class_section  -- [CONFIRM TABLE NAME WITH ANALYST]
  WHERE STRM = ${fall_term}
    AND INSTRUCTION_MODE NOT IN ('P','W','OL','DE')   -- exclude distance learning
    AND CLASS_TYPE NOT IN ('N')                        -- exclude non-enrollment
    AND ASSOCIATED_CLASS NOT IN (9999)                 -- exclude independ study/thesis
    AND ENRL_TOT > 0
), bucketed AS (
  SELECT
    section_type,
    CASE
      WHEN enrollment BETWEEN 2  AND 9   THEN '2_9'
      WHEN enrollment BETWEEN 10 AND 19  THEN '10_19'
      WHEN enrollment BETWEEN 20 AND 29  THEN '20_29'
      WHEN enrollment BETWEEN 30 AND 39  THEN '30_39'
      WHEN enrollment BETWEEN 40 AND 49  THEN '40_49'
      WHEN enrollment BETWEEN 50 AND 99  THEN '50_99'
      WHEN enrollment >= 100             THEN '100'
    END AS bucket
  FROM sections
)
SELECT section_type, bucket, COUNT(*) AS count_n
FROM bucketed
WHERE bucket IS NOT NULL
GROUP BY section_type, bucket
ORDER BY section_type, bucket;
```

**Mapping rule:** Rows with `section_type='SECTION'` → `CLASS_*_N` fields; `section_type='SUBSECTION'` → `CLASS_SUB_*_N` fields. Sum all buckets for `CLASS_TOT_N` and `CLASS_SUB_TOT_N`.

---

## Q-C1-RACE: First-Year Applications/Admits/Enrollees by Race/Ethnicity

**CDS section:** C1 race/ethnicity breakdown, page 14.
**Purpose:** Fill race/ethnicity columns within the C1 applied/admitted/enrolled grid.

**PDF fields filled (applied/admitted/enrolled × 10 race categories):**

```text
AP_RECD_1ST_HISP_N, AP_ADMT_1ST_HISP_N, EN_TOT_1ST_HISP_N,
AP_RECD_1ST_BLACK_N, AP_ADMT_1ST_BLACK_N, EN_TOT_1ST_BLACK_N,
AP_RECD_1ST_WHITE_N, AP_ADMT_1ST_WHITE_N, EN_TOT_1ST_WHITE_N,
AP_RECD_1ST_NATIVE_N, AP_ADMT_1ST_NATIVE_N, EN_TOT_1ST_NATIVE_N,
AP_RECD_1ST_ASIAN_N, AP_ADMT_1ST_ASIAN_N, EN_TOT_1ST_ASIAN_N,
AP_RECD_1ST_ISLANDER_N, AP_ADMT_1ST_ISLANDER_N, EN_TOT_1ST_ISLANDER_N,
AP_RECD_1ST_MULTI_N, AP_ADMT_1ST_MULTI_N, EN_TOT_1ST_MULTI_N,
AP_RECD_1ST_NR_N, AP_ADMT_1ST_NR_N, EN_TOT_1ST_NR_N,
AP_RECD_1ST_UNKNOWN_N, AP_ADMT_1ST_UNKNOWN_N, EN_TOT_1ST_UNKNOWN_N
```

**SQL:**

```sql
WITH enrolled_emplids AS (
  SELECT DISTINCT EMPLID
  FROM production.silver.erss
  WHERE CAST(YEARS AS INT) * 10 + CAST(TERM AS INT) = ${fall_term}
    AND STUDENT_LEVEL_CODE < 5
    AND ENROLLMENT_STATUS = 5
),
apps AS (
  SELECT
    A.EMPLID,
    production.functions.ira_ethnicity(
      A.CITIZENSHIP_CODE, A.IPEDS_RACE_ETHNICITY_CATEGORY,
      A.ETHNIC_CODE_OLD,
      CAST(A.YEARS AS INT) * 10 + CAST(A.TERM AS INT)
    ) AS race_ethnicity,
    1 AS applied,
    CASE WHEN A.ADMISSION_STATUS IN ('A','C','F','H','N','P') THEN 1 ELSE 0 END AS admitted,
    CASE WHEN E.EMPLID IS NOT NULL THEN 1 ELSE 0 END AS enrolled
  FROM production.silver.ersa A
  LEFT JOIN enrolled_emplids E ON A.EMPLID = E.EMPLID
  WHERE CAST(A.YEARS AS INT) * 10 + CAST(A.TERM AS INT) = ${admission_fall_term}
    AND A.STUDENT_LEVEL_CODE < 5
    AND A.ENROLLMENT_STATUS = 5
    AND A.ACCOMMODATION_STATUS IN ('A','B','R')
)
SELECT
  race_ethnicity,
  SUM(applied)  AS applied_n,
  SUM(admitted) AS admitted_n,
  SUM(enrolled) AS enrolled_n
FROM apps
GROUP BY race_ethnicity
ORDER BY race_ethnicity;
```

**Mapping note:** Use the same `ira_ethnicity()` label-to-field mapping as Q-B2.

---

## Q-B22-RACE: Retention by Race/Ethnicity (supplemental)

**CDS section:** B22 supplemental race breakdown (if included in local form version).
**Purpose:** Fill retention numerator/denominator broken out by race/ethnicity — not always required but useful for QA.

**SQL:**

```sql
WITH cohort AS (
  SELECT DISTINCT
    EMPLID,
    production.functions.ira_ethnicity(
      CITIZENSHIP_CODE, IPEDS_RACE_ETHNICITY_CATEGORY, ETHNIC_CODE_OLD,
      CAST(YEARS AS INT) * 10 + CAST(TERM AS INT)
    ) AS race_ethnicity
  FROM production.silver.erss
  WHERE CAST(YEARS AS INT) * 10 + CAST(TERM AS INT) = ${prior_fall_term}
    AND STUDENT_LEVEL_CODE < 5
    AND ENROLLMENT_STATUS = 5
    AND (COALESCE(TUA_PRE_COLLEGIATE,0)+COALESCE(TUA_LOWER_DIVISION,0)+COALESCE(TUA_UPPER_DIVISION,0)+COALESCE(TUA_GRADUATE,0)) >= ${full_time_credit_threshold}
),
returned AS (
  SELECT DISTINCT EMPLID AS ret_emplid
  FROM production.silver.erss
  WHERE CAST(YEARS AS INT) * 10 + CAST(TERM AS INT) = ${fall_term}
    AND STUDENT_LEVEL_CODE < 5
)
SELECT
  C.race_ethnicity,
  COUNT(DISTINCT C.EMPLID)         AS cohort_n,
  COUNT(DISTINCT R.ret_emplid)     AS retained_n,
  ROUND(COUNT(DISTINCT R.ret_emplid) / NULLIF(COUNT(DISTINCT C.EMPLID),0) * 100, 2) AS retention_rate_p
FROM cohort C
LEFT JOIN returned R ON C.EMPLID = R.ret_emplid
GROUP BY C.race_ethnicity
ORDER BY C.race_ethnicity;
```

---

## Q-C11-FRSH-GPA-BINS: Freshman HS GPA Distribution (Full Field Mapping)

**CDS section:** C11, page 21.
**Purpose:** Explicit scalar PDF-field output for each GPA bin row (complement to Q-C11-C12-C13 which returns dimensional rows).

**PDF fields filled:**

| Bin | % of Submitters Field | % of All Enrolled Field |
|---|---|---|
| 4.0 or above | `FRSH_GPA_SUBMIT_1_P` | `EN_FRSH_GPA_1_P` |
| 3.75–3.99 | `FRSH_GPA_SUBMIT_2_P` | `EN_FRSH_GPA_2_P` |
| 3.50–3.74 | `FRSH_GPA_SUBMIT_3_P` | `EN_FRSH_GPA_3_P` |
| 3.25–3.49 | `FRSH_GPA_SUBMIT_4_P` | `EN_FRSH_GPA_4_P` |
| 3.00–3.24 | `FRSH_GPA_SUBMIT_5_P` | `EN_FRSH_GPA_5_P` |
| 2.50–2.99 | `FRSH_GPA_SUBMIT_6_P` | `EN_FRSH_GPA_6_P` |
| 2.00–2.49 | `FRSH_GPA_SUBMIT_7_P` | `EN_FRSH_GPA_7_P` |
| 1.00–1.99 | `FRSH_GPA_SUBMIT_8_P` | `EN_FRSH_GPA_8_P` |
| Below 1.0  | `FRSH_GPA_SUBMIT_9_P` | `EN_FRSH_GPA_9_P` |
| Totals     | `TOT_FRSH_GPA_SUBMIT_P` | `TOT_EN_FRSH_GPA_P` |

**SQL:** Uses the same logic as Q-C11-C12-C13; this query pivots to scalar `pdf_field` / `value` rows:

```sql
WITH fr AS (
  SELECT DISTINCT
    EMPLID,
    HS_GPA,
    CASE WHEN HS_GPA != 0 AND HS_GPA IS NOT NULL THEN 1 ELSE 0 END AS has_gpa
  FROM production.silver.erss
  WHERE CAST(YEARS AS INT) * 10 + CAST(TERM AS INT) = ${fall_term}
    AND STUDENT_LEVEL_CODE < 5
    AND ENROLLMENT_STATUS = 5
),
totals AS (
  SELECT
    COUNT(DISTINCT EMPLID)                                    AS total_enrolled,
    COUNT(DISTINCT CASE WHEN has_gpa=1 THEN EMPLID END)      AS total_submitted
  FROM fr
),
bins AS (
  SELECT
    CASE
      WHEN HS_GPA >= 400 THEN 1
      WHEN HS_GPA >= 375 THEN 2
      WHEN HS_GPA >= 350 THEN 3
      WHEN HS_GPA >= 325 THEN 4
      WHEN HS_GPA >= 300 THEN 5
      WHEN HS_GPA >= 250 THEN 6
      WHEN HS_GPA >= 200 THEN 7
      WHEN HS_GPA >= 100 THEN 8
      WHEN HS_GPA > 0   THEN 9
    END AS bin_num,
    EMPLID
  FROM fr WHERE has_gpa = 1
),
bin_counts AS (
  SELECT bin_num, COUNT(DISTINCT EMPLID) AS n FROM bins GROUP BY bin_num
)
SELECT CONCAT('FRSH_GPA_SUBMIT_', CAST(b.bin_num AS STRING), '_P') AS pdf_field,
       CAST(ROUND(b.n / NULLIF(t.total_submitted, 0) * 100, 2) AS STRING) AS value
FROM bin_counts b CROSS JOIN totals t
UNION ALL
SELECT CONCAT('EN_FRSH_GPA_', CAST(b.bin_num AS STRING), '_P'),
       CAST(ROUND(b.n / NULLIF(t.total_enrolled, 0) * 100, 2) AS STRING)
FROM bin_counts b CROSS JOIN totals t
UNION ALL
SELECT 'TOT_FRSH_GPA_SUBMIT_P',
       CAST(ROUND(total_submitted / NULLIF(total_enrolled, 0) * 100, 2) AS STRING)
FROM totals
UNION ALL
SELECT 'TOT_EN_FRSH_GPA_P', '100.00'
FROM totals
UNION ALL
SELECT 'FRSH_AVG_HSGPA',
       CAST(ROUND(AVG(CASE WHEN has_gpa=1 THEN HS_GPA/100.0 END), 3) AS STRING)
FROM fr CROSS JOIN totals
ORDER BY pdf_field;
```

---

## Q-UG-ENROLL-RACE: Undergraduate Enrollment by Race (UG_ fields, Total UG)

**CDS section:** B2 total UG column + any standalone UG_ fields outside B1/B2.
**Purpose:** Fill UG_ prefixed race/ethnicity fields for total undergraduate enrollment.

> Note: Q-B2 already covers the first-time and degree-seeking columns. Run this only for the total-UG column if it maps to distinct `UG_*` PDF fields.

**SQL:**

```sql
WITH base AS (
  SELECT DISTINCT
    EMPLID,
    production.functions.ira_ethnicity(
      CITIZENSHIP_CODE, IPEDS_RACE_ETHNICITY_CATEGORY, ETHNIC_CODE_OLD,
      CAST(YEARS AS INT) * 10 + CAST(TERM AS INT)
    ) AS race_ethnicity
  FROM production.silver.erss
  WHERE CAST(YEARS AS INT) * 10 + CAST(TERM AS INT) = ${fall_term}
    AND STUDENT_LEVEL_CODE IN ('1','2','3','4')
)
SELECT race_ethnicity, COUNT(DISTINCT EMPLID) AS total_ug_n
FROM base
GROUP BY race_ethnicity
UNION ALL
SELECT 'Total', COUNT(DISTINCT EMPLID)
FROM base
ORDER BY race_ethnicity;
```

**Mapping:** `race_ethnicity` label → `UG_` prefix field (e.g., `Hispanic/Latino` → `UG_HISPANIC_ETHNICITY_N`). See Q-B2 mapping table for label-to-suffix correspondence.

---

## Q-D2-RACE: Transfer Applicants/Admits/Enrollees by Race/Ethnicity

**CDS section:** D2 race breakdown (if present in local form).
**Purpose:** Supplement Q-D2 with race/ethnicity breakdown for transfer pipeline.

**SQL:**

```sql
WITH enrolled_tfer AS (
  SELECT DISTINCT EMPLID
  FROM production.silver.erss
  WHERE CAST(YEARS AS INT) * 10 + CAST(TERM AS INT) = ${fall_term}
    AND STUDENT_LEVEL_CODE < 5
    AND ENROLLMENT_STATUS = 4
),
apps AS (
  SELECT
    A.EMPLID,
    production.functions.ira_ethnicity(
      A.CITIZENSHIP_CODE, A.IPEDS_RACE_ETHNICITY_CATEGORY, A.ETHNIC_CODE_OLD,
      CAST(A.YEARS AS INT) * 10 + CAST(A.TERM AS INT)
    ) AS race_ethnicity,
    1 AS applied,
    CASE WHEN A.ADMISSION_STATUS IN ('A','C','F','H','N','P') THEN 1 ELSE 0 END AS admitted,
    CASE WHEN E.EMPLID IS NOT NULL THEN 1 ELSE 0 END AS enrolled
  FROM production.silver.ersa A
  LEFT JOIN enrolled_tfer E ON A.EMPLID = E.EMPLID
  WHERE CAST(A.YEARS AS INT) * 10 + CAST(A.TERM AS INT) = ${admission_fall_term}
    AND A.STUDENT_LEVEL_CODE < 5
    AND A.ENROLLMENT_STATUS = 4
    AND A.ACCOMMODATION_STATUS IN ('A','B','R')
)
SELECT
  race_ethnicity,
  SUM(applied)  AS applied_n,
  SUM(admitted) AS admitted_n,
  SUM(enrolled) AS enrolled_n
FROM apps
GROUP BY race_ethnicity
ORDER BY race_ethnicity;
```

---

## Q-B3-RACE: Degrees Awarded by Race/Ethnicity

**CDS section:** B3 supplemental (if local form includes race breakdown for completions).
**Purpose:** Fill degrees awarded by race within the B3 grid.

**SQL:**

```sql
WITH degree_race AS (
  SELECT DISTINCT
    T1.EMPLID,
    T1.DEGREE_LEVEL_CODE,
    production.functions.ira_ethnicity(
      T1.CITIZENSHIP_CODE, T1.IPEDS_RACE_ETHNICITY_CATEGORY,
      T1.ETHNIC_CODE_OLD,
      CAST(T1.YEARS AS INT) * 10 + CAST(T1.TERM AS INT)
    ) AS race_ethnicity
  FROM production.silver.ersd T1
  WHERE CAST(T1.YEARS AS INT) * 10 + CAST(T1.TERM AS INT) IN (${degree_award_terms_sql_list})
),
typed AS (
  SELECT
    race_ethnicity,
    CASE
      WHEN DEGREE_LEVEL_CODE IN ('3','4') THEN 'DEG_BACH_N'
      WHEN DEGREE_LEVEL_CODE IN ('6','7') THEN 'DEG_MASTER_N'
      WHEN DEGREE_LEVEL_CODE IN ('8','9') THEN 'DEG_DOCTOR_N'
      ELSE 'OTHER_DEGREE_N'
    END AS degree_type
  FROM degree_race
)
SELECT race_ethnicity, degree_type, COUNT(*) AS degree_count
FROM typed
GROUP BY race_ethnicity, degree_type
ORDER BY race_ethnicity, degree_type;
```

---

## Q-F1-AGE: Student Age Distribution (F1 supplemental fields)

**CDS section:** F1, page 28.
**Purpose:** Fill any F1 fields beyond what Q-F1 already covers — specifically age-bracket counts if the local form includes them.

**SQL:**

```sql
-- F1 already covered in Q-F1 for percentages and averages.
-- Use this for raw age-bracket headcounts if needed.
WITH base AS (
  SELECT DISTINCT
    EMPLID,
    ENROLLMENT_STATUS,
    DATEDIFF(TO_DATE(CONCAT(CAST(YEARS AS INT), '-10-15')), TO_DATE(BIRTH_DATE)) / 365.25 AS age_at_census
  FROM production.silver.erss
  WHERE CAST(YEARS AS INT) * 10 + CAST(TERM AS INT) = ${fall_term}
    AND STUDENT_LEVEL_CODE IN ('1','2','3','4')
)
SELECT
  CASE
    WHEN age_at_census < 18   THEN 'Under 18'
    WHEN age_at_census < 20   THEN '18-19'
    WHEN age_at_census < 22   THEN '20-21'
    WHEN age_at_census < 25   THEN '22-24'
    WHEN age_at_census < 30   THEN '25-29'
    WHEN age_at_census < 35   THEN '30-34'
    WHEN age_at_census < 40   THEN '35-39'
    WHEN age_at_census >= 40  THEN '40 and above'
  END AS age_bracket,
  SUM(CASE WHEN ENROLLMENT_STATUS = 5 THEN 1 ELSE 0 END) AS first_year_n,
  COUNT(*) AS all_ug_n
FROM base
GROUP BY age_bracket
ORDER BY age_bracket;
```

---

## Q-GRAD-ENROLL: Graduate Enrollment by Race/Ethnicity and Sex

**CDS section:** B1 graduate rows + B2 graduate column (if applicable).
**Purpose:** Fill graduate enrollment by race/ethnicity and sex.

**SQL:**

```sql
WITH grad_base AS (
  SELECT DISTINCT
    EMPLID,
    CASE
      WHEN GENDER_IDENTITY_CODE = '10' THEN 'MEN'
      WHEN GENDER_IDENTITY_CODE = '11' THEN 'WMN'
      ELSE 'UNK'
    END AS sex_bucket,
    CASE
      WHEN (COALESCE(TUA_PRE_COLLEGIATE,0)+COALESCE(TUA_LOWER_DIVISION,0)+COALESCE(TUA_UPPER_DIVISION,0)+COALESCE(TUA_GRADUATE,0)) >= 9
      THEN 'FT' ELSE 'PT'
    END AS load_bucket,
    production.functions.ira_ethnicity(
      CITIZENSHIP_CODE, IPEDS_RACE_ETHNICITY_CATEGORY, ETHNIC_CODE_OLD,
      CAST(YEARS AS INT) * 10 + CAST(TERM AS INT)
    ) AS race_ethnicity
  FROM production.silver.erss
  WHERE CAST(YEARS AS INT) * 10 + CAST(TERM AS INT) = ${fall_term}
    AND STUDENT_LEVEL_CODE IN ('5','6','7','8','9')
)
SELECT
  sex_bucket, load_bucket,
  COUNT(DISTINCT EMPLID) AS headcount
FROM grad_base
GROUP BY sex_bucket, load_bucket
UNION ALL
SELECT
  race_ethnicity AS sex_bucket, 'RACE' AS load_bucket,
  COUNT(DISTINCT EMPLID)
FROM grad_base
GROUP BY race_ethnicity
ORDER BY sex_bucket, load_bucket;
```

---

## Q-SAT-ACT: SAT/ACT Score Fields

**CDS section:** C9-C10, page 20.
**Purpose:** SAT and ACT score percentiles (25th/75th) and submission rates for enrolled first-year students.

> **[ROLL] — CSULB is test-optional and does not use SAT/ACT scores in admissions decisions.**
> These fields should be rolled from the prior CDS year after analyst confirmation that test-optional policy has not changed. Do not derive from ERS — test score data in ERSS is incomplete for optional submissions.
> If CSULB begins collecting and reporting test scores again, the source would be `production.silver.erss` columns `SAT_MATH`, `SAT_VERBAL`, `ACT_COMPOSITE` (confirm exact column names with analyst).

```sql
-- [ROLL] — Do not run. Roll forward prior-year values after analyst confirms test-optional policy unchanged.
-- Fields: SAT1_25_N, SAT1_75_N, SAT2_MATH_25_N, SAT2_MATH_75_N, SAT2_READ_25_N, SAT2_READ_75_N,
--         ACT_COMP_25_N, ACT_COMP_75_N, ACT_ENG_25_N, ACT_ENG_75_N,
--         ACT_MATH_25_N, ACT_MATH_75_N, ACT_READ_25_N, ACT_READ_75_N, ACT_SCI_25_N, ACT_SCI_75_N,
--         FRSH_SAT_SUBMIT_N, FRSH_SAT_SUBMIT_P, FRSH_ACT_SUBMIT_N, FRSH_ACT_SUBMIT_P
```

---

## Q-HOUS-EXT: Housing / Living Arrangement Fields

**CDS section:** F1 housing rows, pages 28-29.
**Purpose:** Percentage of students in college-owned housing, off-campus, commuter.

> **[EXT] — Owned by Housing Department.**
> Do not derive from ERS. Request from Housing Dept. or CSU Housing data systems.
> Fields: `HOUS_ON_CAM_P`, `HOUS_OFF_CAM_P`, `HOUS_COMMUT_P`, `HOUS_ON_CAM_N`, `HOUS_OFF_CAM_N`, `HOUS_COMMUT_N`,
> plus capacity fields `HOUS_CAP_FRESH_N`, `HOUS_CAP_TOT_N`, and any guarantee/requirement flags.

---

## Q-TUIT-EXT: Tuition, Fees, Room, Board, and Costs of Attendance

**CDS section:** G1, pages 29-30.
**Purpose:** Annual costs for current and upcoming academic year.

> **[EXT] — Owned by Financial Aid / Budget Office.**
> Do not derive from ERS or ERSA. Request the full G1 dataset from Financial Aid early (typically available May-June).
> Fields include: `TUIT_IN_STATE_D`, `TUIT_OUT_STATE_D`, `TUIT_INDEP_D`, `TUIT_INDEP_OOS_D`,
> `LIFE_ROOM_ONCAM_D`, `LIFE_BOARD_ONCAM_D`, `LIFE_ROOM_OFFCAM_D`, `LIFE_BOARD_OFFCAM_D`,
> `LIFE_TRANS_D`, `LIFE_BOOKS_D`, `LIFE_PERSONAL_D`,
> `TUIT_FULL_TIME_D`, `TUIT_PART_TIME_D`.

---

## Q-AID-EXT: Financial Aid Fields (H Section)

**CDS section:** H1–H15, pages 31-37.
**Purpose:** All financial-aid award amounts, percentages, and counts.

> **[EXT] — All H-section fields are owned by Financial Aid.**
> Do not derive from ERS. Request the full H-section dataset from Financial Aid.
> Key fields include:
> - `SCHOL_NEED_N`, `SCHOL_NEED_TOT_D`, `SCHOL_NEED_AVG_D` (need-based grants)
> - `SCHOL_NONNEED_N`, `SCHOL_NONNEED_TOT_D`, `SCHOL_NONNEED_AVG_D` (merit grants)
> - `LOAN_NEED_N`, `LOAN_NEED_TOT_D`, `LOAN_NEED_AVG_D` (need-based loans)
> - `LOAN_NONNEED_N`, `LOAN_NONNEED_TOT_D` (non-need loans)
> - `AID_FRSH_NEED_P`, `AID_FRSH_NONNEED_P` (% receiving aid)
> - `AID_AVG_PKG_D` (average financial-aid package)
> - H5 loan repayment fields, H6-H15 athletic/other aid

---

## Q-SCHOL-EXT: Scholarship and Grant Fields

**CDS section:** H2–H4, pages 32-33.

> **[EXT] — Owned by Financial Aid.**
> All `SCHOL_*` and `GRANT_*` PDF fields require Financial Aid data.
> Do not guess or derive from ERS enrollment tables.

---

## Q-LIFE-EXT: Living Cost Estimates

**CDS section:** G, pages 29-30.

> **[EXT] — Owned by Financial Aid / Budget Office.**
> All `LIFE_*` PDF fields (books, transportation, personal) require the official Cost of Attendance.
> Do not derive from ERS.

---

## Q-NRA-FACULTY-EXT: Nonresident Alien and Terminal Degree Faculty (I-1 E/F/G)

**CDS section:** I-1 rows E, F, G (nonresident aliens; terminal degree; masters non-terminal).
**Source:** `TYLERN.CDS_FACULTY_NRA_TM_F25_TBL` (personal schema, year-specific).

> **[EXT] — Requires Faculty Affairs-provided data.**
> Rows E (nonresident aliens), F (doctorate/terminal), and G (master's non-terminal) in I-1 depend on
> the `NRA_STATUS` and `TERMINAL_MASTERS` columns from a Faculty Affairs-supplied roster.
> This is already included in Q-I1 via the `${faculty_nra_tm_table}` parameter.
> If the table is unavailable, leave NRES_FT_N, NRES_PT_N, NRES_TOT_N, FT_DEG_TERM_N, PT_DEG_TERM_N,
> TOT_DEG_TERM_N, MASTER_FT_N, MASTER_PT_N, MASTER_TOT_N blank and log as PENDING_EXTERNAL.
> Each year requires a new table (e.g., `TYLERN.CDS_FACULTY_NRA_TM_F26_TBL` for 2026-2027).

---

# Missing or manual sections

The registry now covers most SQL-derivable sections. Remaining gaps are policy/external-owned:

- **A0-A6** — `[ROLL]`/`[NEW]`: respondent, institutional contact, control, calendar, degrees offered, campus belonging URL. Roll from prior year; confirm A6 URL with analyst.
- **C3-C10, C13-C22** — `[ROLL]`: admission policies, deadlines, fees, test policies (test-optional), early decision/action, waitlist (CSULB has no waitlist → `[N/A]`).
- **E1, E3** — `[ROLL]`: special study options and GE requirements. Confirm with Academic Affairs.
- **G** — `[EXT]`: annual costs. All `TUIT_*`, `LIFE_*` fields → see Q-TUIT-EXT and Q-LIFE-EXT.
- **H1-H15** — `[EXT]`: financial aid. All `SCHOL_*`, `LOAN_*`, `GRANT_*`, `AID_*` fields → see Q-AID-EXT and Q-SCHOL-EXT.
- **I-3 class section table** — `[SQL]` but table name unconfirmed. See Q-I3 comment.
- **F1 housing rows** — `[EXT]`: `HOUS_*` fields → see Q-HOUS-EXT.
- **I-1 rows E/F/G (NRA, terminal degree)** — requires Faculty Affairs data → see Q-NRA-FACULTY-EXT.
- **SAT/ACT score fields** — `[ROLL]`/`[N/A]`: CSULB test-optional → see Q-SAT-ACT.
- **B4-B11 Pell/Stafford flag** — requires `cds_fin_aid_status` view; IR coordinates with Financial Aid.
- **B1 non-degree credit-only students** — may need extra coding beyond the degree-seeking ERSS logic.

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
7. C1 race/ethnicity row from Q-C1-RACE should sum to C1 totals.
8. C11 GPA bin percents should sum to 100% of submitters; `TOT_FRSH_GPA_SUBMIT_P` should match C13.
9. D2 total transfer applicants/admits/enrollees should equal the sex-detail sums.
10. I-2 ratio must use the CDS formula; compare to prior-year baseline of 27:1 (flag if |delta| > 2).
11. I-3 section counts: sum of all buckets must equal CLASS_TOT_N; prior-year total was 5,405.9.
12. J totals by award bucket should equal 100% after rounding; use a final rounding adjustment if required.
13. Graduate enrollment from Q-GRAD-ENROLL FT+PT totals must reconcile to B1 graduate rows.

# Source coverage notes

- Existing Databricks notebook `CDS.html` contains SQL for B2 race/ethnicity, B3 degrees, B4-B11 graduation rates, C1 first-year admissions, D transfer admissions, F age/residency percentages, B22 retention, and historical J degree-by-CIP logic.
- Existing Databricks notebook `(Clone) CDS_J.html` contains a newer J-section workflow using `production.silver.ersd`, `production.silver.ersd_supplemental`, and a CIP/HEGIS crosswalk.
- The attached PDF contains the fillable field names and the CDS definitions/layout used for the mappings above.


# Playbook-specific instructions added in latest revision

The latest context file, `CDS_2025_2026_Playbook_v5.pdf`, clarifies that CSULB's official data source is ERS via Databricks SQL, not dashboards or direct IPEDS exports. It also clarifies that sections are a mix of `[SQL]`, `[ROLL]`, `[EXT]`, `[NEW]`, and `[N/A]`; therefore a fully automated PDF-fill agent must not try to SQL-fill every PDF field. The agent should produce three outputs: `(1)` a field-value table for SQL-fillable items, `(2)` a manual/rollover/external request list, and `(3)` a QA exception log for analyst sign-off.
