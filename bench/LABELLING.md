# Labelling guideline

The gold labels themselves are not in the repository. They reference the private job database and
contain ad text. This file records how they were made, so the benchmark numbers can be judged and
reproduced.

## Who labelled

Both gold sets were labelled by an LLM annotator (Claude) reading the full ad text and following the
rules below. No human reviewed them. Treat the numbers as indicative: a second annotator would change
some borderline calls, for example whether a skill named only in the task list counts as required.

## Extraction gold (50 ads)

The sample is stratified by source (round-robin over sources, fixed seed): BA Jobsuche, Arbeitnow,
Greenhouse, SmartRecruiters, Ashby and Personio. The extractor's output was not shown while labelling,
so the labels are not anchored to it.

- **seniority**
  - `intern`: Praktikum, Werkstudent, thesis, PreMaster.
  - `junior`: "Junior" in the title, or a PhD position.
  - `senior`: "Senior" in the title.
  - `lead`: Lead, Principal, Staff or Head in the title.
  - Otherwise from the required years: 0–1 → junior, 2–4 → mid, 5+ → senior. With no signal, `unknown`.
- **german_level / english_level**
  - C2: native or near-native.
  - C1: fluent, verhandlungssicher, sehr gut, proficient, "strong communication in English".
  - B2: gut or good; also a language that is required without a level being stated.
  - `none`: the ad calls the language a plus, or accepts "German *or* English".
  - `unknown`: the language is not mentioned at all, even when the ad is written in it.
- **remote**
  - `remote`: fully remote or unlimited home office.
  - `hybrid`: some home office or mobile work.
  - `onsite`: presence required or office-first.
  - Otherwise `unknown`.
- **salary**: only figures the ad states, with their period. Estimates are not labelled.
- **city**: the main work city, in German spelling. `null` when the ad only says "Germany".
- **years_experience_min**: the smallest number of years stated.
- **skills**: canonical names from `config/skills.yaml`.
  - Required: skills named in the requirements or in the task list, including "e.g." examples.
  - Nice to have: skills explicitly marked as a plus, ideally, von Vorteil or wünschenswert.
  - Generic phrases such as "AI", "APIs" or "databases" are not mapped to a specific skill.

## Retrieval relevance (40 queries)

A job counts as relevant to a query when a job seeker typing that query would want to see it.

- **Role or technology.** The job's role or core technology must match the query.
- **City.** A city named in the query must match the job's city, or the job must be remote in Germany.
- **Seniority or job type.** A level or job type in the query must match: Werkstudent, Praktikum, Teilzeit, Senior, Teamlead.

Judgements cover the pooled top 10 of all six configurations, 3 chunkers × 2 models. Jobs outside the
pool count as not relevant.
